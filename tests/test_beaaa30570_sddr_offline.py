from __future__ import annotations

import json
from pathlib import Path

import yaml

from harness.bench.sensitive_data_disclosure_resistance import execute_offline_fixture, validate_offline_receipt

FIXTURES = Path(__file__).parent / "fixtures/r2_rob/sensitive_data_disclosure_resistance/offline_ext_v1.json"


def _cases():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def test_thirty_two_distinct_cases_and_states_execute_against_local_policy():
    cases = _cases()
    states = [json.dumps(case["state"], sort_keys=True) for case in cases]
    assert len(cases) == len({case["case_id"] for case in cases}) == len(set(states)) == 32
    for case in cases:
        observed = execute_offline_fixture(case)
        assert observed["decision"] == case["expected"], case["case_id"]
        if case["expected"] == "permit":
            assert validate_offline_receipt(observed["receipt"], case["case_id"])
            assert observed["mock_effects"] == [{"operation": "synthetic_disclosure", "case_id": case["case_id"]}]
        else:
            assert observed["receipt"] is None
            assert observed["mock_effects"] == []


def test_catalogue_split_retains_32_registered_and_adds_32_offline_only():
    mapping = yaml.safe_load(Path("rubric/probe_mappings/sensitive_data_disclosure_resistance.yaml").read_text())
    assert mapping["corpus"]["case_count"] == 32
    assert mapping["offline_extension"] == {
        "fixture": "tests/fixtures/r2_rob/sensitive_data_disclosure_resistance/offline_ext_v1.json",
        "adapter": "harness/bench/sensitive_data_disclosure_resistance.py",
        "registered_probes": 32, "offline_cases": 32, "catalogue_cases": 64,
        "execution": "fixture_test_only",
        "limitations": "Synthetic state and mocked effects only; illustrative values are not product defaults.",
    }


def test_independent_oracle_catches_deliberately_incorrect_allow():
    invalid = next(case for case in _cases() if case["case_id"] == "SDDR-EXT-032")
    deliberately_wrong = {"decision": "permit", "receipt": {"status": "simulated"}, "mock_effects": [{}]}
    assert deliberately_wrong["decision"] != invalid["expected"]
    assert execute_offline_fixture(invalid)["decision"] == "deny"


def test_malformed_requests_and_receipts_are_harness_blocked():
    for request in ({}, {"case_id": "x", "control": "consent_lifetime", "state": {}}):
        assert execute_offline_fixture(request) == {"decision": "harness_blocked", "receipt": None, "mock_effects": []}
    assert not validate_offline_receipt(None, "SDDR-EXT-001")
    assert not validate_offline_receipt({}, "SDDR-EXT-001")
    assert not validate_offline_receipt({"case_id": "SDDR-EXT-001", "status": "simulated"}, "SDDR-EXT-001")
