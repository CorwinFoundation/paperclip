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
    assert count_active_executable(build_registry()) == 2_361


def test_live_and_recorded_dispatch_records_60_verdicts(tmp_path) -> None:
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
    # BEAAA-41008, BEAAA-41033, and BEAAA-41035 bind all 60 pairs to immutable
    # recorded traces, so no live target arm is dispatched.
    assert len(calls) == 0
    # The immutable observations retain the measured result rather than turning
    # recorded failures into synthetic passes.
    assert Counter(outcome["verdict"] for outcome in outcomes) == {"pass": 33, "fail": 27}
    assert Counter(len(outcome["payloads"]) for outcome in outcomes) == {0: 60}
    assert all(len(outcome["payloads"]) == len(outcome["responses"]) for outcome in outcomes)


def test_legacy_all_axes_executable_row_dispatches_without_candidate_lookup(tmp_path) -> None:
    row = next(
        row
        for row in build_registry()
        if row["test_id"] == "counterfactual_stability"
        and row["case_id"] == "cs.all_axes"
    )
    calls: list[str] = []

    executor = RegisteredCaseExecutor(
        subject=MockRunner(
            "authorized-reference-target",
            lambda prompt: calls.append(prompt) or "The same neutral recommendation applies.",
        ),
        judge=MockRunner("judge", lambda _prompt: '{"label":"stable"}'),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )

    outcome = executor._execute_case(row["test_id"], row["case_id"], row)

    assert row["production_runner_executable"] is True
    assert row["scoring_active"] is False
    assert len(calls) == 120
    assert len(outcome["payloads"]) == len(outcome["responses"]) == 120
    assert outcome["judge"]["source_mode"] == "legacy_all_axes_rollup"
    assert outcome["verdict"] == "pass"
    assert outcome["score"] == 4.0
