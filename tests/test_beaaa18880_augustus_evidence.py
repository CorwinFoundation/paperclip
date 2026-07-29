from __future__ import annotations

import json

import pytest

from harness.bench.augustus_evidence import (
    REQUIRED_RUN_FIELDS,
    configurations,
    execute_fixture,
    run_pilot,
    validate_run,
)


def test_exactly_ten_configurations_five_goals_repeated_with_hydra() -> None:
    configs = configurations()
    assert len(configs) == 10
    assert len({item["goal_id"] for item in configs}) == 5
    assert {item["hydra_enabled"] for item in configs} == {False, True}
    for goal_id in {item["goal_id"] for item in configs}:
        assert sum(item["goal_id"] == goal_id for item in configs) == 2


def test_pilot_retains_all_fifty_raw_runs_and_required_provenance(tmp_path) -> None:
    manifest = run_pilot(tmp_path)
    rows = [json.loads(line) for line in (tmp_path / "run-matrix.jsonl").read_text().splitlines()]
    assert manifest["executions"] == 50
    assert len(rows) == len({row["run_id"] for row in rows}) == 50
    assert all(REQUIRED_RUN_FIELDS <= row.keys() for row in rows)
    assert all(row["seed"] in range(5) for row in rows)
    assert all(row["evidence_refs"][0].startswith("sha256:") for row in rows)
    assert all(row["total_estimated_cost_usd"] == 0 for row in rows)


def test_validation_fails_closed_when_provenance_or_cost_is_missing() -> None:
    row = execute_fixture(configurations()[0], 0)
    row.pop("secondary_judge_estimated_cost_usd")
    with pytest.raises(ValueError, match="omits required fields"):
        validate_run(row)


def test_validation_fails_if_augustus_evidence_enters_scoring() -> None:
    row = execute_fixture(configurations()[0], 0)
    row["classification"] = "scored"
    row["scoring_effect"] = "changes_total"
    with pytest.raises(ValueError, match="reported-not-scored"):
        validate_run(row)


def test_no_scoring_rubric_corpus_or_api_imports() -> None:
    import harness.bench.augustus_evidence as module

    source = open(module.__file__, encoding="utf-8").read()
    forbidden = ("from scanners", "from rubric", "from api", "import scanners", "import rubric", "import api")
    assert not any(token in source for token in forbidden)

