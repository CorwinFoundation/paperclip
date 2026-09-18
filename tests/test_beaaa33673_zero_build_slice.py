"""BEAAA-33673: the zero-build cohort slice.

The floor itself is tested in ``test_beaaa31092_content_slice.py`` and is
imported, not restated, so nothing here re-tests the three clauses. What is
tested here is what this module actually owns: which rows it admits into the
cohort, what it refuses to bind, and the manifest it builds from a live capture.
"""
from __future__ import annotations

import json

import pytest

from tools import beaaa31092_content_slice as S
from tools import beaaa33673_zero_build_slice as Z


def _inventory_row(case_id, family_id, fingerprint=None, rubric_point=None):
    return {
        "stable_case_id": case_id,
        "family_id": family_id,
        "dedupe_sha256": f"dedupe-{case_id}",
        "semantic_behavior_fingerprint": fingerprint or f"fp-{case_id}",
        "deterministic_oracle": {"na_rule": "never PASS without applicability"},
        "applicability": {"na_evidence_required": ["capability_manifest"]},
        "provenance": {"rubric_point_id": rubric_point} if rubric_point else {},
    }


def _capture_record(case_id, family_id, verdict="pass", response=None):
    return {
        "stable_case_id": case_id,
        "family_id": family_id,
        "verdict": verdict,
        "recorded_target_response": response or f"response for {case_id}",
        "status": "complete",
        "acquisition_basis": "target-measured",
        "target_calls": 1,
        "target_id": Z.TARGET_ID,
        "capture_round": Z.ROSTER_ROUND,
        "prompt_sha256": f"prompt-{case_id}",
    }


def _write(tmp_path, name, payload, jsonl=False):
    path = tmp_path / name
    if jsonl:
        path.write_text("".join(json.dumps(row) + "\n" for row in payload), encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _cohort(tmp_path, case_ids):
    return _write(tmp_path, "cohort.json",
                  {"rows": [{"stable_case_id": case_id} for case_id in case_ids]})


# ---------------------------------------------------------------- manifest ---

def test_manifest_omits_records_that_never_reached_a_verdict(tmp_path):
    """An errored row spent its attempt and must stay absent.

    The ledger then reads ``missing_manifest_evidence`` - short, and reported
    short. Inventing an evidence row for it would present an unspent attempt as
    a measurement.
    """
    inventory = _write(tmp_path, "inv.jsonl", [
        _inventory_row("a.01", "hallucination_legal"),
        _inventory_row("a.02", "hallucination_legal"),
    ], jsonl=True)
    capture = _write(tmp_path, "cap.jsonl", [
        _capture_record("a.01", "hallucination_legal"),
        {**_capture_record("a.02", "hallucination_legal"), "verdict": None},
    ], jsonl=True)

    manifest = Z.manifest_from_capture(capture, inventory, "probe-only")

    assert [row["stable_case_id"] for row in manifest["evidence_rows"]] == ["a.01"]


def test_manifest_makes_no_fitness_claim_and_is_still_bindable(tmp_path):
    """Silence on the fitness fields is absence, not a claim of fitness.

    The 31092 manifest declares itself ``incomplete`` and is refused. This one
    says nothing about completeness because it makes no claim on it, and
    ``_assert_manifest_fit_to_bind`` must read that as absence.
    """
    inventory = _write(tmp_path, "inv.jsonl",
                       [_inventory_row("a.01", "hallucination_legal")], jsonl=True)
    capture = _write(tmp_path, "cap.jsonl",
                     [_capture_record("a.01", "hallucination_legal")], jsonl=True)

    manifest = Z.manifest_from_capture(capture, inventory, "probe-only")

    for field in ("incomplete", "analysis_status", "bind_eligibility", "acceptance_gate"):
        assert field not in manifest
    assert S._assert_manifest_fit_to_bind(manifest) is not None


def test_manifest_judge_digest_is_explicit_and_shared_by_every_row(tmp_path):
    """The explicit digest is what the control channel compares against.

    ``JUDGE_CONFIG_FIELDS`` are not populated on this capture, so without the
    explicit field the family would read
    ``family_has_no_single_judge_configuration`` and no control could be admitted.
    """
    inventory = _write(tmp_path, "inv.jsonl", [
        _inventory_row("a.01", "hallucination_legal"),
        _inventory_row("a.02", "hallucination_legal"),
    ], jsonl=True)
    capture = _write(tmp_path, "cap.jsonl", [
        _capture_record("a.01", "hallucination_legal"),
        _capture_record("a.02", "hallucination_legal"),
    ], jsonl=True)

    manifest = Z.manifest_from_capture(capture, inventory, "probe-only")
    expected = Z.judge_config_digest("probe-only")

    assert manifest["judge_config_digest"] == expected
    assert {row["judge_config_digest"] for row in manifest["evidence_rows"]} == {expected}
    assert S._family_judge_digest(manifest["evidence_rows"]) == expected


# ------------------------------------------------------------------ ledger ---

def test_cohort_case_absent_from_the_pinned_inventory_is_an_error(tmp_path):
    """A cohort id the pinned inventory does not carry is not a held row.

    It means the cohort was drawn against different bytes than the ones being
    bound, which no per-row hold reason can honestly describe.
    """
    inventory = _write(tmp_path, "inv.jsonl",
                       [_inventory_row("a.01", "hallucination_legal")], jsonl=True)
    capture = _write(tmp_path, "cap.jsonl",
                     [_capture_record("a.01", "hallucination_legal")], jsonl=True)
    manifest = _write(tmp_path, "man.json",
                      Z.manifest_from_capture(capture, inventory, "probe-only"))

    with pytest.raises(ValueError, match="not in the pinned inventory"):
        Z.build_ledger(_cohort(tmp_path, ["a.01", "ghost.01"]), inventory, manifest)


def test_cohort_family_with_no_rubric_point_is_an_error(tmp_path):
    """An unmapped family fails the build rather than scoring at a guess."""
    inventory = _write(tmp_path, "inv.jsonl",
                       [_inventory_row("x.01", "family_with_no_rubric_point")], jsonl=True)
    capture = _write(tmp_path, "cap.jsonl",
                     [_capture_record("x.01", "family_with_no_rubric_point")], jsonl=True)
    manifest = _write(tmp_path, "man.json",
                      Z.manifest_from_capture(capture, inventory, "probe-only"))

    with pytest.raises(ValueError, match="no rubric point"):
        Z.build_ledger(_cohort(tmp_path, ["x.01"]), inventory, manifest)


def test_multi_dimension_corpus_rubric_point_holds_the_row(tmp_path):
    """``P1.1/P1.2/P2.5`` is an open question, not a binding."""
    inventory = _write(tmp_path, "inv.jsonl", [
        _inventory_row("a.01", "hallucination_legal", rubric_point="P1.1/P1.2/P2.5"),
    ], jsonl=True)
    capture = _write(tmp_path, "cap.jsonl",
                     [_capture_record("a.01", "hallucination_legal")], jsonl=True)
    manifest = _write(tmp_path, "man.json",
                      Z.manifest_from_capture(capture, inventory, "probe-only"))

    ledger = Z.build_ledger(_cohort(tmp_path, ["a.01"]), inventory, manifest)
    row = ledger["rows"][0]

    assert Z.AMBIGUOUS_RUBRIC_POINT in row["hold_reasons"]
    assert row["disposition"] == "held_reported_not_scored"
    assert row["rubric_point_id"] == "P1.1/P1.2/P2.5"
    assert ledger["scored"] == 0


def test_fingerprint_claimed_outside_the_cohort_is_a_duplicate(tmp_path):
    """Dedupe is against the whole inventory, not merely the other cohort rows.

    A behaviour already claimed by any catalogue row is a duplicate however that
    row is classified, so a cohort cannot score a behaviour twice by drawing only
    one copy of it.
    """
    inventory = _write(tmp_path, "inv.jsonl", [
        _inventory_row("a.01", "hallucination_legal", fingerprint="shared"),
        _inventory_row("z.99", "confidence_calibration", fingerprint="shared"),
    ], jsonl=True)
    capture = _write(tmp_path, "cap.jsonl",
                     [_capture_record("a.01", "hallucination_legal")], jsonl=True)
    manifest = _write(tmp_path, "man.json",
                      Z.manifest_from_capture(capture, inventory, "probe-only"))

    ledger = Z.build_ledger(_cohort(tmp_path, ["a.01"]), inventory, manifest)
    row = ledger["rows"][0]

    assert row["hold_reasons"] == ["semantic_duplicate"]
    assert row["disposition"] == "dropped_duplicate"
    assert ledger["dropped_duplicate"] == 1
    assert ledger["scored"] == 0


def test_row_measured_but_absent_from_the_manifest_is_held_not_scored(tmp_path):
    inventory = _write(tmp_path, "inv.jsonl", [
        _inventory_row("a.01", "hallucination_legal"),
        _inventory_row("a.02", "hallucination_legal"),
    ], jsonl=True)
    capture = _write(tmp_path, "cap.jsonl",
                     [_capture_record("a.01", "hallucination_legal")], jsonl=True)
    manifest = _write(tmp_path, "man.json",
                      Z.manifest_from_capture(capture, inventory, "probe-only"))

    ledger = Z.build_ledger(_cohort(tmp_path, ["a.01", "a.02"]), inventory, manifest)
    held = {row["stable_case_id"]: row for row in ledger["rows"]}["a.02"]

    assert "missing_manifest_evidence" in held["hold_reasons"]
    assert held["disposition"] == "held_reported_not_scored"
    assert ledger["evidenced"] == 1


def test_a_single_roster_capture_is_one_round_however_many_cases(tmp_path):
    """``MIN_MINORITY_SPAN`` measures sampling moments, not cases.

    Every roster row carries the one round label, so a family cannot clear the
    span clause on roster rows alone.
    """
    rows = [_inventory_row(f"a.{index:02d}", "hallucination_legal") for index in range(12)]
    inventory = _write(tmp_path, "inv.jsonl", rows, jsonl=True)
    capture = _write(tmp_path, "cap.jsonl", [
        _capture_record(row["stable_case_id"], "hallucination_legal",
                        verdict="pass" if index >= 3 else "fail")
        for index, row in enumerate(rows)
    ], jsonl=True)
    manifest = _write(tmp_path, "man.json",
                      Z.manifest_from_capture(capture, inventory, "probe-only"))

    ledger = Z.build_ledger(_cohort(tmp_path, [row["stable_case_id"] for row in rows]),
                            inventory, manifest)
    observed = ledger["verdict_spread_gate"]["families"]["hallucination_legal"]

    assert observed["fail"]["rounds"] == [Z.ROSTER_ROUND]
    assert observed["pass"]["rounds"] == [Z.ROSTER_ROUND]
    assert ledger["tier1_families"] == []
    assert {reason for row in ledger["rows"] for reason in row["hold_reasons"]} == {
        S.BELOW_STABILITY_FLOOR}


def test_roster_round_label_is_carried_into_the_gate(tmp_path):
    inventory = _write(tmp_path, "inv.jsonl",
                       [_inventory_row("a.01", "hallucination_legal")], jsonl=True)
    capture = _write(tmp_path, "cap.jsonl",
                     [_capture_record("a.01", "hallucination_legal")], jsonl=True)
    manifest = _write(tmp_path, "man.json",
                      Z.manifest_from_capture(capture, inventory, "probe-only"))

    ledger = Z.build_ledger(_cohort(tmp_path, ["a.01"]), inventory, manifest,
                            roster_round="beaaa-33673-rX")

    assert ledger["roster_capture_round"] == "beaaa-33673-rX"
    assert ledger["verdict_spread_gate"]["families"]["hallucination_legal"]["pass"]["rounds"] == [
        "beaaa-33673-rX"]


def test_slice_never_prices_the_rows_it_scores(tmp_path):
    inventory = _write(tmp_path, "inv.jsonl",
                       [_inventory_row("a.01", "hallucination_legal")], jsonl=True)
    capture = _write(tmp_path, "cap.jsonl",
                     [_capture_record("a.01", "hallucination_legal")], jsonl=True)
    manifest = _write(tmp_path, "man.json",
                      Z.manifest_from_capture(capture, inventory, "probe-only"))

    ledger = Z.build_ledger(_cohort(tmp_path, ["a.01"]), inventory, manifest)

    assert ledger["underwriting_active"] is False
    assert ledger["priced_score_weight"] == 0.0
