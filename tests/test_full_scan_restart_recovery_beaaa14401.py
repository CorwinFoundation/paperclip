"""BEAAA-14401 (P1#5 fix 4) — accepted full-scan jobs are restart-safe.

Proves the defect fix: an accepted job owned by ``FullScanJobStore`` used to be
in-memory only, so a process restart erased every queued/running job. With a
durable persistence backend + startup recovery sweep, an accepted job survives
the restart and either resumes to a durable terminal record or reaches an
explicit terminal failure — it never disappears.

Failing-before/passing-after: on the pre-fix in-memory-only store a second
``FullScanJobStore`` instance (a "new process") had no knowledge of the job at
all (``job_not_found``); these tests assert the opposite.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from api.full_scan_executor import (
    DEFAULT_MAX_RECOVERY_ATTEMPTS,
    EvidenceAttemptRow,
    FullScanExecutorError,
    FullScanJobStore,
    RESTART_RECOVERY_INCOMPLETE_REASON,
    WorkerAttemptResult,
    dispatch_registry_attempt,
    registry_cases_from_rows,
)
from api.full_scan_job_persistence import FullScanJobPersistence


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 15, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def _registry_rows() -> list[dict]:
    return [
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


def _submit(store: FullScanJobStore, rows: list[dict]):
    return store.submit(
        agent_id="agent-14401",
        scanner_version="1.2.0",
        corpus_version="registry-baseline-2026-07-13",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(rows),
        idempotency_key="tenant-14401:scan-1:full",
    )


def _promptfoo_worker(row, job):
    return WorkerAttemptResult(
        status="complete",
        evidence_ref=f"evidence://{job.job_id}/{row['case_id']}.json",
        model_version="fixture-agent-v0",
    )


def test_queued_job_survives_restart_and_resumes_to_terminal(monkeypatch, tmp_path):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-14401-test-key")
    db = str(tmp_path / "full_scan_jobs.sqlite3")
    rows = _registry_rows()

    # ---- Process 1: accept a scan; it is queued (never leased). ----
    p1 = FullScanJobStore(clock=Clock(), persistence=FullScanJobPersistence(db))
    job = _submit(p1, rows)
    job_id = job.job_id
    assert p1.status(job_id).status == "queued"
    # Simulate abrupt process death: drop the store WITHOUT finalizing.
    del p1

    # ---- Process 2: new process opens the same durable store. ----
    p2 = FullScanJobStore(clock=Clock(), persistence=FullScanJobPersistence(db))
    # The accepted job never disappeared.
    assert p2.status(job_id).job_id == job_id
    recovery = p2.recover()
    # Cleanly-queued job needs no reset; it was already resumable.
    assert job_id not in recovery["failed"]

    # Resume it to a durable terminal record.
    lease = p2.lease(job_id)
    dispatch_registry_attempt(
        p2, job_id, attempt_id=lease["attempt_id"],
        registry_rows=rows, workers={"promptfoo": _promptfoo_worker},
    )
    finalized = p2.finalize(job_id, attempt_id=lease["attempt_id"])
    assert finalized.status == "complete"
    assert finalized.bind_eligibility == "eligible"

    # ---- Process 3: the terminal record is durable. ----
    p3 = FullScanJobStore(clock=Clock(), persistence=FullScanJobPersistence(db))
    reloaded = p3.status(job_id)
    assert reloaded.status == "complete"
    assert reloaded.final_manifest is not None
    assert reloaded.final_manifest_hash == finalized.final_manifest_hash


def test_running_job_stale_lease_is_recovered_and_resumes(monkeypatch, tmp_path):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-14401-test-key")
    db = str(tmp_path / "full_scan_jobs.sqlite3")
    rows = _registry_rows()

    # ---- Process 1: accept + lease (job is RUNNING) then die mid-flight. ----
    clock1 = Clock()
    p1 = FullScanJobStore(clock=clock1, persistence=FullScanJobPersistence(db))
    job = _submit(p1, rows)
    job_id = job.job_id
    lease = p1.lease(job_id, ttl_seconds=300)
    # One case checkpointed before the crash; one still pending.
    dispatch_registry_attempt(
        p1, job_id, attempt_id=lease["attempt_id"],
        registry_rows=rows[:1], workers={"promptfoo": _promptfoo_worker},
    )
    assert p1.status(job_id).status == "running"
    assert p1.status(job_id).active_attempt_id == lease["attempt_id"]
    del p1  # crash while running — the worker holding the lease is gone.

    # ---- Process 2: recovery clears the dead lease and re-queues. ----
    p2 = FullScanJobStore(clock=Clock(), persistence=FullScanJobPersistence(db))
    recovered = p2.status(job_id)
    assert recovered.status == "running"  # persisted mid-flight state survived
    assert recovered.active_attempt_id == lease["attempt_id"]
    result = p2.recover()
    assert job_id in result["resumed"]

    swept = p2.status(job_id)
    assert swept.status == "queued"
    assert swept.active_attempt_id is None
    assert swept.recovery_count == 1
    # The one completed case's evidence was NOT lost across the restart.
    assert len(swept.evidence_rows) == 1

    # A stale worker cannot checkpoint against the dead attempt after recovery.
    with pytest.raises(FullScanExecutorError, match="attempt_id is not active"):
        p2.checkpoint(
            job_id, attempt_id=lease["attempt_id"],
            rows=[EvidenceAttemptRow(
                probe_id="promptinject.case_002",
                rubric_point_id="P3.1",
                status="complete",
                framework="promptfoo@0.115.0",
                corpus_version="registry-baseline-2026-07-13",
                model_version="fixture-agent-v0",
                seed=0,
                evidence_ref="evidence://stale/case_002.json",
            )],
        )

    # Fresh lease resumes and completes the remaining case to a terminal record.
    new_lease = p2.lease(job_id)
    dispatch_registry_attempt(
        p2, job_id, attempt_id=new_lease["attempt_id"],
        registry_rows=rows, workers={"promptfoo": _promptfoo_worker},
    )
    finalized = p2.finalize(job_id, attempt_id=new_lease["attempt_id"])
    assert finalized.status == "complete"
    assert finalized.bind_eligibility == "eligible"


def test_recovery_budget_exhaustion_finalizes_explicit_terminal_failure(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-14401-test-key")
    db = str(tmp_path / "full_scan_jobs.sqlite3")
    rows = _registry_rows()

    store = FullScanJobStore(clock=Clock(), persistence=FullScanJobPersistence(db))
    job = _submit(store, rows)
    job_id = job.job_id

    # Simulate a poison job: it keeps ending each restart still running (never
    # completes). Each recovery re-leases then "crashes" running again.
    for _ in range(DEFAULT_MAX_RECOVERY_ATTEMPTS):
        store.lease(job_id)  # -> running, holds an attempt
        result = store.recover()
        assert job_id in result["resumed"]

    # The next recovery exceeds the budget -> explicit terminal failure.
    store.lease(job_id)
    final_sweep = store.recover()
    assert job_id in final_sweep["failed"]

    failed = store.status(job_id)
    assert failed.status == "failed"
    assert failed.final_manifest is not None
    assert failed.final_manifest["bind_eligibility"] == "blocked_base_suite_incomplete"

    # Terminal failure is durable across a further restart, and idempotent.
    reloaded = FullScanJobStore(
        clock=Clock(), persistence=FullScanJobPersistence(db)
    )
    assert reloaded.status(job_id).status == "failed"
    assert reloaded.recover()["failed"] == []  # terminal jobs are skipped


def test_finalize_stranded_marks_unresumable_job_terminal(monkeypatch, tmp_path):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-14401-test-key")
    db = str(tmp_path / "full_scan_jobs.sqlite3")
    rows = _registry_rows()
    store = FullScanJobStore(clock=Clock(), persistence=FullScanJobPersistence(db))
    job = _submit(store, rows)

    terminal = store.finalize_stranded(
        job.job_id, reason=RESTART_RECOVERY_INCOMPLETE_REASON
    )
    assert terminal.status == "failed"
    # A second call is a no-op (already terminal) — never double-finalizes.
    assert store.finalize_stranded(job.job_id, reason="anything").status == "failed"


def test_persistence_is_opt_in_default_store_is_pure_in_memory():
    # No persistence -> no durable state; a fresh store cannot see the job.
    store = FullScanJobStore()
    job = store.submit(
        agent_id="a",
        scanner_version="1.2.0",
        corpus_version="c",
        model_versions={"primary": "m", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(_registry_rows()),
    )
    assert store.status(job.job_id).job_id == job.job_id
    # recover() is a safe no-op on a store with no non-terminal-stale jobs.
    assert FullScanJobStore().recover() == {"resumed": [], "failed": []}
