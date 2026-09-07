"""BEAAA-29973: bind every active cyber-misuse refusal row to live dispatch."""
from __future__ import annotations

from collections import Counter

from harness.runner import MockRunner
from tools.active_test_registry import build_registry, count_active_executable
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _rows() -> list[dict]:
    return [
        row for row in build_registry()
        if row.get("status") == "active" and row["test_id"] == "cyber_misuse_refusal"
    ]


def test_exact_cyber_misuse_roster_is_executable_without_other_family_delta() -> None:
    rows = build_registry()
    cyber = _rows()

    assert len(cyber) == 40
    assert Counter(row["runner"] for row in cyber) == {
        "bench_wrapper": 20,
        "content_candidate_worker": 20,
    }
    assert all(row["production_runner_executable"] is True for row in cyber)
    assert count_active_executable(rows) == 2_348


def test_all_cyber_misuse_rows_call_live_target_and_return_verdicts(tmp_path) -> None:
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

    assert len(outcomes) == len(calls) == 40
    assert Counter(outcome["verdict"] for outcome in outcomes) == {"pass": 40}
    assert all(outcome["payloads"] and outcome["responses"] for outcome in outcomes)
