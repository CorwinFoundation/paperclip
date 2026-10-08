"""BEAAA-39111: scheduled native dispatch keeps every per-test outcome visible."""

from api.day1_report_only import default_registry_rows
from api.full_scan_executor import (
    FullScanJobStore,
    WorkerAttemptResult,
    dispatch_registry_attempt,
    registry_cases_from_rows,
)


CAPABILITIES = {
    "identity_boundary_testable": True,
    "recipient_boundary_testable": True,
    "session_boundary_testable": True,
    "tenant_boundary_testable": True,
    "tool_boundary_testable": True,
}


def test_scheduled_dispatch_preserves_pass_fail_invalid_incomplete_and_worker_error():
    rows = default_registry_rows(CAPABILITIES)[:5]
    outcomes = {
        rows[0]["case_id"]: WorkerAttemptResult(
            status="complete", verdict="PASS", target_calls=1,
            evidence_ref="evidence://unused/pass.json",
        ),
        rows[1]["case_id"]: WorkerAttemptResult(
            status="complete", verdict="FAIL", target_calls=1,
            evidence_ref="evidence://unused/fail.json",
        ),
        rows[2]["case_id"]: WorkerAttemptResult(
            status="complete", verdict="INVALID", target_calls=1,
            evidence_ref="evidence://unused/invalid.json",
        ),
        rows[3]["case_id"]: WorkerAttemptResult(
            status="incomplete", verdict="INCOMPLETE", target_calls=1,
            incomplete_reason="invalid_output:missing_required_observation",
            evidence_ref="evidence://unused/incomplete.json",
        ),
    }

    def worker(row, _job):
        return outcomes[row["case_id"]]

    store = FullScanJobStore()
    job = store.submit(
        agent_id="controlled-target",
        scanner_version="0.5.0",
        corpus_version="direct-family-contract@2026-10-07",
        model_versions={"target": "controlled-target@1"},
        seed=39111,
        registry_cases=registry_cases_from_rows(rows),
    )
    attempt_id = store.lease(job.job_id)["attempt_id"]
    dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=attempt_id,
        registry_rows=rows,
        workers={"day1_report_only": worker},
    )
    manifest = store.finalize(job.job_id, attempt_id=attempt_id).final_manifest

    assert manifest is not None
    assert manifest["counts"] == {
        "expected": 5,
        "evidence_rows": 5,
        "missing_required": 2,
    }
    assert manifest["reconciliation"] == {
        "attempted": 5,
        "terminal": 5,
        "evidenced": 3,
        "unjustified_non_acquisition": 0,
        "by_status": {"complete": 3, "incomplete": 2},
    }
    by_id = {row["probe_id"]: row for row in manifest["evidence_rows"]}
    assert [by_id[row["case_id"]]["verdict"] for row in rows[:4]] == [
        "PASS", "FAIL", "INVALID", "INCOMPLETE",
    ]
    assert by_id[rows[3]["case_id"]]["incomplete_reason"] == (
        "invalid_output:missing_required_observation"
    )
    missing = by_id[rows[4]["case_id"]]
    assert missing["status"] == "incomplete"
    assert missing["verdict"] is None
    assert missing["incomplete_reason"] == "worker_error:day1_report_only:KeyError"
    assert all(row["score_contribution"] is False for row in by_id.values())
    assert all(row["disposition"] == "report-only" for row in by_id.values())
