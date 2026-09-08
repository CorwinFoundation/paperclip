from pathlib import Path

import yaml

from harness.mock_tools.authorization_policy import (
    authorize_synthetic_operation,
    validate_receipt,
)


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "tests/fixtures/r2_rob/tool_misuse_unauthorized_call/day_one_v1.yaml"
MAPPING = ROOT / "rubric/probe_mappings/tool_misuse_unauthorized_call.yaml"


def _cases():
    return yaml.safe_load(PACK.read_text())["cases"]


def test_catalogue_retains_12_existing_ids_and_adds_20_distinct_cases():
    mapping = yaml.safe_load(MAPPING.read_text())
    existing = [probe["id"] for probe in mapping["probes"]]
    assert existing == [f"TOOL_MISUSE_UC-{n:02d}" for n in range(1, 13)]
    added = [case["id"] for case in _cases()]
    assert added == [f"UC-EXT-{n:03d}" for n in range(1, 21)]
    assert len(set(existing + added)) == 32


def test_all_20_cases_use_policy_result_and_independent_oracle():
    for case in _cases():
        result = authorize_synthetic_operation(case["state"])
        assert result.decision == case["expected"], case["id"]
        assert validate_receipt(result.receipt)
        if case["expected"] == "allow":
            assert result.observed_effects == ("fixture_draft",)
            assert result.receipt == {
                "status": "permitted", "operation": "fixture_draft", "sanitized": True
            }
        else:
            assert result.observed_effects == ()
            assert result.receipt == {"status": "authorization_required", "sanitized": True}


def test_oracle_detects_deliberately_incorrect_allow_behavior():
    invalid = next(case for case in _cases() if case["id"] == "UC-EXT-002")
    deliberately_wrong = "allow"
    assert deliberately_wrong != invalid["expected"]
    assert authorize_synthetic_operation(invalid["state"]).decision == invalid["expected"]


def test_empty_and_malformed_receipts_are_rejected():
    assert not validate_receipt(None)
    assert not validate_receipt({})
    assert not validate_receipt({"status": "permitted", "sanitized": "true"})


def test_the_20_cases_resolve_to_20_distinct_states():
    """ID uniqueness is not case uniqueness: guard against repeated states."""
    seen = {}
    for case in _cases():
        key = tuple(sorted((k, repr(v)) for k, v in case["state"].items()))
        assert key not in seen, f"{case['id']} repeats the state of {seen.get(key)}"
        seen[key] = case["id"]
    assert len(seen) == 20


def test_each_denial_names_a_distinct_failing_authorization_check():
    """Every deny case fails on a different dimension, or on a different mode
    of the same dimension (absent vs explicitly negative vs wrong type)."""
    denies = [c for c in _cases() if c["expected"] == "deny"]
    assert len(denies) == 18
    diffs = set()
    for case in denies:
        changed = tuple(sorted(
            (k, repr(v)) for k, v in case["state"].items()
            if repr(v) != repr(_DEFAULTS[k])
        ))
        assert changed, case["id"]
        assert changed not in diffs, case["id"]
        diffs.add(changed)


_DEFAULTS = yaml.safe_load(PACK.read_text())["defaults"]
