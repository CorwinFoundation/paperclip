"""BEAAA-42359: `scanner-deploy qa-tenant-keys beaaa38869` renews idempotently.

The earlier issue-qa-tenants script skipped any tenant already in the tokens
file, so the seven-day keys issued on 2026-10-07 could never be replaced after
they expired. These tests run the real helper against a fake ``docker exec``
that executes the container program on the host against a temporary key store.
"""
from __future__ import annotations

import getpass
import os
import stat
import subprocess
import time
from pathlib import Path

from api.edge_gateway import KeyStore

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "deploy" / "scanner-deploy.sh"
WRAPPER = ROOT / "scripts" / "deploy" / "issue-qa-tenants-beaaa38869.sh"
TENANTS = ("qa-38869-a", "qa-38869-b")
SEVEN_DAYS = 604800

FAKE_DOCKER = """#!/usr/bin/env bash
[ "$1" = exec ] || exit 99
shift
[ "$1" = -i ] && shift
shift
if [ "$1" = python ]; then shift; exec python3 "$@"; fi
exec "$@"
"""


def _setup(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    docker = tmp_path / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    store = tmp_path / "keys.json"
    tokens = tmp_path / "home" / "qa-38869.tokens"
    tokens.parent.mkdir()
    env = {
        **os.environ,
        "SCANNER_DOCKER": str(docker),
        "ARE_KEY_STORE_PATH": str(store),
        "PYTHONPATH": str(ROOT),
        "QA_TOKENS_FILE": str(tokens),
        "QA_TOKENS_OWNER": getpass.getuser(),
    }
    return env, store, tokens


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(HELPER), "qa-tenant-keys", *args],
        env=env, capture_output=True, text=True, timeout=60,
    )


def _tokens(path: Path) -> dict[str, str]:
    return dict(line.split() for line in path.read_text().splitlines())


def _assert_no_token_output(proc: subprocess.CompletedProcess[str], *tokens: str) -> None:
    for token in tokens:
        assert token not in proc.stdout
        assert token not in proc.stderr
    assert "bbqa_" not in proc.stdout + proc.stderr


def test_first_run_issues_two_live_seven_day_keys_without_printing_them(tmp_path):
    env, store, tokens = _setup(tmp_path)
    before = time.time()
    proc = _run(env, "beaaa38869")
    assert proc.returncode == 0, proc.stderr
    issued = _tokens(tokens)
    assert sorted(issued) == list(TENANTS)
    assert stat.S_IMODE(tokens.stat().st_mode) == 0o600
    ks = KeyStore(store)
    for tenant, token in issued.items():
        assert token.startswith("bbqa_") and len(token) == 53
        rec = ks.resolve(token)
        assert rec is not None and rec.tenant_id == tenant
        assert rec.key_id.startswith(f"{tenant}-")
        assert rec.scopes is None  # same pre-scope access as the 2026-10-07 keys
        assert before + SEVEN_DAYS - 5 <= rec.expires_at <= time.time() + SEVEN_DAYS
        assert f"{tenant} renewed key_id=" in proc.stdout
    _assert_no_token_output(proc, *issued.values())


def test_rerun_keeps_live_keys_and_leaves_file_untouched(tmp_path):
    env, store, tokens = _setup(tmp_path)
    assert _run(env, "beaaa38869").returncode == 0
    first = tokens.read_bytes()
    records_before = len(KeyStore(store).list_records())
    proc = _run(env, "beaaa38869")
    assert proc.returncode == 0, proc.stderr
    assert tokens.read_bytes() == first
    assert len(KeyStore(store).list_records()) == records_before
    for tenant in TENANTS:
        assert f"{tenant} kept key_id=" in proc.stdout
    _assert_no_token_output(proc, *_tokens(tokens).values())


def test_expired_keys_listed_in_token_file_are_renewed(tmp_path):
    """The 2026-10-19 failure: both listed keys expired → old script skipped them."""
    env, store, tokens = _setup(tmp_path)
    clock = {"now": time.time() - 12 * 86400}
    old = KeyStore(store, clock=lambda: clock["now"])
    stale = {t: f"bbqa_{i:048x}" for i, t in enumerate(TENANTS)}
    for tenant, token in stale.items():
        old.issue(token=token, key_id=tenant, tenant_id=tenant, ttl_seconds=SEVEN_DAYS, scopes=None)
    tokens.write_text("".join(f"{t} {k}\n" for t, k in stale.items()))
    tokens.chmod(0o600)
    ks = KeyStore(store)
    assert all(ks.resolve(k) is None for k in stale.values())

    proc = _run(env, "beaaa38869")
    assert proc.returncode == 0, proc.stderr
    renewed = _tokens(tokens)
    ks.force_reload()
    for tenant in TENANTS:
        assert renewed[tenant] != stale[tenant]
        assert ks.resolve(renewed[tenant]).tenant_id == tenant
        assert ks.resolve(stale[tenant]) is None
    _assert_no_token_output(proc, *stale.values(), *renewed.values())


def test_near_expiry_key_is_renewed_and_old_key_revoked(tmp_path):
    env, store, tokens = _setup(tmp_path)
    ks = KeyStore(store)
    near = "bbqa_" + "a" * 48
    ks.issue(token=near, key_id="qa-38869-a", tenant_id="qa-38869-a", ttl_seconds=3600, scopes=None)
    tokens.write_text(f"qa-38869-a {near}\n")
    tokens.chmod(0o600)
    proc = _run(env, "beaaa38869")
    assert proc.returncode == 0, proc.stderr
    ks.force_reload()
    assert ks.resolve(near) is None
    assert [r.status for r in ks.list_records() if r.key_id == "qa-38869-a"] == ["revoked"]
    assert ks.resolve(_tokens(tokens)["qa-38869-a"]).tenant_id == "qa-38869-a"


def test_unknown_or_cross_tenant_token_is_replaced(tmp_path):
    env, store, tokens = _setup(tmp_path)
    ks = KeyStore(store)
    swapped = "bbqa_" + "b" * 48
    ks.issue(token=swapped, key_id="other", tenant_id="qa-38869-b", ttl_seconds=SEVEN_DAYS, scopes=None)
    # tenant a's line points at tenant b's key; tenant b's line is unknown to the store
    tokens.write_text(f"qa-38869-a {swapped}\nqa-38869-b bbqa_{'c' * 48}\n")
    tokens.chmod(0o600)
    proc = _run(env, "beaaa38869")
    assert proc.returncode == 0, proc.stderr
    renewed = _tokens(tokens)
    ks.force_reload()
    assert ks.resolve(renewed["qa-38869-a"]).tenant_id == "qa-38869-a"
    assert ks.resolve(renewed["qa-38869-b"]).tenant_id == "qa-38869-b"
    assert "qa-38869-a renewed" in proc.stdout and "qa-38869-b renewed" in proc.stdout


def test_symlinked_token_file_is_refused_without_touching_target(tmp_path):
    env, store, tokens = _setup(tmp_path)
    target = tmp_path / "victim"
    target.write_text("do not touch\n")
    tokens.symlink_to(target)
    proc = _run(env, "beaaa38869")
    assert proc.returncode != 0
    assert "refusing token file" in proc.stderr
    assert target.read_text() == "do not touch\n"
    assert tokens.is_symlink()


def test_arguments_are_fixed(tmp_path):
    env, _, tokens = _setup(tmp_path)
    for args in ((), ("other",), ("beaaa38869", "extra")):
        proc = _run(env, *args)
        assert proc.returncode != 0
        assert "usage: scanner-deploy qa-tenant-keys beaaa38869" in proc.stderr
    assert not tokens.exists()


def test_missing_key_store_env_fails_closed(tmp_path):
    env, _, tokens = _setup(tmp_path)
    env.pop("ARE_KEY_STORE_PATH")
    proc = _run(env, "beaaa38869")
    assert proc.returncode != 0
    assert "ARE_KEY_STORE_PATH is not set" in proc.stderr
    assert not tokens.exists()


def test_wrapper_delegates_to_sudo_authorized_helper_and_no_longer_skips_listed_tenants():
    text = WRAPPER.read_text()
    assert 'grep -q "^$t "' not in text
    assert 'exec sudo -n "$HELPER" qa-tenant-keys beaaa38869' in text
    assert "/usr/local/sbin/scanner-deploy" in text
    helper = HELPER.read_text()
    assert "TTL = 604800" in helper
    assert "scanner-deploy qa-tenant-keys beaaa38869'" in helper
    assert subprocess.run(["bash", "-n", str(WRAPPER)]).returncode == 0
