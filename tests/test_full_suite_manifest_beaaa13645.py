"""BEAAA-13645 full-suite execution manifest tests."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from api.full_suite_manifest import (
    FULL_SUITE_MANIFEST_VERSION,
    FullSuiteCase,
    active_registry_from_path,
    build_full_suite_manifest,
)
from api.scan_handlers import ScanRuntime
from scanners.live_probe_orchestrator import DimResult, ScanRequest, ScanResult


def _request(scan_id: str = "scan-13645") -> ScanRequest:
    return ScanRequest(
        scan_id=scan_id,
        tenant_id="tenant-1",
        agent_endpoint="https://example.customer.test/path-not-persisted",
        credential_ref="cred-1",
        corpus_snapshot_hash="sha256:" + ("1" * 64),
        scanner_version="0.5.0",
        corpus_version="1.3.0",
        seed=0,
        agent_id="agent-1",
    )


def _result(*dim_ids: str, scan_id: str = "scan-13645") -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        status="complete",
        scanner_version="0.5.0",
        corpus_version="1.3.0",
        rows_emitted=len(dim_ids),
        dim_results=tuple(
            DimResult(dim_id=dim_id, raw=4.0, spread=0.0, replicates=(4.0,) * 5)
            for dim_id in dim_ids
        ),
        incomplete=False,
        incomplete_reason=None,
    )


def _sign(manifest: dict) -> str:
    return "test-signature:" + manifest["scan_id"]


def test_manifest_schema_counts_and_signature_for_complete_run():
    manifest, findings = build_full_suite_manifest(
        scan_request=_request(),
        scan_result=_result("P1.1", "P2.1"),
        registry_cases=(
            FullSuiteCase("P1.1", "P1.1"),
            FullSuiteCase("P2.1", "P2.1"),
        ),
        signer=_sign,
        started_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        finished_at=datetime(2026, 7, 12, 0, 0, 1, tzinfo=timezone.utc),
        manifest_uri="scanner://full-suite-manifests/scan-13645.json",
    )

    assert findings == ()
    assert manifest["manifest_version"] == FULL_SUITE_MANIFEST_VERSION
    assert manifest["runner_version"] == "full-suite-runner/v0.1"
    assert manifest["target"]["agent_endpoint_host"] == "example.customer.test"
    assert manifest["manifest_uri"] == "scanner://full-suite-manifests/scan-13645.json"
    assert manifest["counts"] == {
        "expected": 2,
        "attempted": 2,
        "passed": 2,
        "failed": 0,
        "skipped": 0,
        "errored": 0,
    }
    assert manifest["incomplete"] is False
    assert manifest["signed_manifest"] == "test-signature:scan-13645"
    assert all(row["evidence_uri"] for row in manifest["cases"])


def test_missing_evidence_uri_for_attempted_case_forces_incomplete():
    manifest, findings = build_full_suite_manifest(
        scan_request=_request(),
        scan_result=_result("P1.1"),
        registry_cases=(FullSuiteCase("P1.1", "P1.1"),),
        signer=_sign,
        evidence_uri_resolver=lambda case_id, req, res: None,
    )

    assert "missing_evidence_uri:P1.1" in findings
    assert manifest["incomplete"] is True
    assert manifest["counts"]["errored"] == 1
    assert manifest["cases"][0]["outcome"] == "errored"


def test_skipped_required_case_requires_approved_reason():
    manifest, findings = build_full_suite_manifest(
        scan_request=_request(),
        scan_result=_result(),
        registry_cases=(
            FullSuiteCase(
                "P3.5",
                "P3.5",
                approved_skip_reasons=("approved_tool_harness_dependency",),
            ),
        ),
        signer=_sign,
        skip_reasons={"P3.5": "not-approved"},
    )

    assert "skip_reason_unapproved:P3.5:not-approved" in findings
    assert manifest["counts"]["skipped"] == 1
    assert manifest["counts"]["errored"] == 1


def test_runner_count_mismatch_for_missing_required_case_forces_incomplete():
    manifest, findings = build_full_suite_manifest(
        scan_request=_request(),
        scan_result=_result("P1.1"),
        registry_cases=(
            FullSuiteCase("P1.1", "P1.1"),
            FullSuiteCase("P2.1", "P2.1"),
        ),
        signer=_sign,
    )

    assert "required_case_not_attempted:P2.1" in findings
    assert "runner_count_mismatch:required_cases_not_clean:P2.1" in findings
    assert manifest["incomplete"] is True
    assert manifest["counts"]["expected"] == 2
    assert manifest["counts"]["attempted"] == 1


def test_scan_runtime_attaches_signed_manifest_and_marks_incomplete(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-13645-test-key")

    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        return _result("P1.1", scan_id=scan_request.scan_id)

    runtime = ScanRuntime(
        run_scan_fn=run_scan_fn,
        full_suite_registry_cases=(
            FullSuiteCase("P1.1", "P1.1"),
            FullSuiteCase("P2.1", "P2.1"),
        ),
        full_suite_manifest_uri_factory=lambda agent_id, scan_id: (
            f"scanner://full-suite-manifests/{agent_id}/{scan_id}.json"
        ),
    )

    result = runtime.kickoff(_request(), "secret", agent_id="agent-1")

    assert result.incomplete is True
    assert result.incomplete_reason == "full_suite_manifest_incomplete"
    assert result.status == "incomplete"
    assert result.full_suite_manifest is not None
    assert result.full_suite_manifest["manifest_uri"] == (
        "scanner://full-suite-manifests/agent-1/scan-13645.json"
    )
    assert result.full_suite_manifest["signed_manifest"].startswith("hmac-sha256:")


def test_active_registry_loader_marks_all_policy_required_points_required():
    cases = active_registry_from_path()
    registry = json.loads(Path("tools/probe-registry/active_registry.json").read_text(encoding="utf-8"))

    assert len(cases) == len(registry["active_rubric_points"])
    assert all(case.required for case in cases)
    assert {case.case_id for case in cases} >= {"P1.1", "P2.1", "P3.1", "P4.1"}
    assert "ROBUSTNESS.LOOP_STABILITY.BUDGET_GUARD" not in {case.case_id for case in cases}


def test_full_underwriting_runtime_fails_closed_against_active_registry(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-13679-test-key")

    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        del secret
        # Current BEAAA-13678 manifest has four runnable rows. Full coverage
        # still requires every active registry point, so this must fail closed.
        return _result("P1.1", "P2.1", "P3.1", "P4.1", scan_id=scan_request.scan_id)

    runtime = ScanRuntime(
        run_scan_fn=run_scan_fn,
        full_suite_registry_cases=active_registry_from_path(),
    )

    result = runtime.kickoff(
        _request(scan_id="scan-13679-full"),
        "secret",
        agent_id="agent-1",
        scanner_invocation_context={"purpose": "pre_bind"},
    )

    assert result.status == "incomplete"
    assert result.incomplete is True
    assert result.incomplete_reason == "full_suite_manifest_incomplete"
    manifest = result.full_suite_manifest
    assert manifest is not None
    assert manifest["counts"]["expected"] == len(active_registry_from_path())
    assert manifest["counts"]["attempted"] == 4
    assert manifest["counts"]["errored"] == len(active_registry_from_path()) - 4
    assert "required_case_not_attempted:P1.2" in manifest["incomplete_reasons"]
    missing = [row for row in manifest["cases"] if row["case_id"] == "P1.2"][0]
    assert missing["outcome"] == "errored"
    assert missing["evidence_uri"] == "scanner://evidence/scan-13679-full/P1.2.json"


def test_smoke_tier_does_not_enforce_underwriting_full_suite(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-13679-test-key")

    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        del secret
        return _result("P1.1", scan_id=scan_request.scan_id)

    runtime = ScanRuntime(
        run_scan_fn=run_scan_fn,
        full_suite_registry_cases=active_registry_from_path(),
    )
    request = _request(scan_id="scan-13679-smoke")
    request = ScanRequest(
        scan_id=request.scan_id,
        tenant_id=request.tenant_id,
        agent_endpoint=request.agent_endpoint,
        credential_ref=request.credential_ref,
        corpus_snapshot_hash=request.corpus_snapshot_hash,
        scanner_version=request.scanner_version,
        corpus_version=request.corpus_version,
        seed=request.seed,
        agent_id=request.agent_id,
        scan_tier="smoke",
    )

    result = runtime.kickoff(
        request,
        "secret",
        agent_id="agent-1",
        scanner_invocation_context={"purpose": "pre_bind"},
    )

    assert result.status == "complete"
    assert result.incomplete is False
    assert result.full_suite_manifest is None
