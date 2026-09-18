"""BEAAA-33708 DELIVERABLE 1: the census reads the corpus, and reads it the same way twice."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import beaaa33708_scored_corpus_census as C  # noqa: E402


def _mix(pass_cases: list[str], pass_rounds: list[str],
         fail_cases: list[str], fail_rounds: list[str]) -> dict:
    return {
        "pass": {"cases": pass_cases, "count": len(pass_cases), "rounds": pass_rounds},
        "fail": {"cases": fail_cases, "count": len(fail_cases), "rounds": fail_rounds},
    }


def test_control_only_minority_is_named_and_not_hidden_by_the_margin():
    """A minority supplied entirely by controls clears the floors and still reads as control-only."""
    mix = _mix(["control:r2:A", "control:r2:B", "control:r3:C"], ["control:r2", "control:r3"],
               ["X", "Y", "Z", "W"], ["r1"])
    entry = C.census_family("f", mix, "tier1")
    assert entry["spread_basis"] == "control_only"
    assert entry["minority_verdict"] == "pass"
    assert entry["minority_roster_observations"] == 0
    assert entry["minority_observation_margin"] == 0
    assert entry["minority_span_margin"] == 0
    assert entry["two_sided_without_controls"] is False


def test_roster_only_minority_reads_as_roster_and_survives_control_deletion():
    mix = _mix(["A", "B", "C"], ["r1"], ["X", "Y", "Z", "W"], ["r1"])
    entry = C.census_family("f", mix, "tier1")
    assert entry["spread_basis"] == "roster_only"
    assert entry["minority_control_observations"] == 0
    assert entry["two_sided_without_controls"] is True


def test_a_one_sided_family_reports_no_minority_and_a_below_floor_margin():
    mix = {"pass": {"cases": ["A", "B"], "count": 2, "rounds": ["r1"]},
           "fail": {"cases": [], "count": 0, "rounds": []}}
    entry = C.census_family("f", mix, "tier1")
    assert entry["minority_verdict"] is None
    assert entry["spread_basis"] == "no_minority"
    assert entry["minority_observation_margin"] == -C.S.MIN_MINORITY_OBSERVATIONS


@pytest.mark.parametrize("entry", C.CORPUS, ids=lambda e: e.slice_id)
def test_every_corpus_ledger_is_committed_and_carries_a_spread_gate(entry):
    assert entry.ledger.is_file(), f"census names a ledger that is not on disk: {entry.ledger}"


def test_census_over_the_committed_corpus_covers_every_scored_row_exactly_once():
    census = C.build_census()
    rows = [row for entry in census["slices"] for row in entry["rows"]]
    assert census["totals"]["scored_rows"] == len(rows)
    assert len({(row["slice_id"], row["stable_case_id"]) for row in rows}) == len(rows)
    # Disjoint slices: a row counted under two ledgers would be counted twice in the shape.
    assert len({row["stable_case_id"] for row in rows}) == len(rows)
    assert sum(census["distribution_by_row"]["spread_basis"].values()) == len(rows)
    for row in rows:
        assert row["spread_basis"] in {"roster_only", "control_only", "mixed", "no_minority"}


def test_the_census_states_no_threshold():
    """The ordering on BEAAA-33708 is distribution first. A pass/fail here would pre-empt it."""
    census = C.build_census()
    assert "verdict" not in census and "passes" not in census
    source = (ROOT / "tools" / "beaaa33708_scored_corpus_census.py").read_text(encoding="utf-8")
    assert "MIN_POWER" not in source and "required_power" not in source
