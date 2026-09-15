"""BEAAA-32688: bounded live-account containment-failure scan case."""
from scripts.scheduled_scan import (
    LIVE_CONTAINMENT_FAILURE_CASE,
    _rows_for_job,
    _worker_factory_for_job,
)


def test_live_containment_failure_job_selects_one_stable_executable_case() -> None:
    rows, execution_flag = _rows_for_job("vapi-containment-failure")

    assert len(rows) == 1
    row = rows[0]
    assert (row["test_id"], row["case_id"]) == LIVE_CONTAINMENT_FAILURE_CASE
    assert row[execution_flag] is True
    assert row["runner"] == "content_candidate_worker"
    assert row["scoring_active"] is False


def test_live_containment_failure_job_uses_live_provider_worker_union() -> None:
    assert (
        _worker_factory_for_job("vapi-containment-failure").__name__
        == "build_live_framework_workers"
    )
