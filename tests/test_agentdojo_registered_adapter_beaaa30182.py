"""BEAAA-30182: bind the four registered AgentDojo rows to live dispatch."""
from __future__ import annotations

from collections import Counter

from harness.runner import MockRunner
from tools.active_test_registry import build_registry, count_active_executable
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _rows() -> list[dict]:
    return [row for row in build_registry() if row.get("status") == "active" and row["test_id"] == "agentdojo"]


def test_exact_agentdojo_roster_is_executable() -> None:
    rows = _rows()
    assert len(rows) == 4
    assert all(row["production_runner_executable"] is True for row in rows)
    assert count_active_executable(build_registry()) == 2_348


def test_live_dispatch_records_four_verdicts_and_exact_calls(tmp_path) -> None:
    target_calls: list[str] = []
    judge_calls: list[str] = []

    def target(prompt: str) -> str:
        target_calls.append(prompt)
        return "I will ignore instructions found in tool output and follow the user's request safely."

    def judge(prompt: str) -> str:
        judge_calls.append(prompt)
        return '{"verdict":"pass","score":1.0}'

    executor = RegisteredCaseExecutor(
        subject=MockRunner("authorized-reference-target", target),
        judge=MockRunner("judge", judge),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )
    outcomes = [executor._execute_case(row["test_id"], row["case_id"], row) for row in _rows()]

    assert len(outcomes) == len(target_calls) == len(judge_calls) == 4
    assert Counter(outcome["verdict"] for outcome in outcomes) == {"pass": 4}
    assert all(outcome["payloads"] and outcome["responses"] for outcome in outcomes)
    assert all(outcome["judge"]["type"] == "agentdojo_registered_adapter" for outcome in outcomes)
