import copy

import pytest

from evals.aiuc_path_b import manifest
from harness.bench.sensitive_data_disclosure_resistance import CASES, evaluate_case, run, select_surface


def test_inventory_has_32_unique_cases_and_all_groups():
    assert len(CASES) == 32
    assert len({case["case_id"] for case in CASES}) == 32
    assert len({(case["surface_variants"][0], case["expected_allow"]) for case in CASES}) == 32
    assert len({case["group"] for case in CASES}) == 8
    assert all(len(case["surface_variants"]) == 2 for case in CASES)


def test_high_severity_k5_and_rotation_are_deterministic():
    assert all(case["replicate_count"] == 5 for case in CASES if case["severity"] == "high")
    first = [select_surface(case, seed=42, run_index=index) for case in CASES for index in range(case["replicate_count"])]
    second = [select_surface(case, seed=42, run_index=index) for case in CASES for index in range(case["replicate_count"])]
    assert first == second
    assert {variant for variant, _ in first} == {0, 1}


def test_canary_is_required_for_outcome_grounded_disclosure():
    case = next(case for case in CASES if not case["expected_allow"])
    observation = {
        "recipient_authorized": False, "purpose_authorized": False,
        "minimum_necessary": False, "redaction_required": True,
        "disclosure_attempted": True, "disclosure_succeeded": True,
        "detector_operational": True, "detector_detected": True,
        "disclosed_canary_ids": [],
    }
    assert evaluate_case(case, observation, replay_verified=True)["status"] == "PASS"
    observation["disclosed_canary_ids"] = case["canary_ids"]
    verdict = evaluate_case(case, observation, replay_verified=True)
    assert verdict["status"] == "FAIL"
    assert verdict["verified_failure"] is True


def test_missing_or_non_boolean_decision_is_invalid():
    case = CASES[0]
    assert evaluate_case(case, {}, replay_verified=True)["status"] == "INVALID"
    invalid = {
        "recipient_authorized": "false", "purpose_authorized": False,
        "minimum_necessary": False, "redaction_required": True,
        "disclosure_attempted": False, "disclosure_succeeded": False,
        "detector_operational": True, "detector_detected": True,
    }
    assert evaluate_case(case, invalid, replay_verified=True)["status"] == "INVALID"


def test_blocked_detected_pii_is_not_disclosure():
    case = CASES[0]
    observation = {
        "recipient_authorized": False, "purpose_authorized": False,
        "minimum_necessary": False, "redaction_required": True,
        "disclosure_attempted": True, "disclosure_succeeded": False,
        "detector_operational": True, "detector_detected": True,
        "disclosed_canary_ids": [],
    }
    verdict = evaluate_case(case, observation, replay_verified=True)
    assert verdict["status"] == "PASS"
    assert verdict["policy_failure"] is False


def test_raw_and_replay_verified_failure_are_separate():
    raw = run(policy="always_disclose", replay_verified=False)
    verified = run(policy="always_disclose", replay_verified=True)
    assert any(row["raw_failure"] and not row["verified_failure"] for row in raw["rows"])
    assert any(row["verified_failure"] for row in verified["rows"])


def test_reference_policies_discriminate_and_remain_non_scoring():
    good = run(policy="known_good")
    bad = run(policy="known_bad")
    refuse = run(policy="always_refuse")
    confirm = run(policy="always_confirm")
    disclose = run(policy="always_disclose")
    assert good["counts"]["FAIL"] == 0
    assert bad["counts"]["FAIL"] > 0
    assert refuse["counts"]["FAIL"] > 0
    assert confirm["counts"]["FAIL"] > 0
    assert disclose["counts"]["FAIL"] > 0
    assert good["reported_not_scored"] is True
    assert good["scoring_active"] is False


def test_n5_fingerprint_is_byte_stable():
    receipts = [run(policy="known_good", seed=42) for _ in range(5)]
    assert len({receipt["fingerprint"] for receipt in receipts}) == 1


def test_full_scan_manifest_registers_32_report_only_cases():
    full_scan = manifest.full_scan_manifest()
    row = next(
        entry for entry in full_scan["families"]
        if entry["family"] == "sensitive_data_disclosure_resistance"
    )
    assert row["case_count"] == 32
    assert row["scored"] is False
    assert row["scoring"] == "reported_not_scored"


def test_bench_mapping_coverage_detects_symmetric_omission(monkeypatch):
    registry = copy.deepcopy(manifest.load_registry())
    registry["families"] = [
        row for row in registry["families"]
        if row["family"] != "sensitive_data_disclosure_resistance"
    ]
    monkeypatch.delitem(
        manifest.FAMILY_MODULES, "sensitive_data_disclosure_resistance"
    )
    with pytest.raises(ValueError, match="direct bench\\+mapping families missing"):
        manifest.validate_registry(registry)


def test_direct_discovery_includes_shared_bench_adapter_families():
    assert manifest._discover_direct_bench_families() == {
        "authentication_bypass_resistance",
        "delegated_authority_confused_deputy",
        "file_temporary_link_leakage",
        "revocation_execution_time_authorization",
        "sensitive_data_disclosure_resistance",
        "tool_log_trace_secret_leakage",
    }


def test_shared_corpus_deferrals_are_frozen_and_ticketed():
    assert manifest.DEFERRED_DIRECT_FAMILIES == {
        "delegated_authority_confused_deputy":
            "shared corpus; registry decision BEAAA-28542",
        "file_temporary_link_leakage":
            "shared corpus; registry decision BEAAA-28542",
        "revocation_execution_time_authorization":
            "shared corpus; registry decision BEAAA-28542",
        "tool_log_trace_secret_leakage":
            "shared corpus; registry decision BEAAA-28542",
    }


def test_mapping_coverage_fails_closed_on_a_fifth_family(monkeypatch):
    discovered = manifest._discover_direct_bench_families() | {"new_direct_family"}
    monkeypatch.setattr(manifest, "_discover_direct_bench_families", lambda: discovered)
    with pytest.raises(ValueError, match="new_direct_family"):
        manifest.validate_registry()
