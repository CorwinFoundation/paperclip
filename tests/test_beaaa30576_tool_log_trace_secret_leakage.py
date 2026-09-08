import json
from pathlib import Path

import yaml

from harness.mock_tools.log_trace_capture import (
    CAPTURE_PROFILES,
    PROTECTED_MARKER,
    capture_synthetic_diagnostic,
    validate_capture_receipt,
)


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "tests/fixtures/r2_rob/tool_log_trace_secret_leakage/day_one_v1.yaml"
MAPPING = ROOT / "rubric/probe_mappings/tool_log_trace_secret_leakage.yaml"


def _cases():
    return yaml.safe_load(PACK.read_text())["cases"]


def _expected(case_id: str) -> str:
    return "allow" if int(case_id.rsplit("-", 1)[1]) % 2 else "deny"


def _fingerprint(value) -> str:
    return json.dumps(value, sort_keys=True)


def test_catalogue_preserves_8_ids_and_adds_16_distinct_cases():
    mapping = yaml.safe_load(MAPPING.read_text())
    existing = [case["id"] for case in mapping.get("probes", [])]
    added = [case["id"] for case in _cases()]
    assert existing == [f"TLSL-{number:02d}" for number in range(1, 9)]
    assert added == [f"TLSL-EXT-{number:03d}" for number in range(1, 17)]
    assert len(set(existing + added)) == 24


def test_every_added_case_expands_to_a_distinct_state():
    states = [_fingerprint(case["state"]) for case in _cases()]
    assert len(set(states)) == len(states) == 16


def test_clean_cases_cover_every_reviewed_capture_profile():
    clean = [case for case in _cases() if _expected(case["id"]) == "allow"]
    profiles = [case["state"]["capture_profile"] for case in clean]
    assert sorted(profiles) == sorted(CAPTURE_PROFILES)
    records = [capture_synthetic_diagnostic(case["state"]).captured_records[0] for case in clean]
    assert len({_fingerprint(record) for record in records}) == len(records)


def test_unreviewed_capture_profile_fails_closed():
    state = dict(_cases()[0]["state"], capture_profile="not_a_reviewed_profile")
    result = capture_synthetic_diagnostic(state)
    assert result.decision == "deny"
    assert result.receipt["status"] == "unsupported_capture_profile"
    assert result.observed_effects == result.captured_records == ()
    assert validate_capture_receipt(result.receipt)


def test_cases_exercise_capture_with_independent_oracle_and_sanitized_state():
    for case in _cases():
        result = capture_synthetic_diagnostic(case["state"])
        assert result.decision == _expected(case["id"]), case["id"]
        assert validate_capture_receipt(result.receipt)
        assert PROTECTED_MARKER not in repr(result)
        if result.decision == "allow":
            assert result.observed_effects == ("append_mock_capture",)
            assert len(result.captured_records) == 1
        else:
            assert result.observed_effects == result.captured_records == ()


def test_oracle_detects_incorrect_allow_unauthorized_effect_and_overblocking():
    invalid = _cases()[1]
    assert _expected(invalid["id"]) == "deny"
    assert capture_synthetic_diagnostic(invalid["state"]).decision == "deny"
    deliberately_wrong = {"decision": "allow", "effects": ("append_mock_capture",)}
    assert deliberately_wrong["decision"] != _expected(invalid["id"])
    valid = _cases()[0]
    assert capture_synthetic_diagnostic(valid["state"]).decision == "allow"
    assert "deny" != _expected(valid["id"])


def test_empty_and_malformed_receipts_are_harness_invalid_not_family_cases():
    assert not validate_capture_receipt(None)
    assert not validate_capture_receipt({})
    assert not validate_capture_receipt({"status": "captured", "sanitized": "true"})
    assert not validate_capture_receipt({"status": "captured", "sanitized": True})
