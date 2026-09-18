"""BEAAA-33706: the pooled C0 gate, and the four conditions it was granted under.

The stability floor itself is not re-tested here - it is imported and called, and
``tests/test_beaaa33673_zero_build_slice.py`` guards it. What is tested is what
pooling adds: the equivalence proof that has to pass BEFORE any pooling, the
fail-closed declaration of the union, that the gate can still demote, and the
figures the committed rounds actually produce.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import beaaa31092_content_slice as S
from tools import beaaa33673_zero_build_slice as Z
from tools import beaaa33706_pooled_c0_gate as P

FAMILY = "hallucination_legal"
OTHER_FAMILY = "p1_4_refusal_golden"


def _inventory_row(case_id, family_id, fingerprint=None):
    return {
        "stable_case_id": case_id,
        "family_id": family_id,
        "dedupe_sha256": f"dedupe-{case_id}",
        "semantic_behavior_fingerprint": fingerprint or f"fp-{case_id}",
        "deterministic_oracle": {"na_rule": "never PASS without applicability"},
        "applicability": {"na_evidence_required": ["capability_manifest"]},
        "provenance": {},
    }


def _evidence_row(case_id, family_id, capture_round, verdict="pass", response=None,
                  judge_digest="judge-digest", model_version="model-1"):
    return {
        "probe_id": S.CONTENT_CANDIDATE_PROBE_PREFIX + f"dedupe-{case_id}",
        "stable_case_id": case_id,
        "family_id": family_id,
        "evidence_ref": f"{capture_round}/{case_id}",
        "recorded_target_response": response or f"response for {case_id}",
        "verdict": verdict,
        "status": "complete",
        "acquisition_basis": "target-measured",
        "target_calls": 1,
        "target_id": Z.TARGET_ID,
        "model_version": model_version,
        "judge_config_digest": judge_digest,
        "capture_round": capture_round,
        "prompt_sha256": f"prompt-{case_id}",
    }


def _control(family_id, capture_round, slug, verdict, oracle_digest, bound_case_id,
             response=None, judge_digest="judge-digest"):
    return {
        "family_id": family_id,
        "verdict": verdict,
        "n": 1,
        "evidence_ref": f"{capture_round}/{slug}",
        "recorded_target_response": response or f"control response {slug}",
        "status": "complete",
        "acquisition_basis": "target-measured",
        "target_calls": 1,
        "target_id": Z.TARGET_ID,
        "judge_config_digest": judge_digest,
        "oracle_digest": oracle_digest,
        "bound_case_id": bound_case_id,
        "capture_round": capture_round,
        "case_slug": slug,
        "prompt_sha256": f"control-prompt-{slug}",
    }


def _write(path, payload, jsonl=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if jsonl:
        path.write_text("".join(json.dumps(row) + "\n" for row in payload), encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _round(tmp_path, round_id, evidence_rows, controls, inventory_path, judge_digest="judge-digest"):
    """Write one round's committed artifacts - cohort, manifest, controls, ledger."""
    base = tmp_path / round_id
    cohort = _write(base / "cohort.json",
                    {"rows": [{"stable_case_id": row["stable_case_id"]} for row in evidence_rows]})
    manifest = _write(base / "run-manifest.json", {
        "schema_version": P.CAPTURE_SCHEMA_VERSION,
        "inventory_sha256": Z.INVENTORY_SHA256,
        "target_id": Z.TARGET_ID,
        "judge_config_digest": judge_digest,
        "judge_model": "probe-only",
        "evidence_rows": evidence_rows,
    })
    controls_path = _write(base / "controls.json", controls)
    ledger_path = base / "ledger.json"
    ledger = Z.build_ledger(cohort, inventory_path, manifest, controls_path, roster_round=round_id)
    _write(ledger_path, ledger)
    return P.C0Round(round_id=round_id, issue="BEAAA-TEST", cohort=cohort, manifest=manifest,
                     controls=controls_path, ledger=ledger_path)


def _two_rounds(tmp_path, *, round_b_verdict_flip=False, shared_response=None,
                round_b_model_version="model-1"):
    """A family that clears the floor in round A alone, plus a second round."""
    inventory = []
    round_a, round_b = [], []
    # Round A: 12 rows, 3 of them the minority verdict.
    for index in range(12):
        case_id = f"a-{index:02d}"
        inventory.append(_inventory_row(case_id, FAMILY))
        verdict = "fail" if index < 3 else "pass"
        response = shared_response if (shared_response and index < 3) else None
        round_a.append(_evidence_row(case_id, FAMILY, "round-a-r1", verdict=verdict, response=response))
    # Round B: 12 more rows in the same family.
    for index in range(12):
        case_id = f"b-{index:02d}"
        inventory.append(_inventory_row(case_id, FAMILY))
        verdict = "pass"
        response = None
        if round_b_verdict_flip and index < 3:
            # Byte-identical response to round A's minority rows, opposite verdict.
            verdict, response = "pass", shared_response
        round_b.append(_evidence_row(case_id, FAMILY, "round-b-r1", verdict=verdict,
                                     response=response, model_version=round_b_model_version))
    inventory_path = _write(tmp_path / "inventory.jsonl", inventory, jsonl=True)
    oracle = S._case_oracle_digests(inventory)
    # Round A's control supplies the second round its minority needs on its own.
    controls_a = [_control(FAMILY, "round-a-r2", f"ctl-a-{index}", "fail", oracle["a-00"], "a-00",
                           response=f"control response a {index}") for index in range(3)]
    entry_a = _round(tmp_path, "round-a-r1", round_a, controls_a, inventory_path)
    entry_b = _round(tmp_path, "round-b-r1", round_b, [], inventory_path)
    return (entry_a, entry_b), inventory_path


# ------------------------------------------- condition 1: prove equivalence ---

def test_the_committed_rounds_are_one_measurement_population():
    proof = P.prove_equivalence()
    assert proof["population_equivalent"] is True
    assert proof["families_left_split"] == []
    assert all(value["explicit_judge_digest_fix_present"] for value in proof["harness_lineage"].values())
    assert all(value["same_target"] and value["same_judge_config"] and value["target_measured"]
               for value in proof["control_regime"].values())


def test_a_round_measured_under_a_different_judge_configuration_does_not_pool(tmp_path):
    (entry_a, entry_b), _ = _two_rounds(tmp_path)
    manifest = json.loads(entry_b.manifest.read_text(encoding="utf-8"))
    manifest["judge_config_digest"] = "a-different-judge"
    _write(entry_b.manifest, manifest)
    with pytest.raises(P.EquivalenceError, match="not one measurement population"):
        P.prove_equivalence((entry_a, entry_b))


def test_a_round_predating_the_explicit_judge_digest_fix_does_not_pool(tmp_path):
    (entry_a, entry_b), _ = _two_rounds(tmp_path)
    manifest = json.loads(entry_b.manifest.read_text(encoding="utf-8"))
    for row in manifest["evidence_rows"]:
        row.pop("judge_config_digest")
    _write(entry_b.manifest, manifest)
    with pytest.raises(P.EquivalenceError, match="predate the explicit judge_config_digest fix"):
        P.prove_equivalence((entry_a, entry_b))


def test_a_family_whose_identity_differs_across_rounds_is_reported_and_left_split(tmp_path):
    (entry_a, entry_b), _ = _two_rounds(tmp_path, round_b_model_version="model-2")
    proof = P.prove_equivalence((entry_a, entry_b))
    assert proof["families_left_split"] == [FAMILY]
    pooled = P.pool_inputs((entry_a, entry_b), proof["families_left_split"])
    # Left split means left OUT of the union, not pooled on an identity it does not share.
    assert pooled["cohort_ids"] == []
    assert pooled["manifest"]["evidence_rows"] == []


def test_controls_sharing_a_capture_round_across_rounds_do_not_pool(tmp_path):
    (entry_a, entry_b), inventory_path = _two_rounds(tmp_path)
    controls = json.loads(entry_a.controls.read_text(encoding="utf-8"))
    _write(entry_b.controls, [{**control, "capture_round": "round-a-r2"} for control in controls])
    with pytest.raises(P.EquivalenceError, match="control capture rounds are shared"):
        P.prove_equivalence((entry_a, entry_b))


# --------------------------------- condition 2: the union is a standing rule ---

def test_the_registry_must_name_every_c0_capture_manifest_on_master():
    assert P.assert_registry_covers_master()  # the real registry covers the real tree


def test_a_c0_capture_manifest_the_registry_does_not_name_fails_closed(tmp_path):
    (entry_a, entry_b), _ = _two_rounds(tmp_path)
    artifacts = tmp_path / "artifacts"
    for entry in (entry_a, entry_b):
        _write(artifacts / entry.round_id / "run-manifest.json",
               json.loads(entry.manifest.read_text(encoding="utf-8")))
    declared = tuple(P.C0Round(round_id=entry.round_id, issue=entry.issue, cohort=entry.cohort,
                               manifest=artifacts / entry.round_id / "run-manifest.json",
                               controls=entry.controls, ledger=entry.ledger)
                     for entry in (entry_a,))
    with pytest.raises(P.EquivalenceError, match="not declared in C0_ROUNDS"):
        P.assert_registry_covers_master(declared, artifacts_root=artifacts)


# ------------------------------------- condition 4: the declaration is closed ---

def test_a_case_the_declaration_does_not_name_fails_closed(tmp_path):
    (entry_a, entry_b), inventory_path = _two_rounds(tmp_path)
    pooled = P.pool_inputs((entry_a, entry_b))
    rounds_by_case = dict(pooled["roster_cohort"].rounds_by_case)
    rounds_by_case.pop("b-00")
    cohort = Z.RosterCohort(cohort_id="c0-test", rounds_by_case=rounds_by_case)
    with pytest.raises(ValueError, match="declares no capture round"):
        Z.build_ledger_from_objects(pooled["cohort_ids"], S._jsonl(inventory_path),
                                    pooled["manifest"], pooled["controls"], roster_round=cohort)


def test_a_row_relabelled_into_a_round_it_did_not_run_in_fails_closed(tmp_path):
    (entry_a, entry_b), inventory_path = _two_rounds(tmp_path)
    pooled = P.pool_inputs((entry_a, entry_b))
    rounds_by_case = dict(pooled["roster_cohort"].rounds_by_case)
    rounds_by_case["b-00"] = "round-a-r1"
    cohort = Z.RosterCohort(cohort_id="c0-test", rounds_by_case=rounds_by_case)
    with pytest.raises(ValueError, match="a round they did not run in"):
        Z.build_ledger_from_objects(pooled["cohort_ids"], S._jsonl(inventory_path),
                                    pooled["manifest"], pooled["controls"], roster_round=cohort)


def test_a_declared_round_carrying_no_evidence_fails_closed(tmp_path):
    (entry_a, entry_b), inventory_path = _two_rounds(tmp_path)
    pooled = P.pool_inputs((entry_a,))
    rounds_by_case = {**pooled["roster_cohort"].rounds_by_case, "ghost-case": "round-c-r1"}
    cohort = Z.RosterCohort(cohort_id="c0-test", rounds_by_case=rounds_by_case)
    with pytest.raises(ValueError, match="rounds with no evidence"):
        Z.build_ledger_from_objects(pooled["cohort_ids"], S._jsonl(inventory_path),
                                    pooled["manifest"], pooled["controls"], roster_round=cohort)


def test_a_pooled_row_observes_under_its_own_round_not_the_cohort_label(tmp_path):
    (entry_a, entry_b), inventory_path = _two_rounds(tmp_path)
    pooled = P.pool_inputs((entry_a, entry_b))
    ledger = Z.build_ledger_from_objects(pooled["cohort_ids"], S._jsonl(inventory_path),
                                         pooled["manifest"], pooled["controls"],
                                         roster_round=pooled["roster_cohort"])
    assert ledger["roster_capture_round"] == P.C0_COHORT_ID
    assert ledger["roster_capture_rounds"] == ["round-a-r1", "round-b-r1"]
    minority_rounds = {round_id for verdict, bucket in ledger["verdict_spread_gate"]["families"][FAMILY].items()
                       if verdict == "fail" for round_id in bucket["rounds"]}
    # Round A's own roster round, plus the control round that supplied its second
    # observation - the control channel keys itself under ``control:``.
    assert minority_rounds == {"round-a-r1", "control:round-a-r2"}, minority_rounds


def test_a_single_round_ledger_gains_no_pooled_header(tmp_path):
    (entry_a, _), _ = _two_rounds(tmp_path)
    ledger = json.loads(entry_a.ledger.read_text(encoding="utf-8"))
    assert ledger["roster_capture_round"] == "round-a-r1"
    assert "roster_capture_rounds" not in ledger


# ------------------------------------------ condition 3: run it symmetrically ---

def test_pooling_that_demotes_reports_the_demotion(tmp_path):
    """The demotion path is reachable, so a zero demotion count is a measurement.

    Round A clears the floor on its own. Round B then measures the same family and
    returns the SAME target responses as round A's minority rows under the opposite
    verdict, so in the union those observations cannot testify about spread. The
    family falls under the floor and round A's scored rows go held - which is the
    pooled gate doing the thing that makes it a gate rather than a promotion engine.
    """
    (entry_a, entry_b), inventory_path = _two_rounds(
        tmp_path, round_b_verdict_flip=True, shared_response="a response both rounds returned")
    proof = P.prove_equivalence((entry_a, entry_b))
    assert proof["families_left_split"] == []
    pooled = P.pool_inputs((entry_a, entry_b), proof["families_left_split"])
    ledger = Z.build_ledger_from_objects(pooled["cohort_ids"], S._jsonl(inventory_path),
                                         pooled["manifest"], pooled["controls"],
                                         roster_round=pooled["roster_cohort"])
    report = P.compare_split_to_pooled(ledger, (entry_a, entry_b))
    assert report["demotions"] > 0
    assert all(move["from"] == "scored" and move["to"] == "held_reported_not_scored"
               for move in report["demoted_rows"])
    assert all(move["to_hold_reasons"] for move in report["demoted_rows"])
    # Reported as two counts, never a net.
    assert "net" not in json.dumps(report)


# ----------------------------------------------- the committed rounds, pinned ---

def test_the_pooled_gate_over_the_committed_rounds_is_pinned():
    ledger, proof = P.build_pooled_ledger()
    report = P.compare_split_to_pooled(ledger)
    assert proof["families_left_split"] == []
    # Round 3 (BEAAA-33712) entered the registry, so these pins moved again. Every
    # superseded value is kept beside the new one: a pin restated to whatever the
    # newest build printed records nothing, and the whole point of the union
    # being a standing rule is that the movement is inspectable.
    #   350 -> 550 -> 607 rows, 282 -> 468 -> 516 scored, 68 -> 82 -> 91 held,
    #   97 -> 240 -> 282 promotions, 8 -> 11 -> 11 tier-1 families.
    #   Demotions were 0 at every round and stay 0.
    #
    # 607 SELECTED is the cohort exhausting rather than a number that happens to
    # be larger: it is exactly the zero-build family row count of the pinned
    # inventory, so every such row is now measured and in the pool. There is no
    # round 4 to add.
    #
    # EVIDENCED IS 606, NOT 607, and the one-row gap is the point of the field.
    # A single round-3 roster attempt errored; under the pre-registration it stayed
    # short with no top-up and no reselection, so it is mapped but never evidenced.
    # Pinning all three at 607 would erase the only trace in the ledger that a
    # budgeted attempt was spent and returned nothing.
    assert (ledger["selected"], ledger["mapped"], ledger["evidenced"]) == (607, 607, 606)
    assert (ledger["scored"], ledger["held"], ledger["dropped_duplicate"]) == (516, 91, 0)
    assert ledger["roster_capture_rounds"] == [
        "beaaa-33673-r1", "beaaa-33677-r1", "beaaa-33708-r1", "beaaa-33712-r1"]
    assert ledger["tier1_families"] == [
        "citation_integrity_legal",
        "confidence_calibration",
        "counterfactual_stability",
        "hallucination_legal",
        "p1_1_factuality_golden",
        "p1_3_consistency_golden",
        "p2_1_demographic_parity_gap",
        "p2_3_counterfactual_stability",
        "p2_5_sentiment_skew_by_attribute",
        "peril_data_containment",
        "self_contradiction_pairs",
    ]
    assert (report["promotions"], report["demotions"], report["other_disposition_moves"]) == (282, 0, 0)
    assert report["still_held_total"] == 91  # was 82 over three rounds
    # A THIRD hold literal appears with round 3, and it is the errored attempt:
    # ``missing_manifest_evidence`` is how a mapped-but-unevidenced row is held.
    # It is asserted here rather than allowed in by a loosened key check, so the
    # short row is visible as a literal and not as a silently absent row.
    assert report["still_held_by_literal"].keys() == {
        "missing_manifest_evidence",
        "no_verdict_spread_positive_control_missing", "verdict_spread_below_stability_floor"}
    # No row is held without a literal, on either side of the comparison.
    assert all(row["hold_reasons"] for row in ledger["rows"]
               if row["disposition"] == "held_reported_not_scored")
    # The control channel is NO LONGER unchanged by pooling, and that is the
    # point of reporting it. Over two rounds the union accepted every control its
    # own round accepted (115/115, 9/9 rejected). Round 2 cost six. Round 3 takes
    # it to eight, and the two new losses are the part worth reading: they come
    # from round 1b's capture (``beaaa-33677-r2``), not round 3's. A control
    # admitted two rounds ago can stop being independent when a later round
    # measures a roster row that returns the same response. That is control
    # independence enforced over the union as it stands, rather than frozen at
    # the moment a round happened to run, so it is pinned by its cause.
    #   accepted 176 -> 170 -> 202, rejected 15 -> 21 -> 26, lost 6 -> 8.
    channel = report["control_channel"]
    assert (channel["accepted_before"], channel["accepted_after"]) == (210, 202)
    assert (channel["rejected_before"], channel["rejected_after"]) == (18, 26)
    lost = channel["accepted_in_its_own_round_but_rejected_in_the_union"]
    assert len(lost) == 8
    assert all(record["was"] == "accepted" for record in lost)
    assert {reason for record in lost for reason in record["rejection_reasons"]} == {
        "duplicate_roster_response"}
    # No longer round 2's capture alone: rounds 1b and 2 both lose controls here.
    assert {record["capture_round"] for record in lost} == {
        "beaaa-33677-r2", "beaaa-33708-r2"}


def test_no_family_loses_tier1_by_pooling():
    ledger, _ = P.build_pooled_ledger()
    report = P.compare_split_to_pooled(ledger)
    for family, value in report["per_family"].items():
        if value["tier1_before"]:
            assert value["tier1_after"], family


def test_round_1_yield_is_reported_under_both_gates():
    ledger, _ = P.build_pooled_ledger()
    per_round = P.compare_split_to_pooled(ledger)["per_round_yield"]
    # Own-gate yields are properties of a round and never move. Pooled-gate
    # yields are properties of the union, so adding round 2 moved them:
    # 33673-r1 0.8067 -> 0.8533 and 33677-r1 0.805 -> 0.855. Both pre-round-2
    # values are written here rather than replaced, because BEAAA-33708's
    # pre-registration forbids restating the baseline to the newest build.
    assert per_round["beaaa-33677-r1"]["yield_under_its_own_gate"] == 0.435
    assert per_round["beaaa-33677-r1"]["yield_under_the_pooled_gate"] == 0.855
    assert per_round["beaaa-33673-r1"]["yield_under_its_own_gate"] == 0.6533
    assert per_round["beaaa-33673-r1"]["yield_under_the_pooled_gate"] == 0.8533
    # Round 2's read, the field its stop rule was pre-registered against.
    assert per_round["beaaa-33708-r1"]["yield_under_its_own_gate"] == 0.215
    assert per_round["beaaa-33708-r1"]["yield_under_the_pooled_gate"] == 0.845


def test_per_row_round_keying_is_what_carries_the_pooled_result(monkeypatch):
    """Non-vacuity: collapsing the union onto one label moves rows, so the keying is load-bearing.

    A pooled gate that produced the same answer however its rows were keyed would
    mean the span clause was not reading the union at all. Collapsing is only
    reachable here by disabling the declaration check, which is itself the point:
    the check refuses to observe a row under a round it did not run in.
    """
    ledger, proof = P.build_pooled_ledger()
    pooled = P.pool_inputs(P.C0_ROUNDS, proof["families_left_split"])
    collapsed_declaration = Z.RosterCohort(
        cohort_id="collapsed",
        rounds_by_case={case: "one-label" for case in pooled["roster_cohort"].rounds_by_case})
    with pytest.raises(ValueError, match="a round they did not run in"):
        Z.build_ledger_from_objects(pooled["cohort_ids"], S._jsonl(Z.PINNED_INVENTORY_PATH),
                                    pooled["manifest"], pooled["controls"],
                                    roster_round=collapsed_declaration)
    monkeypatch.setattr(Z, "_assert_roster_round_matches_manifest", lambda *args, **kwargs: None)
    collapsed = Z.build_ledger_from_objects(pooled["cohort_ids"], S._jsonl(Z.PINNED_INVENTORY_PATH),
                                            pooled["manifest"], pooled["controls"],
                                            roster_round=collapsed_declaration)
    # Was 247 scored and a 35-row gap over two rounds; 414 and 54 with round 2 in;
    # 456 and 60 with round 3. The gap has widened at every round, so per-row round
    # keying is carrying more of the result as the union grows, not less.
    assert collapsed["scored"] == 456
    assert ledger["scored"] - collapsed["scored"] == 60
    assert set(ledger["tier1_families"]) - set(collapsed["tier1_families"]) == {"counterfactual_stability"}


def test_no_tier1_minority_is_entirely_control_supplied_in_the_union():
    """Over two rounds ``p2_3_counterfactual_stability`` had no roster row on its
    minority side; round 2 supplied one, so the union has no control-only family.

    That family is why ``minority_support`` exists. It cleared on three control
    observations across two control rounds and nothing else - exactly the
    observation floor and exactly the span floor - which is permitted (BEAAA-33537
    admits controls into the tier decision, BEAAA-33611 counts control rounds) and
    invisible in a promotion count. BEAAA-33708 ruled the seven rows stay scored
    and control-only spread stays legal, so what was missing was that a reader
    could not see it. On more data it resolved itself: the flag is now False
    everywhere, and this test pins the transition rather than deleting the claim.
    The flag's own reporting path is held non-vacuous by the synthetic case below.
    """
    ledger, _ = P.build_pooled_ledger()
    support = P.minority_support(ledger)
    control_only = sorted(family for family, value in support.items()
                          if value["minority_entirely_control_supplied"])
    assert control_only == []
    # What moved: one scored roster row on the minority side, from round 2.
    value = support["p2_3_counterfactual_stability"]
    assert value["minority_verdict"] == "pass"
    minority = value["by_verdict"]["pass"]
    assert minority["scored_roster_rows"] == 1
    assert minority["roster_rounds"] == ["beaaa-33708-r1"]
    assert minority["control_rounds"] == ["control:beaaa-33673-r2",
                                          "control:beaaa-33677-r2",
                                          "control:beaaa-33708-r2"]
    # Off both floors now that round 2 is in: 5 observations over 4 rounds.
    assert value["minority_at_the_observation_floor"] is False
    assert value["minority_at_the_span_floor"] is False
    # And every tier-1 family carries roster rows on its minority side, which is
    # the stronger statement the two-round union could not make.
    for family, other in support.items():
        assert other["by_verdict"][other["minority_verdict"]]["scored_roster_rows"] > 0, family


def test_a_minority_supplied_entirely_by_controls_is_still_reported_not_buried():
    """Non-vacuity for the flag itself, independent of what the corpus happens to hold.

    The committed union no longer contains a control-only minority. The reporting
    path must not therefore go untested, or a later round that does produce one
    would convert rows with nothing saying so.
    """
    ledger = {
        "tier1_families": ["synthetic_family"],
        "verdict_spread_gate": {"families": {"synthetic_family": {
            "fail": {"count": 9, "rounds": ["r1", "r2"]},
            "pass": {"count": 3, "rounds": ["control:r1-c", "control:r2-c"]},
        }}},
        "rows": [{"disposition": "scored", "family_id": "synthetic_family",
                  "verdict": "fail", "recorded_target_response": f"refused {i}"}
                 for i in range(9)],
    }
    value = P.minority_support(ledger)["synthetic_family"]
    assert value["minority_verdict"] == "pass"
    assert value["minority_entirely_control_supplied"] is True
    assert value["minority_at_the_observation_floor"] is True
    assert value["minority_at_the_span_floor"] is True
    assert value["by_verdict"]["pass"]["scored_roster_rows"] == 0
    assert value["by_verdict"]["pass"]["roster_rounds"] == []
    # The majority side is read off the scored rows, so it stays visible too.
    assert value["by_verdict"]["fail"]["scored_roster_rows"] == 9
    assert value["by_verdict"]["fail"]["distinct_roster_responses"] == 9
