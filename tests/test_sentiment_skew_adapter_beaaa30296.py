"""BEAAA-30296: bind all sentiment-skew rows to live dispatch."""
from __future__ import annotations

from harness.runner import MockRunner
from tools.active_test_registry import build_registry, count_active_executable
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _rows() -> list[dict]:
    return [
        row for row in build_registry()
        if row.get("status") == "active" and row["test_id"] == "sentiment_skew"
    ]


def test_exact_sentiment_skew_roster_is_executable() -> None:
    rows = _rows()
    assert {row["case_id"] for row in rows} == {
        "ss.bold_subset",
        "ss.holisticbias_subset",
        "ss.legal_intake_counterfactual",
    }
    assert all(row["production_runner_executable"] is True for row in rows)
    assert count_active_executable(build_registry()) == 2_361


def test_live_dispatch_records_three_verdicts_and_exact_calls(tmp_path) -> None:
    target_calls: list[str] = []
    judge_calls: list[str] = []

    def target(prompt: str) -> str:
        target_calls.append(prompt)
        return "Both people should consult an employment lawyer about harassment and wrongful termination."

    def judge(prompt: str) -> str:
        judge_calls.append(prompt)
        return '{"verdict":"pass","score":1.0}'

    evidence_store = EvidenceStore(tmp_path)
    executor = RegisteredCaseExecutor(
        subject=MockRunner("authorized-reference-target", target),
        judge=MockRunner("judge", judge),
        evidence_store=evidence_store,
        budget=ScanBudget(),
    )
    outcomes = [executor._execute_case(row["test_id"], row["case_id"], row) for row in _rows()]

    assert len(outcomes) == len(target_calls) == len(judge_calls) == 3
    assert all(outcome["verdict"] == "pass" for outcome in outcomes)
    assert all(outcome["payloads"] and outcome["responses"] for outcome in outcomes)
    assert all(outcome["judge"]["type"] == "sentiment_skew_registered_adapter" for outcome in outcomes)
