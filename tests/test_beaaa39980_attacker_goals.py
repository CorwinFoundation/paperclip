import json

import pytest

from harness.attacker_goals import FAMILY_SEEDS, build_inventory, generate_goals, rotate_holdout


def test_good_coverage_inventory_is_unique_and_balanced() -> None:
    goals = generate_goals()
    assert len(goals) == 625
    assert len({goal.goal_id for goal in goals}) == 625
    assert {family: sum(goal.family == family for goal in goals) for family in FAMILY_SEEDS} == {
        family: 125 for family in FAMILY_SEEDS
    }


def test_monthly_holdout_is_exactly_twenty_percent_per_family() -> None:
    goals = generate_goals()
    public, private = rotate_holdout(goals, "2026-10")
    assert len(public) == 500
    assert len(private) == 125
    assert not ({goal.goal_id for goal in public} & {goal.goal_id for goal in private})
    assert all(sum(goal.family == family for goal in private) == 25 for family in FAMILY_SEEDS)
    assert rotate_holdout(goals, "2026-11")[1] != private


def test_inventory_writes_private_goals_only_to_holdout_tree(tmp_path) -> None:
    public_path, private_path = build_inventory(tmp_path, "2026-10")
    public_ids = {json.loads(line)["goal_id"] for line in public_path.read_text().splitlines()}
    private_ids = {json.loads(line)["goal_id"] for line in private_path.read_text().splitlines()}
    assert "third_party/private-holdout" in private_path.as_posix()
    assert public_ids.isdisjoint(private_ids)


@pytest.mark.parametrize("month", ["202610", "2026-1", "october"])
def test_rotation_rejects_non_canonical_month(month: str) -> None:
    with pytest.raises(ValueError, match="YYYY-MM"):
        rotate_holdout(generate_goals(), month)
