import json
from pathlib import Path

from tools.oss001_calibration import TARGETS, load_existing_rows, run


def test_same_48_existing_ids_remain_distinct_and_reported_not_scored(tmp_path: Path):
    rows = load_existing_rows()
    ids = [row["case_id"] for family in rows.values() for row in family]
    assert [len(family) for family in rows.values()] == [24, 24]
    assert len(ids) == len(set(ids)) == 48

    summary = run(tmp_path)
    attempts = [json.loads(line) for line in (tmp_path / "attempts.jsonl").read_text().splitlines()]
    assert summary["actual_attempts"] == 240 * len(TARGETS)
    assert {row["seed"] for row in attempts} == set(range(5))
    assert {row["scoring_status"] for row in attempts} == {"reported_not_scored"}
    assert all(row["score_before"] == row["score_after"] for row in attempts)
    assert all(row["evidence_uri"].startswith("sha256://") for row in attempts)


def test_deterministic_happy_path_and_fail_closed_backstop(tmp_path: Path):
    summary = run(tmp_path)
    by_target = {row["agent"]: row for row in summary["targets"]}
    assert by_target["BACKSTOP_KNOWN_GOOD"]["verdict_distribution"]["pass"] == 240
    assert by_target["BACKSTOP_KNOWN_BAD"]["verdict_distribution"]["fail"] == 240
    assert all(row["spread_candidate"] <= 1 for row in summary["targets"])
    assert all(set(row["delta"].values()) == {0.0} for row in summary["targets"])
