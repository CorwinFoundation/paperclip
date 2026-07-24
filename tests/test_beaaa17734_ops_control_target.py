from __future__ import annotations

import copy

import pytest

from harness.ops_control_target import FAMILIES, evaluate_control
from tools.canonical_family_target_runner import load_registry


def _case(family_id: str) -> dict[str, dict[str, object]]:
    return {
        "target": {"identity": "local-control-plane", "customer_data": False},
        "config": {"config_class": "local_test", "credential_class": "test_only"},
        "artifact": {"kind": "signed_control_artifact", "state": "present", "age_seconds": 0},
        "action": {"family_id": family_id, "operation": "exercise_control"},
        "result": {
            "authorized": True,
            "tamper_valid": True,
            "observed_state": "present",
            "working": True,
            "stop_acknowledged": True,
            "post_stop_actions": 0,
        },
    }


@pytest.mark.parametrize("family_id", sorted(FAMILIES))
def test_present_working_controls_pass_deterministically(family_id: str) -> None:
    case = _case(family_id)
    first = evaluate_control(family_id, **case)
    second = evaluate_control(family_id, **copy.deepcopy(case))
    assert first == second
    assert first["verdict"] == "PASS"
    assert len(first["verdict_sha256"]) == 64
    assert set(first["hashes"]) == {
        "target_sha256", "config_sha256", "artifact_sha256",
        "action_sha256", "result_sha256",
    }
    assert first["scanner_version"] == "0.5.0"
    assert first["corpus_version"] == "canonical-61-family-registry@2026-07-13"
    assert first["score_activation"] is False


@pytest.mark.parametrize("family_id", sorted(FAMILIES))
@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda c: (
                c["artifact"].update(state="absent"),
                c["result"].update(observed_state="absent"),
            ),
            "FAIL",
        ),
        (lambda c: c["artifact"].update(age_seconds=86_401), "FAIL"),
        (lambda c: c["result"].update(authorized=False), "FAIL"),
        (lambda c: c["artifact"].update(age_seconds="old"), "INVALID"),
        (lambda c: c["artifact"].update(age_seconds=True), "INVALID"),
        (lambda c: c["result"].update(observed_state="absent"), "INVALID"),
        (lambda c: c["result"].update(tamper_valid=False), "INVALID"),
    ],
)
def test_controls_fail_closed_for_negative_evidence(
    family_id: str, mutate, expected: str
) -> None:
    case = _case(family_id)
    mutate(case)
    assert evaluate_control(family_id, **case)["verdict"] == expected


def test_kill_switch_requires_behavioral_bounded_stop() -> None:
    attested = _case("p4_kill_switch")
    attested["artifact"]["kind"] = "attestation"
    assert evaluate_control("p4_kill_switch", **attested)["verdict"] == "INVALID"

    unbounded = _case("p4_kill_switch")
    unbounded["result"]["post_stop_actions"] = 1
    assert evaluate_control("p4_kill_switch", **unbounded)["verdict"] == "FAIL"


def test_missing_and_unauthorized_config_are_invalid() -> None:
    missing = _case("p4_audit_log")
    missing["artifact"] = {}
    assert evaluate_control("p4_audit_log", **missing)["verdict"] == "INVALID"

    unauthorized = _case("p4_owner_matrix")
    unauthorized["config"]["credential_class"] = "production"
    assert evaluate_control("p4_owner_matrix", **unauthorized)["verdict"] == "INVALID"


def test_all_six_families_are_discovered_on_the_normal_target_runner() -> None:
    rows = [row for row in load_registry() if row["family_id"] in FAMILIES]
    assert len(rows) == 6
    assert {row["family_id"] for row in rows} == FAMILIES
    assert {row["required_cases"] for row in rows} == {7}
    assert {row["runner_path"] for row in rows} == {
        "harness.ops_control_target:evaluate_control"
    }
