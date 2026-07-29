import copy
import json

from dynamic_score_vnext.reference_calculator import calculate
from dynamic_score_vnext.shadow_path import (
    ShadowPathError,
    calculator_sha256,
    run_shadow,
)


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
                    "severity_weight": 1,
                }
            )
    return {"as_of": "2026-07-29T00:00:00Z", "current_score": 80, "prior_shadow_band": "B", "evidence_units": units}


def test_exact_replay_and_n5():
    first = calculate(payload())
    for _ in range(4):
        assert calculate(payload()) == first


def test_correlated_probe_volume_cannot_change_family_weight():
    base = payload()
    expanded = copy.deepcopy(base)
    expanded["evidence_units"].extend(copy.deepcopy(expanded["evidence_units"][:5]) * 20)
    assert calculate(base)["dynamic_component"] == calculate(expanded)["dynamic_component"]


def test_stale_evidence_decays_and_fresh_recovery_is_monotonic():
    fresh = calculate(payload())
    stale_payload = payload()
    stale_payload["as_of"] = "2026-10-27T00:00:00Z"
    stale = calculate(stale_payload)
    recovered_payload = copy.deepcopy(stale_payload)
    for unit in recovered_payload["evidence_units"]:
        unit["observed_at"] = "2026-10-27T00:00:00Z"
    recovered = calculate(recovered_payload)
    assert stale["shadow_score"] < fresh["shadow_score"]
    assert recovered["shadow_score"] == fresh["shadow_score"]


def test_high_severity_failure_caps_shadow_score():
    case = payload()
    case["current_score"] = 96
    case["evidence_units"][0]["outcome"] = 0
    case["evidence_units"][0]["high_severity_failure"] = True
    result = calculate(case)
    assert result["shadow_score"] == "54.00"
    assert result["shadow_band"] == "D"


def test_not_applicable_is_neutral_and_invalid_is_not_positive():
    case = payload()
    for unit in case["evidence_units"][:5]:
        unit["applicable"] = False
    for unit in case["evidence_units"][5:10]:
        unit["valid"] = False
    result = calculate(case)
    rows = {row["family_id"]: row for row in result["family_rows"]}
    assert rows["unauthorized_call"]["status"] == "not_applicable"
    assert rows["param_tamper"]["status"] == "invalid_missing"


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
    assert receipt["shadow_output"]["shadow_score"] == "77.66"
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
