import json
from decimal import Decimal

import pytest

from harness.attacker_engine import AttackGoal, AttackResult
from harness.attacker_evidence import FrozenCaseError, FrozenCaseStore, summarize_zoo, write_zoo_measurements


def _result(goal_id: str, family: str, success: bool, turns: int = 2, cost: str = "0.40") -> AttackResult:
    result = AttackResult(
        AttackGoal(goal_id, family, "objective", "high"),
        "goat",
        "attacker@2026-10-01",
        "judge@2026-10-01",
        success=success,
        cost_usd=Decimal(cost),
    )
    result.turns = [
        {"role": role, "content": f"{role}-{index}"}
        for index in range(turns)
        for role in ("attacker", "target")
    ]
    return result


def test_freeze_store_is_success_only_and_append_only(tmp_path):
    path = tmp_path / "cases.jsonl"
    store = FrozenCaseStore(path)
    case = store.freeze(_result("goal-1", "secrets", True), commit="abc123")
    assert case["provenance"] == "generated:attacker-v1@abc123"
    assert json.loads(path.read_text()) ["id"] == "goal-1"
    with pytest.raises(FrozenCaseError, match="already frozen"):
        store.freeze(_result("goal-1", "secrets", True), commit="different")
    with pytest.raises(FrozenCaseError, match="failed attacks"):
        store.freeze(_result("goal-2", "secrets", False), commit="abc123")


def test_zoo_summary_reports_success_rate_turns_and_cost(tmp_path):
    measurements = [
        summarize_zoo("weak-1", "secrets", [_result("a", "secrets", True, 2, "0.40"), _result("b", "secrets", False)]),
        summarize_zoo("strong-1", "secrets", [_result("c", "secrets", False)]),
    ]
    assert measurements[0].success_rate == 0.5
    assert measurements[0].median_turns_to_success == 2
    assert measurements[0].cost_per_success_usd == "0.40"
    assert measurements[1].cost_per_success_usd is None
    output = write_zoo_measurements(tmp_path / "zoo.json", measurements)
    assert json.loads(output.read_text())[0]["agent_id"] == "weak-1"
