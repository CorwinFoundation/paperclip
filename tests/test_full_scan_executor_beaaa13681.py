"""BEAAA-13681 async full-scan executor tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.bundle import BundleStore
from api.full_scan_executor import (
    ASYNC_FULL_SCAN_API_VERSION,
    ControlledFamilyRun,
    EvidenceAttemptRow,
    FullScanJobStore,
    WorkerAttemptResult,
    build_attempt_manifest,
    build_controlled_validation_report,
    dispatch_registry_attempt,
    registry_cases_from_rows,
    validate_full_scan_release_gate,
)
from api.scan_handlers import ScanRuntime
from api.full_suite_manifest import FullSuiteCase
from rubric.scoring import Rubric
from scanners.live_probe_orchestrator import DimResult, ScanRequest, ScanResult
from tools.framework_workers import IsolatedFrameworkWorker, build_default_framework_workers


REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 12, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def _cases() -> tuple[FullSuiteCase, ...]:
    return (
        FullSuiteCase("P1.1", "P1.1"),
        FullSuiteCase("P2.1", "P2.1"),
    )


def _row(
    rubric_point_id: str,
    *,
    evidence_ref: str = "scanner://evidence/job/P1.1.json",
    status: str = "complete",
    incomplete_reason: str | None = None,
) -> EvidenceAttemptRow:
    return EvidenceAttemptRow(
        probe_id=f"{rubric_point_id}.probe",
        rubric_point_id=rubric_point_id,
        status=status,
        framework="promptfoo",
        corpus_version="1.3.0",
        model_version="fixture-agent-v0",
        seed=0,
        evidence_ref=evidence_ref,
        incomplete_reason=incomplete_reason,
    )


def test_submit_lease_checkpoint_resume_without_duplicate_attempts(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-13681-test-key")
    clock = Clock()
    store = FullScanJobStore(clock=clock)
    first = store.submit(
        agent_id="agent-1",
        scanner_version="0.5.0",
        corpus_version="1.3.0",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=0,
        registry_cases=_cases(),
        idempotency_key="tenant-1:agent-1:full",
    )
    second = store.submit(
        agent_id="agent-1",
        scanner_version="0.5.0",
        corpus_version="1.3.0",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=0,
        registry_cases=_cases(),
        idempotency_key="tenant-1:agent-1:full",
    )
    assert second.job_id == first.job_id

    lease = store.lease(first.job_id, ttl_seconds=60)
    assert lease["leased"] is True
    duplicate_lease = store.lease(first.job_id, ttl_seconds=60)
    assert duplicate_lease["leased"] is False
    assert duplicate_lease["active_attempt_id"] == lease["attempt_id"]

    store.checkpoint(first.job_id, attempt_id=lease["attempt_id"], rows=[_row("P1.1")])
    # Replaying the same checkpoint is idempotent by rubric point + seed.
    store.checkpoint(first.job_id, attempt_id=lease["attempt_id"], rows=[_row("P1.1")])
    resume = store.resume(first.job_id)

    assert len(resume["attempts"]) == 1
    assert len(resume["evidence_rows"]) == 1
    assert [case["rubric_point_id"] for case in resume["pending_cases"]] == ["P2.1"]

    clock.advance(61)
    resumed_lease = store.lease(first.job_id, ttl_seconds=60)
    assert resumed_lease["leased"] is True
    assert resumed_lease["attempt_id"] != lease["attempt_id"]
    assert len(store.status(first.job_id).attempts) == 2


def test_final_manifest_hash_changes_when_evidence_ref_changes(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-13681-test-key")
    base = FullScanJobStore().submit(
        agent_id="agent-1",
        scanner_version="0.5.0",
        corpus_version="1.3.0",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=0,
        registry_cases=_cases(),
    )
    lease = FullScanJobStore()
    del lease

    manifest_a = build_attempt_manifest(
        replace(
            base,
            attempts=("attempt-1",),
            evidence_rows=(
                _row("P1.1", evidence_ref="scanner://evidence/a/P1.1.json"),
                _row("P2.1", evidence_ref="scanner://evidence/a/P2.1.json"),
            ),
        ),
        finished_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )
    manifest_b = build_attempt_manifest(
        replace(
            base,
            attempts=("attempt-1",),
            evidence_rows=(
                _row("P1.1", evidence_ref="scanner://evidence/a/P1.1.json"),
                _row("P2.1", evidence_ref="scanner://evidence/b/P2.1.json"),
            ),
        ),
        finished_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )

    assert manifest_a["api_version"] == ASYNC_FULL_SCAN_API_VERSION
    assert manifest_a["manifest_hash"].startswith("sha256:")
    assert manifest_a["signed_manifest"].startswith("hmac-sha256:")
    assert manifest_a["manifest_hash"] != manifest_b["manifest_hash"]


def test_finalize_blocks_bind_until_base_suite_complete_or_approved_blocker(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-13681-test-key")
    store = FullScanJobStore()
    job = store.submit(
        agent_id="agent-1",
        scanner_version="0.5.0",
        corpus_version="1.3.0",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=0,
        registry_cases=_cases(),
    )
    lease = store.lease(job.job_id)
    store.checkpoint(job.job_id, attempt_id=lease["attempt_id"], rows=[_row("P1.1")])
    finalized = store.finalize(job.job_id)

    assert finalized.status == "incomplete"
    assert finalized.bind_eligibility == "blocked_base_suite_incomplete"
    assert finalized.final_manifest is not None
    assert finalized.final_manifest["bind_eligibility"] == "blocked_base_suite_incomplete"
    assert "required_case_not_attempted:P2.1" in finalized.final_manifest["incomplete_reasons"]

    approved = store.submit(
        agent_id="agent-2",
        scanner_version="0.5.0",
        corpus_version="1.3.0",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=0,
        registry_cases=_cases(),
        approved_blocker_ref="BEAAA-13681-approved-blocker",
    )
    approved_lease = store.lease(approved.job_id)
    store.checkpoint(
        approved.job_id,
        attempt_id=approved_lease["attempt_id"],
        rows=[_row("P1.1")],
    )
    approved_final = store.finalize(approved.job_id)
    assert approved_final.bind_eligibility == "blocked_approved_exception"


def test_evidence_rows_require_status_framework_corpus_model_seed_and_reason(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-13681-test-key")
    store = FullScanJobStore()
    job = store.submit(
        agent_id="agent-1",
        scanner_version="0.5.0",
        corpus_version="1.3.0",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=0,
        registry_cases=_cases(),
    )
    lease = store.lease(job.job_id)

    incomplete = _row(
        "P1.1",
        evidence_ref=None,
        status="incomplete",
        incomplete_reason="worker_timeout",
    )
    updated = store.checkpoint(job.job_id, attempt_id=lease["attempt_id"], rows=[incomplete])
    assert updated.evidence_rows[0].framework == "promptfoo"
    assert updated.evidence_rows[0].corpus_version == "1.3.0"
    assert updated.evidence_rows[0].model_version == "fixture-agent-v0"
    assert updated.evidence_rows[0].seed == 0
    assert updated.evidence_rows[0].incomplete_reason == "worker_timeout"


def test_beaaa13741_ops_loop_stability_manifest_row_carries_required_pins(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-13681-test-key")
    store = FullScanJobStore()
    job = store.submit(
        agent_id="agent-1",
        scanner_version="1.2.0",
        corpus_version="registry-baseline-c6ffe26",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=0,
        registry_cases=(
            FullSuiteCase(
                "OPS.LOOP_STABILITY.BUDGET_GUARD",
                "OPS.LOOP_STABILITY.BUDGET_GUARD",
            ),
        ),
    )
    lease = store.lease(job.job_id)
    store.checkpoint(
        job.job_id,
        attempt_id=lease["attempt_id"],
        rows=[
            EvidenceAttemptRow(
                probe_id="loop_stability_budget_guard_v0_1",
                rubric_point_id="OPS.LOOP_STABILITY.BUDGET_GUARD",
                status="complete",
                framework="backbond_loop_stability_budget_guard@0.1",
                corpus_version="registry-baseline-c6ffe26",
                model_version="deterministic-no-llm-judge",
                seed=0,
                evidence_ref=(
                    "evidence://scan-13741/loop_stability_budget_guard/"
                    "loop_stability_budget_guard_v0_1.json"
                ),
            )
        ],
    )
    finalized = store.finalize(job.job_id)
    manifest = finalized.final_manifest
    assert manifest is not None

    assert finalized.status == "complete"
    assert manifest["scanner_version"] == "1.2.0"
    assert manifest["corpus_version"] == "registry-baseline-c6ffe26"
    assert manifest["model_versions"] == {"primary": "fixture-agent-v0", "judges": []}
    assert manifest["seed"] == 0
    assert manifest["counts"] == {
        "expected": 1,
        "evidence_rows": 1,
        "missing_required": 0,
    }
    row = manifest["evidence_rows"][0]
    assert row["probe_id"] == "loop_stability_budget_guard_v0_1"
    assert row["rubric_point_id"] == "OPS.LOOP_STABILITY.BUDGET_GUARD"
    assert row["framework"] == "backbond_loop_stability_budget_guard@0.1"
    assert row["corpus_version"] == "registry-baseline-c6ffe26"
    assert row["model_version"] == "deterministic-no-llm-judge"
    assert row["seed"] == 0
    assert row["evidence_ref"].endswith(
        "/loop_stability_budget_guard_v0_1.json"
    )


def test_registry_dispatch_runs_unique_cases_not_collapsed_rubric_points(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-13761-test-key")
    registry_rows = [
        {
            "test_id": "promptinject",
            "case_id": "promptinject.case_001",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.1",
            "runner": "promptfoo_wrapper",
        },
        {
            "test_id": "promptinject",
            "case_id": "promptinject.case_002",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.1",
            "runner": "promptfoo_wrapper",
        },
    ]
    store = FullScanJobStore()
    job = store.submit(
        agent_id="agent-1",
        scanner_version="0.5.0",
        corpus_version="registry-baseline-2026-07-13",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(registry_rows),
    )
    lease = store.lease(job.job_id)

    def promptfoo_worker(row, _job):
        return WorkerAttemptResult(
            status="complete",
            evidence_ref=f"evidence://scan/{row['test_id']}/{row['case_id']}.json",
        )

    dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=registry_rows,
        workers={"promptfoo": promptfoo_worker},
    )
    finalized = store.finalize(job.job_id)

    assert finalized.status == "complete"
    assert finalized.bind_eligibility == "eligible"
    assert finalized.final_manifest is not None
    assert finalized.final_manifest["counts"] == {
        "expected": 2,
        "evidence_rows": 2,
        "missing_required": 0,
    }
    assert {
        row["probe_id"] for row in finalized.final_manifest["evidence_rows"]
    } == {"promptinject.case_001", "promptinject.case_002"}


def test_registry_dispatch_worker_failure_blocks_completion(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-13761-test-key")
    registry_rows = [
        {
            "test_id": "garak",
            "case_id": "garak.case_001",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.2",
            "runner": "garak_wrapper",
        }
    ]
    store = FullScanJobStore()
    job = store.submit(
        agent_id="agent-1",
        scanner_version="0.5.0",
        corpus_version="registry-baseline-2026-07-13",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(registry_rows),
    )
    lease = store.lease(job.job_id)

    def broken_worker(_row, _job):
        raise RuntimeError("dependency missing")

    dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=registry_rows,
        workers={"garak": broken_worker},
    )
    finalized = store.finalize(job.job_id)

    assert finalized.status == "incomplete"
    assert finalized.bind_eligibility == "blocked_base_suite_incomplete"
    assert finalized.final_manifest is not None
    assert finalized.final_manifest["incomplete"] is True
    row = finalized.final_manifest["evidence_rows"][0]
    assert row["status"] == "incomplete"
    assert row["framework"].startswith("garak@")
    assert row["incomplete_reason"] == "worker_error:garak:RuntimeError"


def test_registry_dispatch_missing_worker_is_terminal_evidence_gap(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-13761-test-key")
    registry_rows = [
        {
            "test_id": "pyrit",
            "case_id": "pyrit.case_001",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.3",
            "runner": "bench_wrapper",
        }
    ]
    store = FullScanJobStore()
    job = store.submit(
        agent_id="agent-1",
        scanner_version="0.5.0",
        corpus_version="registry-baseline-2026-07-13",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(registry_rows),
    )
    lease = store.lease(job.job_id)

    dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=registry_rows,
        workers={},
    )
    finalized = store.finalize(job.job_id)

    assert finalized.status == "incomplete"
    assert finalized.final_manifest is not None
    row = finalized.final_manifest["evidence_rows"][0]
    assert row["status"] == "incomplete"
    assert row["incomplete_reason"] == "worker_unavailable:pyrit"


def test_beaaa13765_controlled_n5_family_band_report_passes_and_quarantines():
    report = build_controlled_validation_report(
        [
            ControlledFamilyRun(
                agent_id="known-good",
                family="prompt_injection",
                scores=(91, 91, 91, 91, 91),
                expected_bands=("A",),
            ),
            ControlledFamilyRun(
                agent_id="known-bad",
                family="prompt_injection",
                scores=(42, 42, 42, 42, 42),
                expected_bands=("D",),
                expected_failure=True,
            ),
            ControlledFamilyRun(
                agent_id="non-discriminating",
                family="citation_integrity",
                scores=(70, 70, 70, 70, 70),
                expected_bands=("A", "D"),
                disposition="quarantined",
            ),
        ]
    )

    assert report["contract_version"] == "are-scanner-api-v1@v1.0"
    assert report["n_replicates"] == 5
    assert report["passed"] is True
    assert report["quarantined"] == ["non-discriminating:citation_integrity"]
    assert [row["observed_bands"][0] for row in report["rows"]] == ["A", "D", "B"]


def test_beaaa13765_controlled_known_bad_must_land_in_failure_band():
    report = build_controlled_validation_report(
        [
            ControlledFamilyRun(
                agent_id="known-bad",
                family="tool_misuse",
                scores=(88, 88, 88, 88, 88),
                expected_bands=("A",),
                expected_failure=True,
            )
        ]
    )

    assert report["passed"] is False
    assert report["errors"] == [
        {
            "code": "known_bad_did_not_fail",
            "detail": "known-bad:tool_misuse known-bad bands ['A']",
        }
    ]


def test_beaaa13765_vanish_gate_reconciles_active_expected_terminal_evidence(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-13765-test-key")
    registry_rows = [
        {
            "test_id": "promptinject",
            "case_id": "promptinject.case_001",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.1",
            "runner": "promptfoo_wrapper",
            "version_pin": "promptinject@0.1.0",
        },
        {
            "test_id": "garak",
            "case_id": "garak.case_001",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.2",
            "runner": "garak_wrapper",
            "version_pin": "garak@0.10.0",
        },
    ]
    store = FullScanJobStore()
    job = store.submit(
        agent_id="agent-1",
        scanner_version="0.5.0",
        corpus_version="registry-baseline-2026-07-13",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(registry_rows),
    )
    lease = store.lease(job.job_id)

    dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=registry_rows,
        workers={
            "promptfoo": lambda row, _job: WorkerAttemptResult(
                status="complete",
                evidence_ref=f"evidence://scan/{row['test_id']}/{row['case_id']}.json",
            ),
            "garak": lambda _row, _job: WorkerAttemptResult(
                status="incomplete",
                evidence_ref=None,
                incomplete_reason="controlled_known_bad_failure",
            ),
        },
    )
    finalized = store.finalize(job.job_id)
    assert finalized.final_manifest is not None

    worker_manifest = {
        "workers": [
            {
                "worker_id": "promptfoo",
                "framework_version": "0.115.0",
                "release_blocker": False,
            },
            {
                "worker_id": "garak",
                "framework_version": "0.10.0",
                "release_blocker": False,
            },
        ]
    }
    report = validate_full_scan_release_gate(
        active_registry_rows=registry_rows,
        worker_manifest=worker_manifest,
        attempt_manifest=finalized.final_manifest,
        verified_registry_versions={
            "promptinject.case_001": "promptinject@0.1.0",
            "garak.case_001": "garak@0.10.0",
        },
        verified_worker_versions={"promptfoo": "0.115.0", "garak": "0.10.0"},
    )

    assert report["passed"] is True
    assert report["active_case_count"] == 2
    assert report["expected_case_count"] == 2
    assert report["terminal_case_count"] == 2
    assert report["evidenced_case_count"] == 2


def test_beaaa13765_vanish_gate_fails_absent_case_and_pin_drift(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-13765-test-key")
    active_registry_rows = [
        {
            "test_id": "promptinject",
            "case_id": "promptinject.case_001",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.1",
            "runner": "promptfoo_wrapper",
            "version_pin": "promptinject@0.2.0",
        },
        {
            "test_id": "garak",
            "case_id": "garak.case_001",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.2",
            "runner": "garak_wrapper",
            "version_pin": "garak@0.10.0",
        },
    ]
    attempt_manifest = {
        "expected_cases": [
            {"case_id": "promptinject.case_001", "rubric_point_id": "P3.1"},
        ],
        "evidence_rows": [
            {
                "probe_id": "promptinject.case_001",
                "rubric_point_id": "P3.1",
                "status": "complete",
                "evidence_ref": "evidence://scan/promptinject.case_001.json",
            }
        ],
    }
    worker_manifest = {
        "workers": [
            {
                "worker_id": "promptfoo",
                "framework_version": "0.116.0",
                "release_blocker": False,
            },
            {
                "worker_id": "garak",
                "framework_version": "0.10.0",
                "release_blocker": False,
            },
        ]
    }

    report = validate_full_scan_release_gate(
        active_registry_rows=active_registry_rows,
        worker_manifest=worker_manifest,
        attempt_manifest=attempt_manifest,
        verified_registry_versions={"promptinject.case_001": "promptinject@0.1.0"},
        verified_worker_versions={"promptfoo": "0.115.0"},
    )

    assert report["passed"] is False
    errors = {(error["code"], error["detail"]) for error in report["errors"]}
    assert ("active_case_missing_from_expected", "garak.case_001") in errors
    assert ("active_case_missing_from_attempted", "garak.case_001") in errors
    assert ("active_case_missing_from_terminal", "garak.case_001") in errors
    assert ("active_case_missing_from_evidenced", "garak.case_001") in errors
    assert (
        "registry_version_pin_mismatch",
        "promptinject.case_001 deployed=promptinject@0.2.0 verified=promptinject@0.1.0",
    ) in errors
    assert (
        "worker_version_pin_mismatch",
        "promptfoo deployed=0.116.0 verified=0.115.0",
    ) in errors


def _scan_body() -> dict:
    return {
        "tenant_id": "tenant-13763",
        "agent_id": "agent-13763",
        "agent_endpoint": {"host": "agent.customer.example.com"},
        "credential_ref": "phase1-probe.v1",
        "credential_secret": "scoped-token-abc",
        "corpus_snapshot_hash": "sha256:" + ("1" * 64),
        "scanner_version": "1.2.0",
        "corpus_version": "registry-baseline-beaaa13763",
        "seed": 0,
        "scan_mode": "live_probe",
        "k_replicates": 5,
        "requested_pillars": ["GOVERNANCE", "ROBUSTNESS"],
    }


def _four_prompt_walker_result(request: ScanRequest, _secret: str) -> ScanResult:
    return ScanResult(
        scan_id=request.scan_id,
        status="complete",
        scanner_version=request.scanner_version,
        corpus_version=request.corpus_version,
        rows_emitted=4,
        k_replicates=request.k_replicates,
        dim_results=(
            DimResult("P1.1", 3.0, 0.0, (3.0,) * request.k_replicates),
            DimResult("P2.1", 3.0, 0.0, (3.0,) * request.k_replicates),
            DimResult("P3.1", 3.0, 0.0, (3.0,) * request.k_replicates),
            DimResult("P4.1", 3.0, 0.0, (3.0,) * request.k_replicates),
        ),
    )


def test_scanner_v1_scan_dispatches_registry_rows_through_real_api_path(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-13763-test-key")
    monkeypatch.setattr(
        "api.scan_handlers._verify_live_egress_fail_closed",
        lambda **_kwargs: None,
    )
    registry_rows = [
        {
            "test_id": "promptinject",
            "case_id": "promptinject.case_001",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.1",
            "runner": "promptfoo_wrapper",
        },
        {
            "test_id": "promptinject",
            "case_id": "promptinject.case_002",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.1",
            "runner": "promptfoo_wrapper",
        },
        {
            "test_id": "promptinject",
            "case_id": "promptinject.inactive",
            "status": "inactive",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.1",
            "runner": "promptfoo_wrapper",
        },
    ]
    observed_cases: list[str] = []

    def promptfoo_worker(row, job):
        observed_cases.append(row["case_id"])
        return WorkerAttemptResult(
            status="complete",
            evidence_ref=f"evidence://{job.job_id}/{row['case_id']}.json",
            model_version="fixture-agent-v0",
        )

    runtime = ScanRuntime(
        run_scan_fn=_four_prompt_walker_result,
        full_scan_registry_rows=registry_rows,
        full_scan_workers={"promptfoo": promptfoo_worker},
    )
    app = create_app(
        rubric=Rubric.load(RUBRIC_PATH),
        bundles=BundleStore(),
        scan_runtime=runtime,
    )

    response = TestClient(app).post(
        "/scanner/v1/scans",
        json=_scan_body(),
        headers={"X-Tenant-Id": "tenant-13763"},
    )

    assert response.status_code == 202
    scan_id = response.json()["scan_id"]
    assert observed_cases == ["promptinject.case_001", "promptinject.case_002"]
    result = runtime.fetch(scan_id)
    assert result is not None
    assert result.status == "complete"
    assert result.full_suite_manifest is not None
    assert result.full_suite_manifest["signed_manifest"].startswith("hmac-sha256:")
    assert result.full_suite_manifest["counts"] == {
        "expected": 2,
        "evidence_rows": 2,
        "missing_required": 0,
    }


def test_scanner_v1_scan_blocks_full_status_when_registry_worker_missing(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-13763-test-key")
    monkeypatch.setattr(
        "api.scan_handlers._verify_live_egress_fail_closed",
        lambda **_kwargs: None,
    )
    registry_rows = [
        {
            "test_id": "pyrit",
            "case_id": "pyrit.case_001",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.3",
            "runner": "bench_wrapper",
        }
    ]
    runtime = ScanRuntime(
        run_scan_fn=_four_prompt_walker_result,
        full_scan_registry_rows=registry_rows,
        full_scan_workers={},
    )
    app = create_app(
        rubric=Rubric.load(RUBRIC_PATH),
        bundles=BundleStore(),
        scan_runtime=runtime,
    )

    response = TestClient(app).post(
        "/scanner/v1/scans",
        json=_scan_body(),
        headers={"X-Tenant-Id": "tenant-13763"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "partial"
    scan_id = payload["scan_id"]
    result = runtime.fetch(scan_id)
    assert result is not None
    assert result.status == "incomplete"
    assert result.incomplete_reason == "full_scan_registry_incomplete"
    assert result.full_suite_manifest is not None
    row = result.full_suite_manifest["evidence_rows"][0]
    assert row["status"] == "incomplete"
    assert row["incomplete_reason"] == "worker_unavailable:pyrit"


def test_default_framework_workers_cover_registry_worker_ids():
    workers = build_default_framework_workers(timeout_seconds=5)

    assert "garak" in workers
    assert "pyrit" in workers
    assert "promptfoo" in workers
    assert "governance_attestation" in workers
    assert all(isinstance(worker, IsolatedFrameworkWorker) for worker in workers.values())


def test_default_isolated_worker_dependency_gap_is_terminal_incomplete(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-13764-test-key")
    registry_rows = [
        {
            "test_id": "promptinject",
            "case_id": "promptinject.case_001",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.1",
            "runner": "promptfoo_wrapper",
            "evidence_uri_template": "evidence://{scan_id}/{test_id}/{case_id}.json",
        }
    ]
    store = FullScanJobStore()
    job = store.submit(
        agent_id="agent-1",
        scanner_version="0.5.0",
        corpus_version="registry-baseline-2026-07-13",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(registry_rows),
    )
    lease = store.lease(job.job_id)

    dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=registry_rows,
    )
    finalized = store.finalize(job.job_id)

    assert finalized.status == "incomplete"
    assert finalized.final_manifest is not None
    row = finalized.final_manifest["evidence_rows"][0]
    assert row["probe_id"] == "promptinject.case_001"
    assert row["framework"] == "promptfoo@0.115.0"
    assert row["status"] == "incomplete"
    assert row["model_version"] == "fixture-agent-v0"
    assert row["incomplete_reason"].startswith(
        (
            "worker_dependency_unavailable:promptfoo:",
            "worker_adapter_not_bound:promptfoo",
        )
    )
