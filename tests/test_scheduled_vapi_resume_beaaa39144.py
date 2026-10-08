"""BEAAA-39144: scheduled Vapi scans retain per-case progress."""

from api.full_scan_executor import EvidenceAttemptRow, FullScanJobStore, FullSuiteCase
from api.full_scan_job_persistence import FullScanJobPersistence
from scripts.scheduled_scan import _pending_rows_for_resume


def _row(job_id: str, *, status: str = "complete") -> EvidenceAttemptRow:
    return EvidenceAttemptRow(probe_id="suite:a", rubric_point_id="r1", status=status, framework="mock", corpus_version="c", model_version="m", seed=7, evidence_ref=f"evidence://{job_id}/a.json" if status == "complete" else None, incomplete_reason=None if status == "complete" else "operator_abort", verdict="PASS" if status == "complete" else None, target_calls=1, judge_calls=1)


def test_fresh_store_resumes_only_unfinished_stable_identity(tmp_path) -> None:
    db = tmp_path / "jobs.sqlite3"
    store = FullScanJobStore(persistence=FullScanJobPersistence(db))
    cases = (FullSuiteCase("suite:a", "r1"), FullSuiteCase("suite:b", "r1"))
    job = store.submit(agent_id="target", scanner_version="s", corpus_version="c", model_versions={}, seed=7, registry_cases=cases, idempotency_key="run")
    lease = store.lease(job.job_id)
    store.checkpoint(job.job_id, attempt_id=lease["attempt_id"], rows=[_row(job.job_id)])

    fresh = FullScanJobStore(persistence=FullScanJobPersistence(db))
    resumed = fresh.resume(job.job_id)
    rows = [{"runtime_case_identity": "suite:a"}, {"runtime_case_identity": "suite:b"}]
    assert [row["runtime_case_identity"] for row in _pending_rows_for_resume(rows, resumed)] == ["suite:b"]
    assert resumed["evidence_rows"][0]["target_calls"] == 1
    assert resumed["evidence_rows"][0]["judge_calls"] == 1


def test_incomplete_disposition_does_not_permanently_skip_case(tmp_path) -> None:
    store = FullScanJobStore(persistence=FullScanJobPersistence(tmp_path / "jobs.sqlite3"))
    job = store.submit(agent_id="target", scanner_version="s", corpus_version="c", model_versions={}, seed=7, registry_cases=(FullSuiteCase("suite:a", "r1"),))
    lease = store.lease(job.job_id)
    store.checkpoint(job.job_id, attempt_id=lease["attempt_id"], rows=[_row(job.job_id, status="incomplete")])
    assert [row["case_id"] for row in store.resume(job.job_id)["pending_cases"]] == ["suite:a"]
