"""BEAAA-34042: the round-2 pool converts the cac family without moving the floor."""
import json
from pathlib import Path

import pytest

from tools import beaaa34042_pooled_rank2_gate as G
from tools.beaaa31092_content_slice import (
    BELOW_STABILITY_FLOOR, MIN_MINORITY_OBSERVATIONS, MIN_MINORITY_SPAN, MIN_SPREAD_OBSERVATIONS)

REQUIRED = ("evidence_ref", "recorded_target_response", "verdict", "rubric_point_id")


@pytest.fixture(scope="module")
def pooled():
    return G.build_pooled_ledger()


def test_the_floor_itself_is_untouched():
    """BEAAA-33515 stands. The card converts rows by measuring again, not by lowering a bar."""
    assert (MIN_SPREAD_OBSERVATIONS, MIN_MINORITY_OBSERVATIONS, MIN_MINORITY_SPAN) == (10, 3, 2)


def test_the_span_clause_is_what_the_second_round_bought(pooled):
    """The only clause round 1 missed was the span, and pooling is what supplies it."""
    mix = pooled["ledger"]["verdict_spread_gate"]["families"][G.FAMILY]
    assert sorted(mix["pass"]["rounds"]) == ["beaaa-34042-r2", "e1332067-ac21-4711-9462-8abeeb0e2254"]
    assert len(mix["pass"]["rounds"]) >= MIN_MINORITY_SPAN
    # Pass is the minority and clears the observation clause on the pooled count.
    assert mix["pass"]["count"] < mix["fail"]["count"]
    assert mix["pass"]["count"] >= MIN_MINORITY_OBSERVATIONS
    assert mix["pass"]["count"] + mix["fail"]["count"] >= MIN_SPREAD_OBSERVATIONS
    assert G.FAMILY in pooled["ledger"]["tier1_families"]


def test_round_one_observations_are_kept_exactly_as_captured(pooled):
    """Additive only: pooling never rewrites a round-1 row, it only adds beside it."""
    committed = json.loads(Path(G.LEDGER).read_text(encoding="utf-8"))
    before = {row["stable_case_id"]: row for row in committed["rows"] if row["family_id"] == G.FAMILY}
    after = [row for row in pooled["ledger"]["rows"]
             if row["family_id"] == G.FAMILY and row["evidence_ref"].startswith("evidence://")]
    assert len(after) == len(before)
    for row in after:
        # Disposition is the gate's to change; the measurement is not.
        for field in ("verdict", "recorded_target_response", "evidence_ref", "dedupe_sha256"):
            assert row[field] == before[row["stable_case_id"]][field]


def test_every_newly_scored_row_carries_the_beaaa21728_required_fields(pooled):
    """No row reaches scored without evidence_ref, response, verdict and rubric point."""
    scored = [row for row in pooled["ledger"]["rows"] if row["disposition"] == "scored"]
    assert scored, "the pool is supposed to score rows"
    for row in scored:
        for field in REQUIRED:
            assert row.get(field) not in (None, ""), f"{row['stable_case_id']} missing {field}"


def test_every_held_row_still_carries_a_literal_reason(pooled):
    for row in pooled["ledger"]["rows"]:
        if row["disposition"] == "held_reported_not_scored":
            assert row["hold_reasons"], row["stable_case_id"]


def test_cac_04_is_out_of_scope_and_stays_held(pooled):
    """It holds unsupported_verdict, which no amount of re-measuring the target fixes."""
    row = next(r for r in pooled["ledger"]["rows"] if r["stable_case_id"] == "cac-04")
    assert row["disposition"] == "held_reported_not_scored"
    assert row["hold_reasons"] == ["unsupported_verdict"]


def test_the_pool_is_exactly_the_fourteen_rows_the_card_named(pooled):
    committed = json.loads(Path(G.LEDGER).read_text(encoding="utf-8"))
    named = [row["stable_case_id"] for row in committed["rows"]
             if row["family_id"] == G.FAMILY and row["hold_reasons"] == [BELOW_STABILITY_FLOOR]]
    added = [row["stable_case_id"] for row in pooled["ledger"]["rows"]
             if row["family_id"] == G.FAMILY and not row["evidence_ref"].startswith("evidence://")]
    assert pooled["round2_rows"] == 14
    assert sorted(added) == sorted(named)


def test_a_round2_row_observes_under_round2_not_the_round1_job(pooled):
    """The derivation is what keeps the two rounds two rounds."""
    from tools.beaaa31092_content_slice import _capture_round_for

    row = next(r for r in pooled["ledger"]["rows"]
               if r["family_id"] == G.FAMILY and not r["evidence_ref"].startswith("evidence://"))
    assert _capture_round_for(row, "caller-said-this") == "beaaa-34042-r2"


def test_an_errored_capture_is_not_an_observation(tmp_path):
    """A row that errors stays errored; it does not enter the pool as a silent absence."""
    committed = json.loads(Path(G.LEDGER).read_text(encoding="utf-8"))
    log = tmp_path / "log.jsonl"
    log.write_text(json.dumps({
        "stable_case_id": "cac-01", "family_id": G.FAMILY, "capture_round": "beaaa-34042-r2",
        "verdict": None, "status": "incomplete", "incomplete_reason": "target_call_failed"}) + "\n",
        encoding="utf-8")
    assert G.round2_rows(committed, log) == []
