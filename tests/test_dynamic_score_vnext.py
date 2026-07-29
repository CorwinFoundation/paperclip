import copy
import json
from decimal import Decimal

import pytest

from dynamic_score_vnext.reference_calculator import calculate
from dynamic_score_vnext.shadow_path import (
    ShadowPathError,
    calculator_sha256,
    rollback_shadow_sink,
    run_shadow,
)
from dynamic_score_vnext.reference_calculator import PARAMETERS_SHA256


def payload():
    units = []
    for family in ("unauthorized_call", "param_tamper", "scope_escape"):
        for idx in range(5):
            units.append(
                {
                    "family_id": family,
                    "evidence_id": f"{family}-{idx}",
                    "observed_at": "2026-07-29T00:00:00Z",
                    "valid": True,
                    "applicable": True,
                    "outcome": 1,
                    "adverse_severity": 0,
                    "reason_code": "MEASURED",
                    "evidence_refs": [f"evidence://{family}/{idx}"],
                    "applicability_rule_version": "capability-manifest@1",
                }
            )
    return {
        "as_of": "2026-07-29T00:00:00Z",
        "current_score": 80,
        "current_score_provenance": {
            "scanner_version": "0.4.1",
            "corpus_version": "2026.6.24",
            "model": "golden-agent-fixture",
            "seed": 0,
            "score_version": "backbond-score@0.5.0",
            "source_receipt_sha256": "a" * 64,
        },
        "expected_family_manifest": {
            "manifest_version": "tranche-shadow@1",
            "families": [
                {"family_id": family, "applicability_rule_version": "capability-manifest@1"}
                for family in ("unauthorized_call", "param_tamper", "scope_escape")
            ],
        },
        "calculator_code_sha256": calculator_sha256(),
        "git_commit_sha": "b" * 40,
        "parameter_config_sha256": PARAMETERS_SHA256,
        "evidence_snapshot_sha256": "c" * 64,
        "prior_shadow_band": "B",
        "evidence_units": units,
    }


def test_exact_replay_and_n5():
    first = calculate(payload())
    for _ in range(4):
        assert calculate(payload()) == first


def test_correlated_probe_volume_cannot_change_family_weight():
    base = payload()
    baseline = calculate(base)["dynamic_component"]
    for multiplier in (10, 100):
        expanded = copy.deepcopy(base)
        expanded["evidence_units"] = copy.deepcopy(base["evidence_units"]) * multiplier
        assert calculate(expanded)["dynamic_component"] == baseline


def test_stale_evidence_decays_and_fresh_recovery_is_monotonic():
    fresh = calculate(payload())
    stale_payload = payload()
    stale_payload["as_of"] = "2026-10-27T00:00:00Z"
    stale = calculate(stale_payload)
    recovered_payload = copy.deepcopy(stale_payload)
    for unit in recovered_payload["evidence_units"]:
        unit["observed_at"] = "2026-10-27T00:00:00Z"
    recovered = calculate(recovered_payload)
    assert Decimal(stale["dynamic_component"]) < Decimal(fresh["dynamic_component"])
    assert recovered["dynamic_component"] == fresh["dynamic_component"]


def test_high_severity_failure_caps_shadow_score():
    case = payload()
    case["current_score"] = 96
    case["evidence_units"][0]["outcome"] = 0
    case["evidence_units"][0]["adverse_severity"] = 4
    case["evidence_units"][0]["high_severity_failure"] = True
    result = calculate(case)
    assert result["shadow_score"] == "95.00"
    assert result["high_severity_referral"] is True


def test_not_applicable_is_neutral_and_invalid_is_not_positive():
    case = payload()
    for unit in case["evidence_units"][:5]:
        unit["applicable"] = False
        unit["reason_code"] = "CAPABILITY_ABSENT"
    for unit in case["evidence_units"][5:10]:
        unit["valid"] = False
    result = calculate(case)
    rows = {row["family_id"]: row for row in result["family_rows"]}
    assert rows["unauthorized_call"]["status"] == "not_applicable"
    assert rows["param_tamper"]["status"] == "invalid_missing"
    assert result["fail_closed"] is True


def test_shadow_path_defaults_off_and_never_mutates_public_result(tmp_path):
    public = {
        "scan_id": "scan-shadow-1",
        "are_total": 80,
        "band": "B",
        "eligibility": "eligible",
    }
    before = copy.deepcopy(public)

    assert (
        run_shadow(
            public_result=public,
            evidence_payload=payload(),
            receipt_directory=tmp_path,
            expected_calculator_sha256=calculator_sha256(),
        )
        is None
    )
    assert public == before
    assert list(tmp_path.iterdir()) == []


def test_shadow_path_is_exact_sha_bound_and_content_addressed(tmp_path):
    public = {"scan_id": "scan-shadow-1", "are_total": 80, "band": "B"}
    before = copy.deepcopy(public)
    evidence = payload()

    first = run_shadow(
        public_result=public,
        evidence_payload=evidence,
        receipt_directory=tmp_path,
        expected_calculator_sha256=calculator_sha256(),
        enabled=True,
    )
    second = run_shadow(
        public_result=public,
        evidence_payload=evidence,
        receipt_directory=tmp_path,
        expected_calculator_sha256=calculator_sha256(),
        enabled=True,
    )

    assert first == second
    assert public == before
    receipt = json.loads(first.read_text())
    assert receipt["mode"] == "shadow_only"
    assert receipt["calculator_sha256"] == calculator_sha256()
    assert receipt["public_baseline"] == {"are_total": 80, "band": "B"}
    assert receipt["shadow_output"]["shadow_score"] == "79.00"
    assert "shadow_score" not in public


def test_shadow_path_fails_closed_on_pin_or_baseline_mismatch(tmp_path):
    public = {"are_total": 80, "band": "B"}
    evidence = payload()
    evidence["current_score"] = 81

    try:
        run_shadow(
            public_result=public,
            evidence_payload=evidence,
            receipt_directory=tmp_path,
            expected_calculator_sha256="0" * 64,
            enabled=True,
        )
    except ShadowPathError as exc:
        assert "SHA-256 mismatch" in str(exc)
    else:
        raise AssertionError("mismatched calculator pin must fail")

    try:
        run_shadow(
            public_result=public,
            evidence_payload=evidence,
            receipt_directory=tmp_path,
            expected_calculator_sha256=calculator_sha256(),
            enabled=True,
        )
    except ShadowPathError as exc:
        assert "must match public are_total" in str(exc)
    else:
        raise AssertionError("mismatched baseline must fail")


def test_receipt_binds_replay_inputs_and_wholly_absent_family_fails_closed():
    base = payload()
    original = calculate(base)
    changed = copy.deepcopy(base)
    changed["evidence_units"][0]["evidence_id"] = "changed-id"
    assert calculate(changed)["receipt_sha256"] != original["receipt_sha256"]
    changed = copy.deepcopy(base)
    changed["as_of"] = "2026-07-30T00:00:00Z"
    assert calculate(changed)["receipt_sha256"] != original["receipt_sha256"]
    for field, value in (
        ("git_commit_sha", "d" * 40),
        ("evidence_snapshot_sha256", "e" * 64),
    ):
        changed = copy.deepcopy(base)
        changed[field] = value
        assert calculate(changed)["receipt_sha256"] != original["receipt_sha256"]
    changed = copy.deepcopy(base)
    changed["expected_family_manifest"]["manifest_version"] = "tranche-shadow@2"
    assert calculate(changed)["receipt_sha256"] != original["receipt_sha256"]
    changed = copy.deepcopy(base)
    changed["current_score_provenance"]["seed"] = 1
    assert calculate(changed)["receipt_sha256"] != original["receipt_sha256"]

    missing = copy.deepcopy(base)
    missing["evidence_units"] = [
        unit for unit in missing["evidence_units"] if unit["family_id"] != "scope_escape"
    ]
    result = calculate(missing)
    row = next(row for row in result["family_rows"] if row["family_id"] == "scope_escape")
    assert row["status"] == "invalid_missing"
    assert row["reason_code"] == "EXPECTED_FAMILY_ABSENT"
    assert result["fail_closed"] is True
    assert result["status_counts"]["invalid_missing"] == 1
    assert result["reason_code_counts"]["EXPECTED_FAMILY_ABSENT"] == 1


def test_one_fresh_unit_cannot_refresh_stale_family_and_severity_is_adverse_monotonic():
    stale = payload()
    stale["as_of"] = "2026-10-27T00:00:00Z"
    one_fresh = copy.deepcopy(stale)
    one_fresh["evidence_units"][0]["observed_at"] = one_fresh["as_of"]
    fully_fresh = copy.deepcopy(stale)
    for unit in fully_fresh["evidence_units"][:5]:
        unit["observed_at"] = fully_fresh["as_of"]
    assert calculate(one_fresh)["dynamic_component"] < calculate(fully_fresh)["dynamic_component"]

    mild = payload()
    mild["evidence_units"][0]["outcome"] = 0.5
    severe = copy.deepcopy(mild)
    severe["evidence_units"][0]["adverse_severity"] = 4
    assert calculate(severe)["dynamic_component"] <= calculate(mild)["dynamic_component"]


def test_absolute_movement_cap_and_public_bytes_are_identical(tmp_path):
    public = {
        "scan_id": "scan-shadow-1",
        "are_total": 80,
        "band": "B",
        "eligibility": "eligible",
        "pricing": {"risk_multiplier": "1.00"},
        "premium": {"amount": "1000.00", "currency": "USD"},
        "quote": {"status": "draft"},
        "policy": {"status": "not_bound"},
        "warranty": {"status": "inactive"},
        "underwriting_decision": {"status": "shadow_ignored"},
    }
    before = json.dumps(public, sort_keys=True, separators=(",", ":")).encode()
    receipt = run_shadow(
        public_result=public,
        evidence_payload=payload(),
        receipt_directory=tmp_path,
        expected_calculator_sha256=calculator_sha256(),
        enabled=True,
    )
    after = json.dumps(public, sort_keys=True, separators=(",", ":")).encode()
    assert before == after
    result = json.loads(receipt.read_text())["shadow_output"]
    assert abs(float(result["shadow_score"]) - public["are_total"]) <= 1.00
    assert rollback_shadow_sink(tmp_path) == 1
    assert [path for path in tmp_path.glob("*.json") if not path.name.startswith(".")] == []
    assert before == json.dumps(public, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ShadowPathError, match="rollback-disabled"):
        run_shadow(
            public_result=public,
            evidence_payload=payload(),
            receipt_directory=tmp_path,
            expected_calculator_sha256=calculator_sha256(),
            enabled=True,
        )
    assert before == json.dumps(public, sort_keys=True, separators=(",", ":")).encode()
    assert (
        run_shadow(
            public_result=public,
            evidence_payload=payload(),
            receipt_directory=tmp_path,
            expected_calculator_sha256=calculator_sha256(),
            enabled=False,
        )
        is None
    )
    assert before == json.dumps(public, sort_keys=True, separators=(",", ":")).encode()


def test_distinct_fail_closed_states_and_affirmative_na_proof():
    case = payload()
    case["evidence_units"] = [
        unit for unit in case["evidence_units"] if unit["family_id"] != "scope_escape"
    ]
    for unit in case["evidence_units"][:5]:
        unit["applicable"] = False
        unit["reason_code"] = ""
    conflict = copy.deepcopy(case["evidence_units"][5])
    conflict["outcome"] = 0
    case["evidence_units"].append(conflict)
    result = calculate(case)
    rows = {row["family_id"]: row for row in result["family_rows"]}
    assert rows["unauthorized_call"]["reason_code"] == "UNPROVEN_NOT_APPLICABLE"
    assert rows["param_tamper"]["reason_code"] == "CONFLICTING_EVIDENCE_ID"
    assert rows["scope_escape"]["reason_code"] == "EXPECTED_FAMILY_ABSENT"
    assert result["status_counts"]["unproven_not_applicable"] == 1
    assert result["status_counts"]["conflicting"] == 1
    assert result["status_counts"]["invalid_missing"] == 1
    assert rows["unauthorized_call"]["raw_probe_count"] == 5
    assert rows["unauthorized_call"]["valid_n"] == 0
    assert rows["unauthorized_call"]["invalid_n"] == 5
    assert rows["param_tamper"]["raw_probe_count"] == 6
    assert rows["param_tamper"]["valid_n"] == 0
    assert rows["param_tamper"]["invalid_n"] == 6
    assert rows["scope_escape"]["raw_probe_count"] == 0
    assert rows["scope_escape"]["valid_n"] == 0
    assert rows["scope_escape"]["invalid_n"] == 0
