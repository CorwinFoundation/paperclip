"""Target-bound dispatch coverage for evidence-pending demo cases."""
from __future__ import annotations

import pytest

from api.full_scan_executor import (
    FullScanExecutorError,
    FullScanJobStore,
    WorkerAttemptResult,
    dispatch_registry_attempt,
    registry_cases_from_rows,
)


def test_target_bound_flag_dispatches_without_changing_production_default(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "target-bound-test-key")
    rows = [
        {
            "test_id": "promptinject",
            "case_id": "promptinject.direct",
            "status": "active",
            "target_bound": True,
            "production_runner_executable": False,
            "risk_or_rubric_id": "ROBUSTNESS.PROMPT_INJECTION",
            "runner": "promptfoo_wrapper",
        }
    ]

    with pytest.raises(FullScanExecutorError, match="no executable active registry rows"):
        registry_cases_from_rows(rows)
    cases = registry_cases_from_rows(rows, execution_flag="target_bound")
    assert len(cases) == 1

    store = FullScanJobStore()
    job = store.submit(
        agent_id="agent-1",
        scanner_version="0.5.0",
        corpus_version="target-bound-test",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=42,
        registry_cases=cases,
    )
    lease = store.lease(job.job_id)

    dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=rows,
        workers={
            "promptfoo": lambda _row, _job: WorkerAttemptResult(
                status="complete",
                evidence_ref="evidence://target-bound/promptinject.direct.json",
            )
        },
        execution_flag="target_bound",
    )
    finalized = store.finalize(job.job_id)

    assert finalized.status == "complete"
    assert finalized.final_manifest is not None
    assert finalized.final_manifest["counts"] == {
        "expected": 1,
        "evidence_rows": 1,
        "missing_required": 0,
    }
