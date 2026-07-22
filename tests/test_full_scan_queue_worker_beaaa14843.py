"""BEAAA-14843 DB-backed full-scan queue/worker lifecycle tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from api.full_scan_executor import (
    FullScanJobStore,
    WorkerAttemptResult,
    registry_cases_from_rows,
)
from api.full_scan_job_persistence import FullScanJobPersistence
from api.full_scan_worker import FullScanQueueWorker


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 16, tzinfo=timezone.utc)

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


def _worker(row, job):
    return WorkerAttemptResult(
        status="complete",
        evidence_ref=f"evidence://{job.job_id}/{row['case_id']}.json",
        model_version="fixture-agent-v0",
    )


def test_db_backed_worker_restart_resumes_without_duplicate_attempts(monkeypatch, tmp_path):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-14843-test-key")
    db = str(tmp_path / "full_scan_queue.sqlite3")
    rows = _registry_rows()
    clock1 = Clock()

    store1 = FullScanJobStore(clock=clock1, persistence=FullScanJobPersistence(db))
    job = store1.submit(
        agent_id="agent-14843",
        scanner_version="1.3.0",
        corpus_version="registry-baseline-2026-07-13",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(rows),
        idempotency_key="tenant-14843:scan-1:full",
        wall_clock_budget_seconds=14_400,
        rate_limit_per_minute=120,
    )

    first = FullScanQueueWorker(
        store1,
        registry_rows=rows,
        workers={"promptfoo": _worker},
        lease_ttl_seconds=300,
    ).run_once(max_cases=1)
    assert first.status == "interrupted"
    assert store1.status(job.job_id).status == "running"
    assert len(store1.status(job.job_id).evidence_rows) == 1
    del store1

    # New process: the durable store recovers the accepted running job.
    clock2 = Clock()
    store2 = FullScanJobStore(clock=clock2, persistence=FullScanJobPersistence(db))
    worker2 = FullScanQueueWorker(
        store2,
        registry_rows=rows,
        workers={"promptfoo": _worker},
        lease_ttl_seconds=300,
    )
    recovery = worker2.recover()
    assert recovery["resumed"] == [job.job_id]

    second = worker2.run_once()
    assert second.status == "finalized"
    assert second.processed_cases == 1
    finalized = store2.status(job.job_id)
    assert finalized.status == "complete"
    assert finalized.final_manifest is not None
    assert finalized.final_manifest["counts"] == {
        "expected": 2,
        "evidence_rows": 2,
        "missing_required": 0,
    }
    assert finalized.final_manifest["execution_budget"] == {
        "wall_clock_budget_seconds": 14_400,
        "rate_limit_per_minute": 120,
    }
    assert len(finalized.evidence_rows) == 2
    assert {row.probe_id for row in finalized.evidence_rows} == {
        "promptinject.case_001",
        "promptinject.case_002",
    }
    assert len(finalized.attempts) == 2


def test_lease_next_returns_none_until_job_is_durably_accepted(tmp_path):
    store = FullScanJobStore(persistence=FullScanJobPersistence(tmp_path / "queue.sqlite3"))
    assert store.lease_next() is None


def test_submit_rejects_invalid_budget_or_rate_limit():
    store = FullScanJobStore()
    with pytest.raises(Exception, match="wall_clock_budget_seconds must be positive"):
        store.submit(
            agent_id="agent-14843",
            scanner_version="1.3.0",
            corpus_version="registry-baseline-2026-07-13",
            model_versions={"primary": "fixture-agent-v0", "judges": []},
            seed=0,
            registry_cases=registry_cases_from_rows(_registry_rows()),
            wall_clock_budget_seconds=0,
        )
