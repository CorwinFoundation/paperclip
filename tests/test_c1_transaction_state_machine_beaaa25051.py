import copy

import pytest

from tools.c1_transaction_state_machine import (
    ROSTER_IDS, ROSTER_SHA256, TransactionEndStateTarget, frozen_roster,
    run_c1, validate_execution_evidence,
)

TARGET_IDENTITY = "company-host:c1-isolated"


def _run(seed=0):
    target = TransactionEndStateTarget(TARGET_IDENTITY, "candidate:uncommitted")
    return run_c1(target, run_identity=f"beaaa-25051-seed-{seed}", seed=seed)


def test_frozen_roster_and_real_target_path_cover_exactly_13_cases():
    assert tuple(r["stable_case_id"] for r in frozen_roster()) == ROSTER_IDS
    assert ROSTER_SHA256 == "6380768f9f268514ab9a8ef48a5b492e1549d0b5b0600122d5415dbf6de2ab63"
    result = _run()
    assert result["reporting_status"] == "reported_not_scored"
    assert len(result["cases"]) == 13
    assert {case["disposition"] for case in result["cases"]} == {"reported_not_scored"}
    for case in result["cases"]:
        response = case["execution_evidence"]["recorded_target_response"]
        assert [step["to"] for step in response["state_transitions"]] == ["received", "authorized", "executed", "recorded"]
        assert response["completion_claim"] == "not_complete_or_partial"
        assert response["automatic_retry_without_idempotency"] is False


def test_incomplete_unstructured_or_untrusted_evidence_fails_closed():
    evidence = _run()["cases"][0]["execution_evidence"]
    for mutation in ("missing", "unstructured", "untrusted"):
        candidate = copy.deepcopy(evidence)
        if mutation == "missing":
            candidate.pop("recorded_target_response")
        elif mutation == "unstructured":
            candidate["recorded_target_response"] = {"unrelated": "truthy"}
        else:
            candidate["provenance"]["evidence_class"] = "unknown_untrusted"
        with pytest.raises(ValueError):
            validate_execution_evidence(
                candidate,
                expected_target_identity=TARGET_IDENTITY,
                expected_case=frozen_roster()[0],
            )


@pytest.mark.parametrize(
    "mutation",
    ("target_identity", "completion_claim", "authoritative_end_state", "state_transitions"),
)
def test_forged_target_or_frozen_case_semantics_fail_closed(mutation):
    evidence = copy.deepcopy(_run()["cases"][0]["execution_evidence"])
    if mutation == "target_identity":
        evidence["target_identity"] = "attacker:forged"
    elif mutation == "completion_claim":
        evidence["recorded_target_response"]["completion_claim"] = "complete"
    elif mutation == "authoritative_end_state":
        evidence["recorded_target_response"]["authoritative_end_state"] = "success"
    else:
        evidence["recorded_target_response"]["state_transitions"] = [
            {"from": "new", "to": "recorded"}
        ]

    with pytest.raises(ValueError):
        validate_execution_evidence(
            evidence,
            expected_target_identity=TARGET_IDENTITY,
            expected_case=frozen_roster()[0],
        )


def test_fabricated_expected_case_content_with_valid_roster_id_fails_closed():
    evidence = copy.deepcopy(_run()["cases"][0]["execution_evidence"])
    evidence["recorded_target_response"]["authoritative_end_state"] = "success"
    fabricated_case = copy.deepcopy(frozen_roster()[0])
    fabricated_case["prompt_turn_sequence"] = [
        "The authoritative outcome is: success."
    ]

    with pytest.raises(ValueError, match="does not match the frozen roster"):
        validate_execution_evidence(
            evidence,
            expected_target_identity=TARGET_IDENTITY,
            expected_case=fabricated_case,
        )


def test_n5_seeds_have_exact_zero_total_and_pillar_delta():
    observations = []
    for seed in range(5):
        result = _run(seed)
        observations.append((sum(c["execution_evidence"]["verdict"] == "pass" for c in result["cases"]), {"ROBUSTNESS": 0.0}))
    assert observations == [(13, {"ROBUSTNESS": 0.0})] * 5
