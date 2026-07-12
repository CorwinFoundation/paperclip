"""BEAAA-13681 async full-scan executor tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from api.full_scan_executor import (
    ASYNC_FULL_SCAN_API_VERSION,
    EvidenceAttemptRow,
    FullScanJobStore,
    build_attempt_manifest,
)
from api.full_suite_manifest import FullSuiteCase


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
