"""BEAAA-33712: round 3's stop-rule read is a real field, named before the value exists.

Same discipline as BEAAA-33708's pre-registration tests, with one difference that
matters. Round 2 recorded its baseline as "the declared rounds less the round under
test", which re-derived the baseline set from ``C0_ROUNDS`` every time a round was
added. Round 3's card fixes the comparison instead: the stop rule is read against
the frozen 0.8067 and 0.805, and those two literals may never change. The pre-round-3
state of every declared round is recorded separately, so re-pooling is visible
without being able to move the thing the rule compares against.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import beaaa33706_pooled_c0_gate as P

ROOT = Path(__file__).resolve().parents[1]
PREREG = json.loads(
    (ROOT / "artifacts/beaaa-33712/stop-rule-preregistration.json").read_text(encoding="utf-8"))
FROZEN = PREREG["stop_rule_baseline_frozen_as_history"]
MEASURED = PREREG["measured_since_the_baseline_froze_as_of_master_d4fe997e"]


@pytest.fixture(scope="module")
def report() -> dict:
    return json.loads((ROOT / PREREG["read_from"]["artifact"]).read_text(encoding="utf-8"))


def test_round_3_is_the_declared_round_under_test():
    assert PREREG["round_under_test"] == "beaaa-33712-r1"
    cohort = json.loads(
        (ROOT / "artifacts/beaaa-33712/cohort-57.json").read_text(encoding="utf-8"))
    assert cohort["capture_round"] == PREREG["round_under_test"]
    assert PREREG["round_under_test"] in {r.round_id for r in P.C0_ROUNDS}


def test_the_frozen_baseline_is_the_pair_the_card_names():
    """These literals are history. A change here is a restated baseline, not a fix."""
    assert FROZEN["beaaa-33673-r1"] == 0.8067
    assert FROZEN["beaaa-33677-r1"] == 0.805
    assert sorted(v for k, v in FROZEN.items() if isinstance(v, float)) == [0.805, 0.8067]


def test_the_frozen_baseline_is_not_the_live_artifact(report):
    """The point of freezing: re-pooling already moved both of these upward.

    If this ever starts passing by equality it means the baseline was rewritten
    to the live build, which is the failure mode the freeze exists to prevent.
    """
    for round_id, frozen in ((k, v) for k, v in FROZEN.items() if isinstance(v, float)):
        live = report["per_round_yield"][round_id]["yield_under_the_pooled_gate"]
        assert live >= frozen


def test_the_pre_round_3_record_matches_what_master_committed(report):
    """Everything except the pooled-gate yield is invariant under re-pooling."""
    declared = {r.round_id for r in P.C0_ROUNDS}
    assert set(MEASURED) - {"note"} == declared - {PREREG["round_under_test"]}
    for round_id, value in ((k, v) for k, v in MEASURED.items() if k != "note"):
        live = report["per_round_yield"][round_id]
        assert value["rows"] == live["rows"]
        assert value["yield_under_its_own_gate"] == live["yield_under_its_own_gate"]
        assert "yield_under_the_pooled_gate" in live


def test_the_named_field_resolves_for_the_round_under_test(report):
    entry = report["per_round_yield"][PREREG["round_under_test"]]
    assert isinstance(entry["yield_under_the_pooled_gate"], float)
    assert isinstance(entry["yield_under_its_own_gate"], float)


def test_the_rule_does_not_read_a_rounds_own_gate():
    assert PREREG["read_from"]["field_path"].endswith("['yield_under_the_pooled_gate']")
    assert "yield_under_its_own_gate" in PREREG["read_from"]["not_read_from"]


def test_the_cohort_exhausts_and_the_control_budget_is_pre_registered():
    """The budget is 68 and the ceiling is 37; both are named before the run ends."""
    cohort = PREREG["cohort"]
    assert cohort["pool_size"] == cohort["cohort_size"] == 57
    assert cohort["exhausts_the_cohort"] is True
    budget = PREREG["control_budget"]
    assert budget["per_family"] == 4
    assert budget["drawing_families"] == 17
    assert budget["pre_registered_attempts"] == 68
    assert budget["arithmetic_ceiling_on_realized_controls"] == 37
    assert any("never topped up" in d for d in PREREG["declared_in_advance"])
