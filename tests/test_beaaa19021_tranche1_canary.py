from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from api.full_scan_executor import FullScanJob
from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.tranche1_canary_runner import (
    API_CONTRACT,
    ALLOWLIST_SHA256,
    EXACT_SELECTION_SHA256,
    EXACT_TARGET_CALLS,
    PROMOTION_ISSUES,
    PROMOTION_LEASE_VERSION,
    TRUSTED_PROMOTION_ISSUERS,
    Tranche1CanaryError,
    select_exact_tranche,
    run_tranche1_canary,
)
from tools.tranche1_observational import ALLOWLIST

LEASE_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"k" * 32)
LEASE_PUBLIC_KEY = LEASE_PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)
KEY_ID = "backbond-production-authority-2026-07"
NOW = datetime(2026, 7, 29, 16, 0, tzinfo=timezone.utc)


def _lease(*, signer=LEASE_PRIVATE_KEY, **overrides):
    body = {
        "version": PROMOTION_LEASE_VERSION,
        "lease_id": "lease-7",
        "approval_ref": "approval-9",
        "key_id": KEY_ID,
        "environment": "production",
        "tenant_id": "tenant",
        "authorization_ref": "root-ref",
        "commit_sha": "a" * 40,
        "image_ref": "scanner@sha256:" + "b" * 64,
        "allowlist_sha256": ALLOWLIST_SHA256,
        "exact_selection_sha256": EXACT_SELECTION_SHA256,
        "target_call_ceiling": EXACT_TARGET_CALLS,
        "judge_call_ceiling": 0,
        "issues": PROMOTION_ISSUES,
        "not_before": "2026-07-29T15:55:00Z",
        "expires_at": "2026-07-29T16:05:00Z",
    }
    body.update(overrides)
    signature = signer.sign(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    )
    encoded = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return {**body, "signature": "ed25519:" + encoded}


def _approval(**overrides):
    body = {
        "version": "founder-production-authorization/v1",
        "status": "accepted",
        "authorizer_role": "Founder",
        "approval_ref": "approval-9",
        "key_id": KEY_ID,
        "environment": "production",
        "tenant_id": "tenant",
        "authorization_ref": "root-ref",
        "commit_sha": "a" * 40,
        "image_ref": "scanner@sha256:" + "b" * 64,
        "allowlist_sha256": ALLOWLIST_SHA256,
        "exact_selection_sha256": EXACT_SELECTION_SHA256,
        "target_call_ceiling": EXACT_TARGET_CALLS,
        "judge_call_ceiling": 0,
        "issues": PROMOTION_ISSUES,
    }
    body.update(overrides)
    signature = LEASE_PRIVATE_KEY.sign(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    )
    encoded = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return {**body, "signature": "ed25519:" + encoded}


@pytest.fixture(autouse=True)
def trusted_test_issuer(monkeypatch):
    monkeypatch.setitem(
        TRUSTED_PROMOTION_ISSUERS,
        KEY_ID,
        base64.b64encode(LEASE_PUBLIC_KEY).decode(),
    )


def _runtime(monkeypatch, *, commit="a" * 40, image=None, environment="production"):
    image = image or "scanner@sha256:" + "b" * 64
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", commit)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", commit)
    monkeypatch.setenv("BACKBOND_RUNTIME_IMAGE_REF", image)
    monkeypatch.setenv("BACKBOND_RUNTIME_ENVIRONMENT", environment)
    monkeypatch.setenv("BACKBOND_RUNTIME_TENANT_ID", "tenant")


def test_public_full_scan_path_overdispatches_and_defaults_capability_manifest_to_none():
    rows = build_registry()
    executable = [row for row in rows if row.get("production_runner_executable")]
    assert len(executable) > sum(ALLOWLIST.values())
    assert FullScanJob.__dataclass_fields__["capability_manifest"].default is None


def test_exact_selector_rejects_count_and_family_drift_before_target_call(tmp_path, monkeypatch):
    commit = "a" * 40
    _runtime(monkeypatch, image="image@sha256:" + "b" * 64)
    rows = build_registry()
    selected = select_exact_tranche(rows)
    assert len(selected) == 36
    assert {row["test_id"] for row in selected} == set(ALLOWLIST)

    called = 0

    def target(_prompt: str) -> str:
        nonlocal called
        called += 1
        return '{"actual_response_label":"resisted_no_disclosure"}'

    drifted = [dict(row) for row in rows]
    victim = next(row for row in drifted if row.get("test_id") in ALLOWLIST)
    victim["case_id"] = "drifted"
    with pytest.raises(Tranche1CanaryError):
        run_tranche1_canary(
            registry_rows=drifted,
            subject=MockRunner("target", target),
            evidence_root=Path("/tmp/not-written"),
            job_id="drift",
            tenant_id="tenant",
            authorization_ref="root-ref",
            commit_sha=commit,
            image_ref="image@sha256:" + "b" * 64,
            promotion_lease=_lease(image_ref="image@sha256:" + "b" * 64),
            founder_authorization=_approval(image_ref="image@sha256:" + "b" * 64),
            promotion_lease_ledger=tmp_path / "lease-ledger.sqlite3",
            now=NOW,
        )
    assert called == 0


@pytest.mark.parametrize(
    ("commit", "runtime", "authorized", "image_ref"),
    [
        ("a" * 39, "a" * 40, "a" * 40, "scanner@sha256:" + "b" * 64),
        ("A" * 40, "A" * 40, "A" * 40, "scanner@sha256:" + "b" * 64),
        ("c" * 40, "a" * 40, "c" * 40, "scanner@sha256:" + "b" * 64),
        ("c" * 40, "c" * 40, "a" * 40, "scanner@sha256:" + "b" * 64),
        ("a" * 40, "a" * 40, "a" * 40, "scanner:mutable-latest"),
        ("a" * 40, "a" * 40, "a" * 40, "scanner@sha256:" + "B" * 64),
        ("a" * 40, "a" * 40, "a" * 40, "sha256:" + "b" * 64),
    ],
)
def test_identity_drift_is_rejected_before_target_call(
    tmp_path, monkeypatch, commit, runtime, authorized, image_ref
):
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", runtime)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", authorized)
    monkeypatch.setenv("BACKBOND_RUNTIME_IMAGE_REF", image_ref)
    monkeypatch.setenv("BACKBOND_RUNTIME_ENVIRONMENT", "production")
    calls = 0

    def target(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return '{"actual_response_label":"resisted_no_disclosure"}'

    with pytest.raises(Tranche1CanaryError):
        run_tranche1_canary(
            registry_rows=build_registry(),
            subject=MockRunner("target", target),
            evidence_root=tmp_path,
            job_id="identity-drift",
            tenant_id="tenant",
            authorization_ref="root-ref",
            commit_sha=commit,
            image_ref=image_ref,
            promotion_lease=_lease(commit_sha=commit, image_ref=image_ref),
            founder_authorization=_approval(commit_sha=commit, image_ref=image_ref),
            promotion_lease_ledger=tmp_path / "lease-ledger.sqlite3",
            now=NOW,
        )
    assert calls == 0
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize(
    "lease_overrides",
    [
        {"signature": "hmac-sha256:" + "0" * 64},
        {"expires_at": "2026-07-29T16:00:00Z"},
        {"environment": "staging"},
        {"tenant_id": "other-tenant"},
        {"authorization_ref": "other-approval"},
        {"commit_sha": "c" * 40},
        {"image_ref": "scanner@sha256:" + "c" * 64},
        {"allowlist_sha256": "0" * 64},
        {"exact_selection_sha256": "0" * 64},
        {"target_call_ceiling": 181},
        {"judge_call_ceiling": 1},
        {"issues": ["BEAAA-19023"]},
    ],
)
def test_promotion_lease_drift_is_zero_call_zero_write(
    tmp_path, monkeypatch, lease_overrides
):
    _runtime(monkeypatch)
    calls = 0

    def target(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return '{"actual_response_label":"resisted_no_disclosure"}'

    lease = _lease(**lease_overrides)
    if "signature" in lease_overrides:
        lease["signature"] = lease_overrides["signature"]
    with pytest.raises(Tranche1CanaryError):
        run_tranche1_canary(
            registry_rows=build_registry(),
            subject=MockRunner("target", target),
            evidence_root=tmp_path,
            job_id="bad-lease",
            tenant_id="tenant",
            authorization_ref="root-ref",
            commit_sha="a" * 40,
            image_ref="scanner@sha256:" + "b" * 64,
            promotion_lease=lease,
            founder_authorization=_approval(),
            promotion_lease_ledger=tmp_path / "lease-ledger.sqlite3",
            now=NOW,
        )
    assert calls == 0
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize(
    "failure", ["absent_lease", "runtime_image", "runtime_environment", "runtime_tenant"]
)
def test_runtime_authority_absence_or_mismatch_is_zero_call_zero_write(
    tmp_path, monkeypatch, failure
):
    _runtime(monkeypatch)
    if failure == "runtime_image":
        monkeypatch.setenv(
            "BACKBOND_RUNTIME_IMAGE_REF", "scanner@sha256:" + "c" * 64
        )
    if failure == "runtime_environment":
        monkeypatch.setenv("BACKBOND_RUNTIME_ENVIRONMENT", "staging")
    if failure == "runtime_tenant":
        monkeypatch.setenv("BACKBOND_RUNTIME_TENANT_ID", "other-tenant")
    lease = {} if failure == "absent_lease" else _lease()
    calls = 0

    def target(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return '{"actual_response_label":"resisted_no_disclosure"}'

    with pytest.raises(Tranche1CanaryError):
        run_tranche1_canary(
            registry_rows=build_registry(),
            subject=MockRunner("target", target),
            evidence_root=tmp_path,
            job_id="runtime-authority-drift",
            tenant_id="tenant",
            authorization_ref="root-ref",
            commit_sha="a" * 40,
            image_ref="scanner@sha256:" + "b" * 64,
            promotion_lease=lease,
            founder_authorization=_approval(),
            promotion_lease_ledger=tmp_path / "lease-ledger.sqlite3",
            now=NOW,
        )
    assert calls == 0
    assert not any(tmp_path.iterdir())


def test_runner_executes_exact_budget_zero_judges_and_redacts_secret(tmp_path, monkeypatch):
    commit = "a" * 40
    _runtime(monkeypatch)
    monkeypatch.setenv("BACKBOND_RUNTIME_TENANT_ID", "tenant-a")
    secret = "never-persist-this-secret"

    report = run_tranche1_canary(
        registry_rows=build_registry(),
        subject=MockRunner(
            "target",
            lambda _prompt: '{"actual_response_label":"resisted_no_disclosure"}',
        ),
        evidence_root=tmp_path,
        job_id="canary",
        tenant_id="tenant-a",
        authorization_ref="root-custody:lease-7",
        commit_sha=commit,
        image_ref="scanner@sha256:" + "b" * 64,
        promotion_lease=_lease(
            tenant_id="tenant-a", authorization_ref="root-custody:lease-7"
        ),
        founder_authorization=_approval(
            tenant_id="tenant-a", authorization_ref="root-custody:lease-7"
        ),
        promotion_lease_ledger=tmp_path / "lease-ledger.sqlite3",
        now=NOW,
    )

    assert report["budget"]["target_calls"] == EXACT_TARGET_CALLS == 180
    assert report["budget"]["judge_calls"] == 0
    assert report["api_contract"] == API_CONTRACT == "are-scanner-api-v1@v1.0"
    assert set(report) == {
        "ticket",
        "job_id",
        "scanner_version",
        "api_contract",
        "envelope",
        "authorization_ref",
        "promotion_authority",
        "capability_manifest",
        "pins",
        "budget",
        "families",
        "zero_weight_invariants",
    }
    assert report["capability_manifest"] == {"has_tool_calling": True}
    assert report["authorization_ref"] == "root-custody:lease-7"
    assert report["zero_weight_invariants"]["absolute_delta"] == {
        "are_total": 0,
        "pillars": 0,
        "band": 0,
        "eligibility": 0,
        "pricing": 0,
        "premium": 0,
        "quote_policy_behavior": 0,
        "published_weighted_countable_totals": 0,
    }
    assert all(row["scoring_active"] for row in report["families"])
    assert all(not row["underwriting_active"] for row in report["families"])
    assert all(row["effective_underwriting_weight"] == 0.0 for row in report["families"])
    materialized = "\n".join(
        path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.json")
    )
    assert secret not in materialized
    manifest = json.loads((tmp_path / "canary" / "manifest.json").read_text())
    assert manifest["pins"]["commit"] == commit
    pinned_manifest = json.loads(json.dumps(manifest))
    # Wall-clock telemetry is intentionally measured, not reproducible pin input.
    pinned_manifest["budget"].pop("elapsed_seconds")
    canonical_manifest = json.dumps(
        pinned_manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    # The canary manifest embeds the tranche-1 evaluator pin, so this digest
    # moves whenever tools/registered_case_executor.py legitimately changes.
    # BEAAA-24310 integrates both the scored-floor dispatch and lane-C adapter;
    # budget (180 target calls, 0 judge calls), families, and zero-weight
    # invariants remain unchanged.
    # BEAAA-24612 adds the MCP applicability gate to the evaluator, moving the
    # pin f24c9d3c -> 872db672 and this digest ff252a09 -> 45353c45. BEAAA-26529
    # adds validated saved-evidence dispatch, moving the evaluator pin to
    # e33ae84f and this derived manifest digest to f915c3a2. The AIUC-1 direct
    # adapter then moves the evaluator pin to dbe225ba and this derived digest
    # to a4691ed8. Native VAPI transport binds the production subject to the
    # requested assistant identity, moving the evaluator pin to 00e0d4cb and
    # this derived digest to 40aa3926. The Day-1 supporting-family dispatch
    # moves the evaluator pin to 81e6556d and this derived digest to 886abe3d.
    # Binding the pii_attestation row changes this derived manifest digest.
    # BEAAA-29085 then routes report-only TAU calls through the existing Vapi
    # adapter. BEAAA-29793 converges applicability resolution on the shared
    # tri-state. Canary budget, families, and zero-weight invariants remain
    # unchanged. BEAAA-30946 gates tau_support_crm rows rather than raising
    # RuntimeError, moving the evaluator pin to 52398d4e and this digest to
    # c9536ff5. BEAAA-30954 retains lane-C responses on parse failure, touching
    # registered_case_executor.py and moving the evaluator pin to df105868 and
    # this digest to a542c1f4. BEAAA-31298 adds untested_coverage disclosure to
    # the grade render, moving this derived manifest digest to 02f7b4f6.
    # BEAAA-31493 preserves upstream HTTP failure reasons in the registered
    # executor, moving its evaluator pin and this digest to 2ce1423d.
    # BEAAA-31971 makes three grading paths terminal (fenced-JSON judge parse,
    # na_is_pass enforcement, structured-invariant exceptions) in
    # tools/registered_case_executor.py, moving the evaluator pin to 9888dd57
    # and this derived digest to 67cdcb8e. Canary budget (180 target calls, 0
    # judge calls), families, and the zero-weight invariants asserted above are
    # unchanged. BEAAA-30935 adds direct-Anthropic provider identity and usage
    # accounting; the canary still executes zero judge calls.
    # BEAAA-33228 threads observational_rejection through
    # tools/registered_case_executor.py so a terminal row that never
    # contacted the target states why, moving the evaluator pin to b25f87df
    # and this derived digest to d3698f02. Canary budget (180 target calls,
    # 0 judge calls), families, and zero-weight invariants are unchanged.
    # BEAAA-33101 then declares scoring_active explicitly on the probe mappings so
    # inverting the _scoring_active default to False is a measured no-op. That
    # changes mapping file bytes and so moves this derived digest again, to 3b960d41. Budget,
    # families and the zero-weight invariants remain unchanged, and no family
    # enters or leaves the scored set: 72 scoring-active before, 72 after.
    # BEAAA-30934 decides a clean refusal deterministically ahead of the
    # general-purpose judge in tools/registered_case_executor.py, moving the
    # evaluator pin to a4408a6d and this derived digest to 10e4d292. The canary
    # budget (180 target calls, 0 judge calls), the families, and the
    # zero-weight invariants asserted above are unchanged.
    # BEAAA-30934 then teaches the same evaluator to read the applicability gate
    # a family declares in its rubric mapping when it has no aiuc_path_b SPEC to
    # carry the field, which is how all three families pinned here declare
    # has_tool_calling. That moves the evaluator pin to 6b2bf670 and this derived
    # digest to 9b3f8066. The gate changes which rows are dispatched, never how
    # many calls a dispatched row costs, so the 180/0 budget and the zero-weight
    # invariants asserted above are again unchanged.
    # BEAAA-30934 work item 2 then splits the confirmation adapter's single
    # LaneCError into an absent-surface case and a contract-violation case, so a
    # row that answered can no longer terminate on an uncaught exception. That
    # moves the evaluator pin to b214b2a4 and this derived digest to ca508f30.
    # It changes how a dispatched row's outcome is classified, never how many
    # target or judge calls it makes, so the 180/0 budget and the zero-weight
    # invariants asserted above are unchanged once more.
    # BEAAA-30934 work item 1 then teaches the shared resolver the any-of gate
    # shape insecure_code_generation declares, so that family resolves
    # not_applicable at the gate instead of dying at the unbound-adapter raise.
    # That moves the evaluator pin to 7fca535a and this derived digest to
    # 17c32e6e. None of the three tranche1 families declares an any-of gate, so
    # the rows dispatched here are the same rows, costing the same calls: the
    # 180/0 budget and the zero-weight invariants asserted above are unchanged
    # for a fourth time, and the digest moves only because the evaluator pin it
    # embeds did.
    assert hashlib.sha256(canonical_manifest).hexdigest() == (
        "17c32e6eabd569c18bc5e8ac348899c13515f50b63b4db18bc1f7267985bdd51"
    )


def test_valid_lease_replay_is_zero_call_zero_evidence_write(tmp_path, monkeypatch):
    _runtime(monkeypatch)
    lease = _lease()
    ledger = tmp_path / "replay-ledger.sqlite3"
    calls = 0

    def target(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return '{"actual_response_label":"resisted_no_disclosure"}'

    common = dict(
        registry_rows=build_registry(),
        subject=MockRunner("target", target),
        tenant_id="tenant",
        authorization_ref="root-ref",
        commit_sha="a" * 40,
        image_ref="scanner@sha256:" + "b" * 64,
        promotion_lease=lease,
        founder_authorization=_approval(),
        promotion_lease_ledger=ledger,
        now=NOW,
    )
    run_tranche1_canary(
        **common, evidence_root=tmp_path / "first", job_id="first"
    )
    assert calls == EXACT_TARGET_CALLS
    with pytest.raises(Tranche1CanaryError, match="already been consumed"):
        run_tranche1_canary(
            **common, evidence_root=tmp_path / "replay", job_id="replay"
        )
    assert calls == EXACT_TARGET_CALLS
    assert not (tmp_path / "replay").exists()


def test_concurrent_replay_allows_exactly_one_execution(tmp_path, monkeypatch):
    _runtime(monkeypatch)
    lease = _lease()
    ledger = tmp_path / "concurrent-ledger.sqlite3"
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    calls = 0
    outcomes: list[str] = []

    def target(_prompt: str) -> str:
        nonlocal calls
        with lock:
            calls += 1
        return '{"actual_response_label":"resisted_no_disclosure"}'

    def attempt(index: int) -> None:
        barrier.wait()
        try:
            run_tranche1_canary(
                registry_rows=build_registry(),
                subject=MockRunner("target", target),
                evidence_root=tmp_path / f"run-{index}",
                job_id=f"concurrent-{index}",
                tenant_id="tenant",
                authorization_ref="root-ref",
                commit_sha="a" * 40,
                image_ref="scanner@sha256:" + "b" * 64,
                promotion_lease=lease,
                founder_authorization=_approval(),
                promotion_lease_ledger=ledger,
                now=NOW,
            )
        except Tranche1CanaryError as exc:
            outcomes.append(str(exc))
        else:
            outcomes.append("executed")

    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["executed", "promotion lease has already been consumed"]
    assert calls == EXACT_TARGET_CALLS
    assert sum(path.exists() for path in (tmp_path / "run-0", tmp_path / "run-1")) == 1


def test_ledger_failure_is_zero_call_zero_evidence_write(tmp_path, monkeypatch):
    _runtime(monkeypatch)
    ledger_directory = tmp_path / "ledger-is-a-directory"
    ledger_directory.mkdir()
    calls = 0

    def target(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return '{"actual_response_label":"resisted_no_disclosure"}'

    with pytest.raises(Tranche1CanaryError, match="ledger failed closed"):
        run_tranche1_canary(
            registry_rows=build_registry(),
            subject=MockRunner("target", target),
            evidence_root=tmp_path / "evidence",
            job_id="ledger-failure",
            tenant_id="tenant",
            authorization_ref="root-ref",
            commit_sha="a" * 40,
            image_ref="scanner@sha256:" + "b" * 64,
            promotion_lease=_lease(),
            founder_authorization=_approval(),
            promotion_lease_ledger=ledger_directory,
            now=NOW,
        )
    assert calls == 0
    assert not (tmp_path / "evidence").exists()


def test_locked_ledger_retries_are_bounded_and_fail_closed(tmp_path, monkeypatch):
    _runtime(monkeypatch)
    attempts = 0
    calls = 0

    def locked_connect(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise sqlite3.OperationalError("database is locked")

    def target(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return '{"actual_response_label":"resisted_no_disclosure"}'

    monkeypatch.setattr(
        "tools.tranche1_canary_runner.sqlite3.connect", locked_connect
    )
    monkeypatch.setattr("tools.tranche1_canary_runner.time.sleep", lambda _delay: None)

    with pytest.raises(Tranche1CanaryError, match="ledger failed closed"):
        run_tranche1_canary(
            registry_rows=build_registry(),
            subject=MockRunner("target", target),
            evidence_root=tmp_path / "evidence",
            job_id="locked-ledger",
            tenant_id="tenant",
            authorization_ref="root-ref",
            commit_sha="a" * 40,
            image_ref="scanner@sha256:" + "b" * 64,
            promotion_lease=_lease(),
            founder_authorization=_approval(),
            promotion_lease_ledger=tmp_path / "locked-ledger.sqlite3",
            now=NOW,
        )

    assert attempts == 5
    assert calls == 0
    assert not (tmp_path / "evidence").exists()


def test_forged_authority_key_is_zero_call_zero_write(tmp_path, monkeypatch):
    _runtime(monkeypatch)
    forged_private_key = Ed25519PrivateKey.generate()
    calls = 0

    def target(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return '{"actual_response_label":"resisted_no_disclosure"}'

    with pytest.raises(Tranche1CanaryError, match="signature is invalid"):
        run_tranche1_canary(
            registry_rows=build_registry(),
            subject=MockRunner("target", target),
            evidence_root=tmp_path / "evidence",
            job_id="forged-authority",
            tenant_id="tenant",
            authorization_ref="root-ref",
            commit_sha="a" * 40,
            image_ref="scanner@sha256:" + "b" * 64,
            promotion_lease=_lease(signer=forged_private_key),
            founder_authorization=_approval(),
            promotion_lease_ledger=tmp_path / "ledger.sqlite3",
            now=NOW,
        )
    assert calls == 0
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize(
    "approval",
    [
        _approval(approval_ref="fabricated"),
        _approval(status="pending"),
        _approval(commit_sha="c" * 40),
        _approval(tenant_id="other"),
        _approval(target_call_ceiling=360),
    ],
)
def test_unaccepted_or_misbound_founder_authorization_is_zero_call_zero_write(
    tmp_path, monkeypatch, approval
):
    _runtime(monkeypatch)
    calls = 0

    def target(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return '{"actual_response_label":"resisted_no_disclosure"}'

    with pytest.raises(Tranche1CanaryError, match="Founder authorization"):
        run_tranche1_canary(
            registry_rows=build_registry(),
            subject=MockRunner("target", target),
            evidence_root=tmp_path / "evidence",
            job_id="bad-founder-approval",
            tenant_id="tenant",
            authorization_ref="root-ref",
            commit_sha="a" * 40,
            image_ref="scanner@sha256:" + "b" * 64,
            promotion_lease=_lease(),
            founder_authorization=approval,
            promotion_lease_ledger=tmp_path / "ledger.sqlite3",
            now=NOW,
        )
    assert calls == 0
    assert not any(tmp_path.iterdir())


def test_unknown_or_revoked_issuer_is_zero_call_zero_write(tmp_path, monkeypatch):
    _runtime(monkeypatch)
    lease = _lease(key_id="revoked-key")
    calls = 0

    with pytest.raises(Tranche1CanaryError, match="unknown or revoked"):
        run_tranche1_canary(
            registry_rows=build_registry(),
            subject=MockRunner("target", lambda _prompt: calls + 1),
            evidence_root=tmp_path / "evidence",
            job_id="revoked-key",
            tenant_id="tenant",
            authorization_ref="root-ref",
            commit_sha="a" * 40,
            image_ref="scanner@sha256:" + "b" * 64,
            promotion_lease=lease,
            founder_authorization=_approval(key_id="revoked-key"),
            promotion_lease_ledger=tmp_path / "ledger.sqlite3",
            now=NOW,
        )
    assert calls == 0
    assert not any(tmp_path.iterdir())


def test_partial_execution_consumes_entire_lease_and_replay_adds_no_calls(
    tmp_path, monkeypatch
):
    _runtime(monkeypatch)
    ledger = tmp_path / "ledger.sqlite3"
    calls = 0

    def target(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise RuntimeError("transport failed")
        return '{"actual_response_label":"resisted_no_disclosure"}'

    common = dict(
        registry_rows=build_registry(),
        subject=MockRunner("target", target),
        tenant_id="tenant",
        authorization_ref="root-ref",
        commit_sha="a" * 40,
        image_ref="scanner@sha256:" + "b" * 64,
        promotion_lease=_lease(),
        founder_authorization=_approval(),
        promotion_lease_ledger=ledger,
        now=NOW,
    )
    with pytest.raises(Tranche1CanaryError, match="case failed"):
        run_tranche1_canary(
            **common, evidence_root=tmp_path / "partial", job_id="partial"
        )
    first_attempt_calls = calls
    assert 0 < first_attempt_calls <= EXACT_TARGET_CALLS
    with pytest.raises(Tranche1CanaryError, match="already been consumed"):
        run_tranche1_canary(
            **common, evidence_root=tmp_path / "replay", job_id="replay"
        )
    assert calls == first_attempt_calls
