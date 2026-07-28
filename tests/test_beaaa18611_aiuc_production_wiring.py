from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.registered_case_executor import (
    EvidenceStore,
    RegisteredCaseExecutor,
    ScanBudget,
)


FAMILY_COUNTS = {
    "human_escalation_flagging": 20,
    "customer_defined_risk_pack": 12,
    "mcp_tool_integrity": 12,
}


def _rows():
    return [
        row
        for row in build_registry()
        if row["test_id"] in FAMILY_COUNTS
    ]


def test_exactly_44_unique_canonical_production_rows():
    rows = _rows()
    assert Counter(row["test_id"] for row in rows) == FAMILY_COUNTS
    assert len(rows) == 44
    assert len({row["runtime_case_identity"] for row in rows}) == 44
    assert {row["canonical_runtime_case"] for row in rows} == {True}
    assert {row["alias_of_runtime_case_identity"] for row in rows} == {None}
    assert {row["production_runner_executable"] for row in rows} == {True}
    assert {row["execution_evidenced"] for row in rows} == {True}
    assert {row["reporting_status"] for row in rows} == {"reported_not_scored"}
    assert {row["scoring_active"] for row in rows} == {False}


def _executor(tmp_path: Path, response: str, *, capabilities=None):
    return RegisteredCaseExecutor(
        subject=MockRunner("target@1", lambda _prompt: response),
        judge=MockRunner("unused@1", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_target_calls=10, max_judge_calls=0),
        target_capabilities=capabilities,
    )


def test_human_escalation_uses_standard_target_path_and_deterministic_grader(tmp_path):
    row = next(row for row in _rows() if row["case_id"] == "esc-01")
    result = _executor(
        tmp_path, '{"escalate": true, "rationale": "human review"}'
    )(row, SimpleNamespace(job_id="job-escalation", agent_id="agent", seed=0))
    assert result.status == "complete"
    assert result.verdict == "pass"
    assert result.target_calls == 1
    assert result.judge_calls == 0


def test_mcp_absent_is_not_applicable_and_never_calls_target(tmp_path):
    row = next(row for row in _rows() if row["case_id"] == "mcp-01")
    result = _executor(tmp_path, '{"rejected_tool": true}')(
        row, SimpleNamespace(job_id="job-mcp-na", agent_id="agent", seed=0)
    )
    assert result.status == "complete"
    assert result.verdict == "not_applicable"
    assert result.target_calls == 0
    evidence = json.loads(
        (tmp_path / "job-mcp-na" / "mcp_tool_integrity" / "mcp-01.json").read_text()
    )
    assert evidence["judge"]["applicable"] is False


def test_mcp_or_a2a_declaration_executes_standard_target_path(tmp_path):
    row = next(row for row in _rows() if row["case_id"] == "mcp-01")
    result = _executor(
        tmp_path,
        '{"rejected_tool": true, "rationale": "tampered"}',
        capabilities={"declares_a2a": True},
    )(row, SimpleNamespace(job_id="job-mcp-on", agent_id="agent", seed=0))
    assert result.status == "complete"
    assert result.verdict == "pass"
    assert result.target_calls == 1
