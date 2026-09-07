"""BEAAA-30216: bind the equal-opportunity row to live dispatch."""
from __future__ import annotations

from harness.runner import MockRunner
from tools.active_test_registry import build_registry, count_active_executable
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _rows() -> list[dict]:
    return [
        row for row in build_registry()
        if row.get("status") == "active" and row["test_id"] == "equal_opportunity"
    ]


def test_exact_equal_opportunity_roster_is_executable() -> None:
    rows = _rows()
    assert [row["case_id"] for row in rows] == ["eo.uw_labeled_subset"]
    assert rows[0]["production_runner_executable"] is True
    assert count_active_executable(build_registry()) == 2_353


def test_live_dispatch_records_one_verdict_and_exact_calls(tmp_path) -> None:
    target_calls: list[str] = []
    judge_calls: list[str] = []

    def target(prompt: str) -> str:
        target_calls.append(prompt)
        return "Both cases should be routed to an employment lawyer for retaliation review."

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

    assert len(outcomes) == len(target_calls) == len(judge_calls) == 1
    assert outcomes[0]["verdict"] == "pass"
    assert outcomes[0]["payloads"] and outcomes[0]["responses"]
    assert outcomes[0]["judge"]["type"] == "equal_opportunity_registered_adapter"
