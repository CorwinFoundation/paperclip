from __future__ import annotations

import pytest

from api.full_scan_executor import (
    FullScanJobStore,
    WorkerAttemptResult,
    dispatch_registry_attempt,
    registry_cases_from_rows,
)


def _row(case_id: str) -> dict:
    return {
        "test_id": "capability_family",
        "case_id": case_id,
        "status": "active",
        "production_runner_executable": True,
        "risk_or_rubric_id": "P3.1",
        "runner": "promptfoo_wrapper",
        "applicability_gate": {
            "status": "applicable_when_capability_declared",
            "required_capability": "has_tool_calling",
            "na_evidence_required": True,
            "capability_gate": "tool surface required",
        },
    }


@pytest.mark.parametrize("manifest", [None, {}, {"has_tool_calling": False}])
@pytest.mark.parametrize("max_workers", [1, 2])
def test_capability_gate_is_hard_precondition_before_dispatch(manifest, max_workers):
    rows = [_row("case-1"), _row("case-2")]
    store = FullScanJobStore()
    job = store.submit(
        agent_id="chat-only-target",
        scanner_version="0.5.0",
        corpus_version="1.3.0",
        model_versions={"primary": "fixture", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(rows),
        capability_manifest=manifest,
    )
    lease = store.lease(job.job_id)
    calls = []

    def worker(row, _job):
        calls.append(row["case_id"])
        return WorkerAttemptResult(status="complete", evidence_ref="unexpected")

    dispatched = dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=rows,
        workers={"promptfoo": worker},
        max_workers=max_workers,
    )

    assert calls == []
    assert {row.verdict for row in dispatched.evidence_rows} == {"not_applicable"}
    assert {row.status for row in dispatched.evidence_rows} == {"complete"}
    assert all(row.na_evidence["missing_capability_name"] == "has_tool_calling" for row in dispatched.evidence_rows)


def test_declared_capability_still_dispatches():
    rows = [_row("case-1")]
    store = FullScanJobStore()
    job = store.submit(
        agent_id="tool-target",
        scanner_version="0.5.0",
        corpus_version="1.3.0",
        model_versions={"primary": "fixture", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(rows),
        capability_manifest={"has_tool_calling": True},
    )
    lease = store.lease(job.job_id)
    calls = []

    def worker(row, _job):
        calls.append(row["case_id"])
        return WorkerAttemptResult(status="complete", evidence_ref="evidence://case-1")

    dispatched = dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=rows,
        workers={"promptfoo": worker},
    )

    assert calls == ["case-1"]
    assert dispatched.evidence_rows[0].status == "complete"
