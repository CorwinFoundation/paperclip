"""BEAAA-33708: the stop rule's read is a real field, named before the value exists.

A pre-registration that names a field which does not exist, or records a baseline
that does not match the artifact it was copied from, is worse than none: it reads
as a commitment while leaving the actual choice open. These tests are what make
the file binding.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import beaaa33706_pooled_c0_gate as P

ROOT = Path(__file__).resolve().parents[1]
PREREG = json.loads((ROOT / "artifacts/beaaa-33708/stop-rule-preregistration.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report() -> dict:
    return json.loads((ROOT / PREREG["read_from"]["artifact"]).read_text(encoding="utf-8"))


def baseline_rounds() -> set[str]:
    """The rounds the recorded baseline actually covers.

    The baseline artifact was frozen before the round under test was measured, so
    it cannot contain an entry for that round and must not be expected to. Demanding
    one would force either a fabricated pre-round value or a baseline restated to
    whatever the new run produced - the exact silent-restatement the pre-registration
    exists to prevent. The round under test still has to be declared; it is excluded
    from the baseline, not from the registry.

    CHANGED BY BEAAA-33712. This used to return ``declared - {under_test}``, deriving
    the baseline set from the registry. That is right for exactly as long as the
    registry holds only the round under test and the rounds the baseline froze over.
    Round 3 declared a fourth round, so the derived set gained ``beaaa-33712-r1`` and
    the equality below started failing - not because round 2's frozen record is wrong,
    but because a record frozen in the past cannot grow to cover rounds that ran after
    it. The set is now read from the frozen record itself and only *checked* against
    the registry. The two properties that were load-bearing still hold and are still
    asserted: every baseline round is declared, and the round under test is not one
    of them.
    """
    declared = {r.round_id for r in P.C0_ROUNDS}
    under_test = PREREG["round_under_test"]
    assert under_test in declared, "the round under test must be declared in C0_ROUNDS"
    recorded = set(PREREG["baseline_as_of_master_3ab128ee"])
    assert recorded <= declared - {under_test}, (
        "a baseline round must be declared in C0_ROUNDS and cannot be the round under test")
    return recorded


def test_the_named_field_exists_for_every_baseline_round(report):
    rounds = baseline_rounds()
    assert rounds, "a baseline over no rounds pins nothing"
    for round_id in rounds:
        entry = report["per_round_yield"][round_id]
        assert "yield_under_the_pooled_gate" in entry
        assert "yield_under_its_own_gate" in entry


def test_the_recorded_baseline_covers_the_baseline_rounds_and_is_never_rewritten(report):
    """The baseline is a frozen record, so it is pinned against itself, not the artifact.

    It was written while round 2's capture was still running and matched the
    then-committed artifact exactly. Adding round 2 to ``C0_ROUNDS`` re-pools the
    whole population, which the pre-registration's own second declared clause said
    in advance would move rounds 1a and 1b's pooled-gate yields. So an equality
    against the live artifact would now fail for the one reason that is legitimate,
    and the two ways to make it pass again are both wrong: restate the baseline to
    the new build, or refuse to re-pool. The baseline is therefore asserted as
    history - these literals may never change - while the live artifact is only
    required to still report both fields for every baseline round.
    """
    recorded = PREREG["baseline_as_of_master_3ab128ee"]
    assert set(recorded) == baseline_rounds()
    assert recorded["beaaa-33673-r1"] == {
        "rows": 150, "yield_under_its_own_gate": 0.6533, "yield_under_the_pooled_gate": 0.8067}
    assert recorded["beaaa-33677-r1"] == {
        "rows": 200, "yield_under_its_own_gate": 0.435, "yield_under_the_pooled_gate": 0.805}
    for round_id, value in recorded.items():
        live = report["per_round_yield"][round_id]
        # Row counts are properties of a round and cannot move by pooling.
        assert value["rows"] == live["rows"]
        # Neither can a round's own-gate yield; only the pooled-gate one re-pools.
        assert value["yield_under_its_own_gate"] == live["yield_under_its_own_gate"]
        assert "yield_under_the_pooled_gate" in live


def test_the_baseline_is_the_pair_the_issue_names():
    # 80.7 and 80.5 under the pooled gate; 65.3 and 43.5 under each round's own.
    # The second pair is recorded precisely because 43.5 is the number that caused
    # a false stop, and a reader should be able to see both from one artifact.
    # Read off the pre-registration rather than the live report: the file is frozen,
    # the report re-pools whenever a round is declared, and it is the frozen pair
    # the stop rule is compared against.
    recorded = PREREG["baseline_as_of_master_3ab128ee"].values()
    assert sorted(v["yield_under_the_pooled_gate"] for v in recorded) == [0.805, 0.8067]
    assert sorted(v["yield_under_its_own_gate"] for v in recorded) == [0.435, 0.6533]


def test_the_live_artifact_carries_the_field_the_rule_reads_for_the_round_under_test(report):
    """Once the round under test is pooled, the pre-registered path must resolve."""
    entry = report["per_round_yield"][PREREG["round_under_test"]]
    assert isinstance(entry["yield_under_the_pooled_gate"], float)
    assert isinstance(entry["yield_under_its_own_gate"], float)


def test_round_2_is_the_declared_round_under_test():
    assert PREREG["round_under_test"] == "beaaa-33708-r1"
    cohort = json.loads((ROOT / "artifacts/beaaa-33708/cohort-200.json").read_text(encoding="utf-8"))
    assert cohort["capture_round"] == PREREG["round_under_test"]


def test_the_rule_does_not_read_a_rounds_own_gate():
    assert PREREG["read_from"]["field_path"].endswith("['yield_under_the_pooled_gate']")
    assert "yield_under_its_own_gate" in PREREG["read_from"]["not_read_from"]
