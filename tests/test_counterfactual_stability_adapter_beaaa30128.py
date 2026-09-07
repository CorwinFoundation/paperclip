"""BEAAA-30128: bind every active counterfactual-stability row."""
from __future__ import annotations

from collections import Counter

from harness.runner import MockRunner
from tools.active_test_registry import build_registry, count_active_executable
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _rows() -> list[dict]:
    return [
        row for row in build_registry()
        if row.get("status") == "active"
        and row["test_id"] == "counterfactual_stability"
        and row.get("content_candidate_inventory") is True
    ]


def test_exact_counterfactual_roster_is_executable() -> None:
    rows = _rows()
    assert len(rows) == 60
    assert Counter(row["runner"] for row in rows) == {"content_candidate_worker": 60}
    assert all(row["production_runner_executable"] is True for row in rows)
    assert count_active_executable(build_registry()) == 2_349


def test_live_dispatch_records_60_verdicts_and_120_target_calls(tmp_path) -> None:
    calls: list[str] = []

    def target(prompt: str) -> str:
        calls.append(prompt)
        return "The same neutral recommendation applies."

    executor = RegisteredCaseExecutor(
        subject=MockRunner("authorized-reference-target", target),
        judge=MockRunner("judge", lambda _prompt: '{"label":"stable"}'),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )
    outcomes = [executor._execute_case(row["test_id"], row["case_id"], row) for row in _rows()]

    assert len(outcomes) == 60
    assert len(calls) == 120
    assert Counter(outcome["verdict"] for outcome in outcomes) == {"pass": 60}
    assert all(len(outcome["payloads"]) == len(outcome["responses"]) == 2 for outcome in outcomes)
