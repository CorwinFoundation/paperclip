"""BEAAA-29998: bind every harmful-output golden row to live dispatch."""
from __future__ import annotations

from collections import Counter

from harness.runner import MockRunner
from tools.active_test_registry import build_registry, count_active_executable
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _rows() -> list[dict]:
    return [
        row for row in build_registry()
        if row.get("status") == "active" and row["test_id"] == "harmful_output_golden"
    ]


def test_exact_harmful_output_roster_is_executable_without_other_family_delta() -> None:
    rows = build_registry()
    harmful = _rows()

    assert len(harmful) == 60
    assert Counter(row["runner"] for row in harmful) == {
        "bench_wrapper": 30,
        "content_candidate_worker": 30,
    }
    assert all(row["production_runner_executable"] is True for row in harmful)
    assert count_active_executable(rows) == 2_349


def test_all_harmful_output_rows_call_live_target_and_return_verdicts(tmp_path) -> None:
    calls: list[str] = []

    def target(prompt: str) -> str:
        calls.append(prompt)
        return "I cannot help with harmful operational instructions."

    executor = RegisteredCaseExecutor(
        subject=MockRunner("authorized-reference-target", target),
        judge=MockRunner(
            "judge",
            lambda _prompt: '{"verdict":"pass","score":1.0,"reason":"safe refusal"}',
        ),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )
    outcomes = [executor._execute_case(row["test_id"], row["case_id"], row) for row in _rows()]

    assert len(outcomes) == len(calls) == 60
    assert Counter(outcome["verdict"] for outcome in outcomes) == {"pass": 60}
    assert all(outcome["payloads"] and outcome["responses"] for outcome in outcomes)
