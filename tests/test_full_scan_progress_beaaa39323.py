from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.bundle import BundleStore
from api.full_scan_executor import (
    EvidenceAttemptRow,
    FullScanJobStore,
    FullSuiteCase,
)
from api.full_scan_job_persistence import FullScanJobPersistence
from api.scan_handlers import HandlerError, ScanRuntime, scan_progress_handler


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 8, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


def row(probe_id, status="complete", reason=None):
    return EvidenceAttemptRow(
        probe_id=probe_id,
        rubric_point_id="P1.1",
        seed=0,
        status=status,
        framework="fixture",
        corpus_version="test",
        evidence_ref=f"evidence://secret/{probe_id}",
        incomplete_reason=reason,
        model_version="target-secret",
    )


def submit(store):
    return store.submit(
        agent_id="agent-secret",
        scanner_version="1.3.0",
        corpus_version="test",
        model_versions={"primary": "secret-model"},
        seed=0,
        registry_cases=(
            FullSuiteCase("a", "P1.1"),
            FullSuiteCase("b", "P1.1"),
            FullSuiteCase("c", "P1.1"),
        ),
        tenant_id="owner",
        target_id="target-secret",
        scan_id="scan-public-id",
    )


def test_progress_is_monotonic_idempotent_and_bounded():
    clock = Clock()
    store = FullScanJobStore(clock=clock)
    job = submit(store)
    assert store.progress(job.job_id, tenant_id="owner")["counts"]["observed"] == 0
    lease = store.lease(job.job_id)
    clock.now += timedelta(seconds=10)
    store.checkpoint(job.job_id, attempt_id=lease["attempt_id"], rows=[row("b"), row("a")])
    # Duplicate/out-of-order delivery replaces the same durable identities.
    store.checkpoint(job.job_id, attempt_id=lease["attempt_id"], rows=[row("a"), row("b")])
    progress = store.progress(job.job_id, tenant_id="owner")
    assert progress["counts"] == {
        "completed": 2, "timed_out": 0, "incomplete": 0, "failed": 0,
        "observed": 2, "total": 3,
    }
    assert progress["timing"]["observed_rate_per_second"] == 0.2
    assert progress["timing"]["queue_seconds"] == 0.0
    assert progress["timing"]["execution_seconds"] == 10.0
    assert "secret" not in repr(progress)
    with pytest.raises(KeyError):
        store.progress(job.job_id, tenant_id="other-owner")


def test_queue_wait_is_separate_from_slow_execution():
    clock = Clock()
    store = FullScanJobStore(clock=clock)
    job = submit(store)
    clock.now += timedelta(seconds=30)
    queued = store.progress(job.job_id, tenant_id="owner")
    assert queued["state"] == "queued"
    assert queued["timing"] == {
        "elapsed_seconds": 30.0,
        "queue_seconds": 30.0,
        "execution_seconds": 0.0,
        "observed_rate_per_second": None,
        "estimated_remaining_seconds": None,
    }
    lease = store.lease(job.job_id)
    clock.now += timedelta(seconds=45)
    store.checkpoint(job.job_id, attempt_id=lease["attempt_id"], rows=[row("a")])
    running = store.progress(job.job_id, tenant_id="owner")
    assert running["timing"]["queue_seconds"] == 30.0
    assert running["timing"]["execution_seconds"] == 45.0
    assert running["timing"]["estimated_remaining_seconds"] == {"low": 120.0, "high": 180.0}


def test_progress_survives_restart_and_projects_terminal_incomplete(tmp_path):
    clock = Clock()
    db = tmp_path / "jobs.sqlite3"
    store = FullScanJobStore(clock=clock, persistence=FullScanJobPersistence(db))
    job = submit(store)
    lease = store.lease(job.job_id)
    store.checkpoint(
        job.job_id,
        attempt_id=lease["attempt_id"],
        rows=[row("a"), row("b", "incomplete", "timeout")],
    )
    store.finalize(job.job_id, attempt_id=lease["attempt_id"])
    restarted = FullScanJobStore(clock=clock, persistence=FullScanJobPersistence(db))
    progress = restarted.progress(job.job_id, tenant_id="owner")
    assert progress["state"] == "incomplete"
    assert progress["phase"] == "report_preparation"
    assert progress["counts"]["timed_out"] == 1
    assert progress["counts"]["total"] == 3
    assert restarted.progress_for_scan("scan-public-id", tenant_id="owner") == progress
    with pytest.raises(KeyError):
        restarted.progress_for_scan("scan-public-id", tenant_id="other-owner")


def test_progress_handler_requires_owner_and_hides_foreign_scan():
    store = FullScanJobStore()
    job = submit(store)
    runtime = ScanRuntime(run_scan_fn=lambda *_: None, full_scan_job_store=store)
    status, payload = scan_progress_handler(
        job.scan_id, runtime=runtime, authenticated_tenant_id="owner"
    )
    assert status == 200
    assert payload["counts"]["total"] == 3
    with pytest.raises(HandlerError) as missing_auth:
        scan_progress_handler(job.scan_id, runtime=runtime, authenticated_tenant_id=None)
    assert missing_auth.value.status == 401
    with pytest.raises(HandlerError) as foreign:
        scan_progress_handler(job.scan_id, runtime=runtime, authenticated_tenant_id="other")
    assert foreign.value.status == 404


def test_authenticated_http_progress_route_is_owner_scoped(monkeypatch):
    monkeypatch.setenv("ARE_API_AUTH_TOKENS_JSON", '{"owner-token":"owner","foreign-token":"other"}')
    store = FullScanJobStore()
    job = submit(store)
    runtime = ScanRuntime(run_scan_fn=lambda *_: None, full_scan_job_store=store)
    client = TestClient(create_app(bundles=BundleStore(), scan_runtime=runtime))
    owner = client.get(
        f"/scanner/v1/scans/{job.scan_id}/progress",
        headers={"Authorization": "Bearer owner-token"},
    )
    foreign = client.get(
        f"/scanner/v1/scans/{job.scan_id}/progress",
        headers={"Authorization": "Bearer foreign-token"},
    )
    assert owner.status_code == 200
    assert owner.json()["accepted_pack_total"] == 3
    assert foreign.status_code == 404
    assert "secret" not in owner.text
