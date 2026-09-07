"""BEAAA-30209: bind the two demographic-parity rows to live dispatch."""
from __future__ import annotations

from collections import Counter

from harness.runner import MockRunner
from tools.active_test_registry import build_registry, count_active_executable
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _rows() -> list[dict]:
    return [
        row for row in build_registry()
        if row.get("status") == "active" and row["test_id"] == "demographic_parity"
    ]


def test_exact_demographic_parity_roster_is_executable() -> None:
    rows = _rows()
    assert Counter(row["case_id"] for row in rows) == {
        "dp.bbq_disambig": 1,
        "dp.legal_intake_counterfactual": 1,
    }
    assert all(row["production_runner_executable"] is True for row in rows)
    assert count_active_executable(build_registry()) == 2_348


def test_live_dispatch_records_two_verdicts_and_exact_calls(tmp_path) -> None:
    target_calls: list[str] = []
    judge_calls: list[str] = []

    def target(prompt: str) -> str:
        target_calls.append(prompt)
        return "I will decide from the supplied evidence and apply the same criteria to everyone."

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

    assert len(outcomes) == len(target_calls) == len(judge_calls) == 2
    assert Counter(outcome["verdict"] for outcome in outcomes) == {"pass": 2}
    assert all(outcome["payloads"] and outcome["responses"] for outcome in outcomes)
    assert all(outcome["judge"]["type"] == "demographic_parity_registered_adapter" for outcome in outcomes)
