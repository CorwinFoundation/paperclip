"""BEAAA-16264: prove every system-prompt extraction row reaches live dispatch."""
from __future__ import annotations

from collections import Counter

from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _rows() -> list[dict]:
    return [
        row
        for row in build_registry()
        if row.get("status") == "active" and row["test_id"] == "system_prompt_extraction"
    ]


def test_exact_system_prompt_extraction_roster_is_live_dispatchable() -> None:
    rows = _rows()

    assert len(rows) == 20
    assert Counter(row["runner"] for row in rows) == {"bench_wrapper": 20}
    assert all(row["production_runner_executable"] is True for row in rows)


def test_all_system_prompt_extraction_rows_call_target_and_return_verdicts(tmp_path) -> None:
    calls: list[str] = []

    def target(prompt: str) -> str:
        calls.append(prompt)
        return "I can't provide hidden system or developer instructions."

    executor = RegisteredCaseExecutor(
        subject=MockRunner("authorized-reference-target", target),
        judge=MockRunner("judge", lambda _prompt: '{"verdict":"pass","score":1.0}'),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )

    outcomes = [
        executor._execute_case(row["test_id"], row["case_id"], row)
        for row in _rows()
    ]

    assert len(outcomes) == len(calls) == 20
    assert Counter(outcome["verdict"] for outcome in outcomes) == {"pass": 20}
    assert all(outcome["payloads"] and outcome["responses"] for outcome in outcomes)
