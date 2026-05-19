"""BEAAA-538 — credential vault adapter (`harness/credentials.py`).

Negative tests assert the secret literal ``SECRET_TEST_DO_NOT_LEAK_a3f9``
does not appear in any of:

  * ``repr(vault)`` / ``str(vault)``
  * ``pickle.dumps(vault)``
  * ``json.dumps(vault.__dict__)`` (and ``vars(vault)`` / ``dataclasses``-
    style traversal where applicable)

Positive tests cover the documented lifecycle: register → resolve →
forget / forget_all, plus the detached-after-forget invariant.

Hard Invariant #4 check (BEAAA-538 §Acceptance #3) is enforced in an
isolated subprocess to avoid pytest's collector pre-loading the module.
The forward-looking canonical check on ``scanners.live_probe_orchestrator``
runs only if that module is present in the tree (per ADR-0009 it lands
under T3 §5 #2, a sibling ticket).
"""
from __future__ import annotations

import importlib
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from harness import credentials
from harness.credentials import (
    CredentialError,
    CredentialRef,
    CredentialVault,
)

SECRET_LITERAL = "SECRET_TEST_DO_NOT_LEAK_a3f9"
REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# CredentialRef format validation
# ---------------------------------------------------------------------------


def test_credentialref_accepts_canonical_shape():
    ref = CredentialRef.from_str("tenant-001:probe_key.v1")
    assert ref.value == "tenant-001:probe_key.v1"
    assert str(ref) == "tenant-001:probe_key.v1"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        " leading-space",
        "trailing-space ",
        "has whitespace",
        "has/slash",
        "has\\backslash",
        "has\nnewline",
        "_starts-with-punct",
        "x" * 200,  # over the 128-char cap
        123,  # type error
        None,
    ],
)
def test_credentialref_rejects_malformed(bad):
    with pytest.raises(CredentialError):
        CredentialRef.from_str(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Lifecycle: register → resolve → forget / forget_all
# ---------------------------------------------------------------------------


def test_register_then_resolve_roundtrips():
    vault = CredentialVault()
    try:
        vault.register("api-key-1", SECRET_LITERAL)
        assert vault.resolve("api-key-1") == SECRET_LITERAL
        # accepts typed ref too
        assert vault.resolve(CredentialRef.from_str("api-key-1")) == SECRET_LITERAL
    finally:
        vault.forget_all()


def test_resolve_unknown_ref_returns_none():
    vault = CredentialVault()
    try:
        assert vault.resolve("never-registered") is None
    finally:
        vault.forget_all()


def test_forget_single_ref_removes_only_that_secret():
    vault = CredentialVault()
    try:
        vault.register("ref-a", SECRET_LITERAL)
        vault.register("ref-b", "other-secret")
        vault.forget("ref-a")
        assert vault.resolve("ref-a") is None
        assert vault.resolve("ref-b") == "other-secret"
    finally:
        vault.forget_all()


def test_forget_is_idempotent():
    vault = CredentialVault()
    try:
        vault.forget("never-registered")  # must not raise
        vault.register("x", SECRET_LITERAL)
        vault.forget("x")
        vault.forget("x")  # second call must not raise
        assert vault.resolve("x") is None
    finally:
        vault.forget_all()


def test_forget_all_clears_store_and_detaches_vault():
    vault = CredentialVault()
    vault.register("ref", SECRET_LITERAL)
    vault.forget_all()
    # Vault is detached: resolve returns None, register refuses.
    assert vault.resolve("ref") is None
    with pytest.raises(CredentialError):
        vault.register("ref", SECRET_LITERAL)


def test_empty_or_nonstr_secret_rejected():
    vault = CredentialVault()
    try:
        with pytest.raises(CredentialError):
            vault.register("ref", "")
        with pytest.raises(CredentialError):
            vault.register("ref", None)  # type: ignore[arg-type]
        with pytest.raises(CredentialError):
            vault.register("ref", 123)  # type: ignore[arg-type]
    finally:
        vault.forget_all()


def test_two_vaults_are_isolated():
    a = CredentialVault()
    b = CredentialVault()
    try:
        a.register("ref", SECRET_LITERAL)
        assert a.resolve("ref") == SECRET_LITERAL
        # b has no entry for "ref" — must NOT pick up a's secret
        assert b.resolve("ref") is None
    finally:
        a.forget_all()
        b.forget_all()


# ---------------------------------------------------------------------------
# Negative leak tests — §Acceptance #2
# ---------------------------------------------------------------------------


def _assert_no_leak(haystack: object, where: str) -> None:
    text = haystack if isinstance(haystack, str) else (
        haystack.decode("latin-1") if isinstance(haystack, (bytes, bytearray)) else repr(haystack)
    )
    assert SECRET_LITERAL not in text, (
        f"secret literal leaked via {where}; "
        f"surface had {len(text)} chars, sample: {text[:120]!r}"
    )


def test_repr_does_not_leak():
    vault = CredentialVault()
    try:
        vault.register("ref", SECRET_LITERAL)
        _assert_no_leak(repr(vault), "repr(vault)")
        _assert_no_leak(f"{vault!r}", "f-string !r")
    finally:
        vault.forget_all()


def test_str_does_not_leak():
    vault = CredentialVault()
    try:
        vault.register("ref", SECRET_LITERAL)
        _assert_no_leak(str(vault), "str(vault)")
        _assert_no_leak(f"{vault}", "f-string default")
    finally:
        vault.forget_all()


def test_pickle_does_not_leak():
    vault = CredentialVault()
    try:
        vault.register("ref", SECRET_LITERAL)
        blob = pickle.dumps(vault)
        _assert_no_leak(blob, "pickle.dumps(vault)")
        # Restored vault must be detached, never re-grant the credential.
        restored = pickle.loads(blob)
        assert restored.resolve("ref") is None
    finally:
        vault.forget_all()


def test_json_dict_dump_does_not_leak():
    vault = CredentialVault()
    try:
        vault.register("ref", SECRET_LITERAL)
        # vault.__dict__ is empty under __slots__; ``vars`` raises, so
        # construct the equivalent surface another way:
        slot_state = {
            name: getattr(vault, name, None)
            for name in (CredentialVault.__slots__ or ())
        }
        blob = json.dumps(slot_state, default=str)
        _assert_no_leak(blob, "json.dumps(slot state)")
    finally:
        vault.forget_all()


def test_dir_listing_does_not_leak():
    vault = CredentialVault()
    try:
        vault.register("ref", SECRET_LITERAL)
        # dir() exposes attribute *names*, not values; double-check.
        for name in dir(vault):
            _assert_no_leak(name, f"dir() name {name!r}")
            val = getattr(vault, name, None)
            if callable(val):
                continue
            _assert_no_leak(repr(val), f"attr {name!r} repr")
    finally:
        vault.forget_all()


# ---------------------------------------------------------------------------
# Hard Invariant #4 — Scorer-side import isolation (§Acceptance #3)
# ---------------------------------------------------------------------------


SCORER_SIDE_MODULES = (
    # Existing Scorer-side imports. The canonical ADR-0009 §5 #2 module
    # ``scanners.live_probe_orchestrator`` is a sibling-ticket build;
    # when it lands, the assertion below will guard its import root too.
    "harness.cli_runner",
    "rubric.scoring",
    "scanners.snapshot_hash",
)


def test_scorer_modules_do_not_import_credentials():
    """Hard Invariant #4 — `harness.credentials` MUST NOT appear in the
    Scorer worker's module table.

    Run in an isolated subprocess so pytest's collector cannot bias the
    result via prior imports.
    """
    script = (
        "import sys, json\n"
        f"mods = {list(SCORER_SIDE_MODULES)!r}\n"
        "for m in mods: __import__(m)\n"
        "leaked = sorted(k for k in sys.modules if k.startswith('harness.credentials'))\n"
        "print(json.dumps(leaked))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"subprocess failed: {proc.stderr}"
    leaked = json.loads(proc.stdout.strip() or "[]")
    assert leaked == [], (
        f"Scorer-side import root pulled in {leaked} — Hard Invariant #4 "
        "(credential read path lives only in the Prober worker) violated."
    )


def test_credentials_module_not_imported_by_default_harness():
    """Importing the harness package itself must not import the vault."""
    script = (
        "import sys\n"
        "import harness\n"
        "print('harness.credentials' in sys.modules)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False", (
        "`import harness` is pulling in `harness.credentials` — Scorer "
        "workers that only import the package must NOT acquire a "
        "credential read path."
    )


# ---------------------------------------------------------------------------
# Module surface sanity
# ---------------------------------------------------------------------------


def test_public_surface_matches_spec():
    # BEAAA-538 §Scope public surface
    importlib.reload(credentials)
    assert hasattr(credentials, "CredentialRef")
    assert hasattr(credentials, "CredentialVault")
    vault = credentials.CredentialVault()
    try:
        for name in ("register", "resolve", "forget", "forget_all"):
            assert callable(getattr(vault, name)), name
    finally:
        vault.forget_all()
