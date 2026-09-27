"""BEAAA-16264: prove the 12-row AI-disclosure Path B family reaches live dispatch."""
from __future__ import annotations

from collections import Counter

from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _path_b_rows() -> list[dict]:
    return [
        row
        for row in build_registry()
        if row.get("status") == "active"
        and row["test_id"] == "ai_disclosure"
        and row["runner"] == "bench_wrapper"
    ]


def test_exact_ai_disclosure_path_b_roster_is_live_dispatchable() -> None:
    rows = _path_b_rows()

    assert len(rows) == 12
    assert {row["reporting_status"] for row in rows} == {"reported_not_scored"}
    assert {row["scoring_active"] for row in rows} == {False}
    assert all(row["production_runner_executable"] is True for row in rows)


def test_all_ai_disclosure_path_b_rows_call_target_and_return_verdicts(tmp_path) -> None:
    calls: list[str] = []

    def target(prompt: str) -> str:
        calls.append(prompt)
        return "I am an automated AI assistant, not a human."

    executor = RegisteredCaseExecutor(
        subject=MockRunner("authorized-reference-target", target),
        judge=MockRunner("judge", lambda _prompt: '{"verdict":"pass","score":1.0}'),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )

    outcomes = [
        executor._execute_case(row["test_id"], row["case_id"], row)
        for row in _path_b_rows()
    ]

    assert len(outcomes) == len(calls) == 12
    assert Counter(outcome["verdict"] for outcome in outcomes) == {"pass": 12}
    assert all(outcome["payloads"] and outcome["responses"] for outcome in outcomes)
