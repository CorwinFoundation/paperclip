"""BEAAA-31973: controlled full scans resume without repeating spend."""

from types import SimpleNamespace

from api.full_scan_executor import (
    EvidenceAttemptRow,
    FullScanJobStore,
    registry_cases_from_rows,
)
from api.full_scan_job_persistence import FullScanJobPersistence
from scripts import scheduled_scan


def test_zoo_target_reopens_durable_job_and_dispatches_only_pending(monkeypatch, tmp_path):
    rows = [
        {
            "test_id": "family",
            "case_id": case_id,
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "R1",
        }
        for case_id in ("already-paid", "pending")
    ]
    decision = SimpleNamespace(rows=rows)
    target = SimpleNamespace(
        name="fixture",
        capability_manifest={"prose": True},
        adapter_proof="fixture-adapter",
    )
    config = scheduled_scan.ScheduleConfig(
        "tenant", "endpoint", tmp_path, "scanner", "corpus", 0,
        1, 30, 0, 100, 30, "judge",
    )
    observed = {}

    class Worker:
        def manifest_metadata(self):
            return {"primary": "fixture@1"}

    class FakeStore:
        def __init__(self, *, persistence):
            observed["persistence"] = persistence
            self.job = SimpleNamespace(
                job_id="job-1", status="running", active_attempt_id="dead-attempt",
                final_manifest=None, scan_id="scan-1", final_manifest_hash=None,
            )

        def submit(self, **kwargs):
            observed["idempotency_key"] = kwargs["idempotency_key"]
            return self.job

        def recover(self):
            self.job.active_attempt_id = None
            self.job.status = "queued"
            return {"resumed": [self.job.job_id], "failed": []}

        def status(self, job_id):
            assert job_id == self.job.job_id
            return self.job

        def resume(self, job_id):
            return {
                "evidence_rows": [
                    {"probe_id": "already-paid", "status": "complete"}
                ]
            }

        def lease(self, job_id, *, ttl_seconds):
            return {"attempt_id": "resumed-attempt"}

        def finalize(self, job_id, *, attempt_id):
            self.job.status = "complete"
            self.job.final_manifest = {"evidence_rows": []}
            return self.job

    monkeypatch.setattr(scheduled_scan, "FullScanJobPersistence", lambda path: path)
    monkeypatch.setattr(scheduled_scan, "FullScanJobStore", FakeStore)
    monkeypatch.setattr(
        scheduled_scan, "build_registered_case_workers", lambda *a, **k: {"x": Worker()}
    )
    monkeypatch.setattr("harness.controlled_zoo.build_zoo_runner", lambda target: object())
    monkeypatch.setattr(
        "harness.controlled_zoo.controlled_tau_session_factory", lambda subject: object()
    )

    def dispatch(store, job_id, *, registry_rows, **kwargs):
        observed["dispatched"] = [row["case_id"] for row in registry_rows]

    monkeypatch.setattr(scheduled_scan, "dispatch_registry_attempt", dispatch)

    scheduled_scan._run_zoo_target(
        target,
        [decision],
        config=config,
        output_dir=tmp_path / "2026-09-12" / "zoo",
        run_date="2026-09-12",
        limiter=scheduled_scan.SharedRateLimiter(30),
    )

    assert observed["persistence"] == (
        tmp_path / "2026-09-12" / "zoo" / "full_scan_jobs.sqlite3"
    )
    assert observed["idempotency_key"] == (
        "scheduled-zoo:2026-09-12:fixture:scanner:corpus:0"
    )
    assert observed["dispatched"] == ["pending"]


def test_zoo_target_matches_real_checkpoint_identity_after_restart(monkeypatch, tmp_path):
    rows = [
        {
            "test_id": "family",
            "case_id": case_id,
            "runtime_case_identity": f"family:{case_id}",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "R1",
        }
        for case_id in ("already-paid", "pending")
    ]
    decision = SimpleNamespace(rows=rows)
    target = SimpleNamespace(
        name="fixture",
        capability_manifest={"prose": True},
        adapter_proof="fixture-adapter",
    )
    config = scheduled_scan.ScheduleConfig(
        "tenant", "endpoint", tmp_path, "scanner", "corpus", 0,
        1, 30, 0, 100, 30, "judge",
    )
    output_dir = tmp_path / "2026-09-12" / "zoo"
    output_dir.mkdir(parents=True)
    database = output_dir / "full_scan_jobs.sqlite3"
    idempotency_key = "scheduled-zoo:2026-09-12:fixture:scanner:corpus:0"

    # Process one checkpoints the exact identity derived from a production-form
    # registry row, then dies while its lease is active.
    first = FullScanJobStore(persistence=FullScanJobPersistence(database))
    job = first.submit(
        agent_id="controlled-zoo-fixture",
        scanner_version="scanner",
        corpus_version="corpus",
        model_versions={"primary": "fixture@1"},
        seed=0,
        registry_cases=registry_cases_from_rows(rows),
        idempotency_key=idempotency_key,
    )
    lease = first.lease(job.job_id)
    first.checkpoint(
        job.job_id,
        attempt_id=lease["attempt_id"],
        rows=[
            EvidenceAttemptRow(
                probe_id="family:already-paid",
                rubric_point_id="R1",
                status="complete",
                framework="fixture@1",
                corpus_version="corpus",
                model_version="fixture@1",
                seed=0,
                evidence_ref="evidence://already-paid.json",
            )
        ],
    )
    del first

    class Worker:
        def manifest_metadata(self):
            return {"primary": "fixture@1"}

    monkeypatch.setattr(
        scheduled_scan, "build_registered_case_workers", lambda *a, **k: {"x": Worker()}
    )
    monkeypatch.setattr("harness.controlled_zoo.build_zoo_runner", lambda target: object())
    monkeypatch.setattr(
        "harness.controlled_zoo.controlled_tau_session_factory", lambda subject: object()
    )
    observed = {}

    def dispatch(store, job_id, *, registry_rows, **kwargs):
        observed["dispatched"] = [row["runtime_case_identity"] for row in registry_rows]

    monkeypatch.setattr(scheduled_scan, "dispatch_registry_attempt", dispatch)

    scheduled_scan._run_zoo_target(
        target,
        [decision],
        config=config,
        output_dir=output_dir,
        run_date="2026-09-12",
        limiter=scheduled_scan.SharedRateLimiter(30),
    )

    assert observed["dispatched"] == ["family:pending"]
