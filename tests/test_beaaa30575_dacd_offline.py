from __future__ import annotations

import json
from pathlib import Path

import yaml

from harness.bench.delegated_authority_confused_deputy import execute_offline_fixture, validate_offline_receipt


FIXTURES = Path(__file__).parent / "fixtures/r2_rob/delegated_authority_confused_deputy/offline_ext_v1.json"
MAPPING = Path("rubric/probe_mappings/delegated_authority_confused_deputy.yaml")


def _cases():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def test_sixteen_distinct_cases_and_states_execute_against_local_policy():
    cases = _cases()
    states = [json.dumps(case["state"], sort_keys=True) for case in cases]
    assert len(cases) == len({case["case_id"] for case in cases}) == len(set(states)) == 16
    for case in cases:
        observed = execute_offline_fixture(case)
        assert observed["decision"] == case["expected"], case["case_id"]
        if case["expected"] == "permit":
            assert validate_offline_receipt(observed["receipt"], case["case_id"])
            assert observed["mock_effects"] == [{"operation": "synthetic_delegated_action", "case_id": case["case_id"]}]
        else:
            assert observed["receipt"] is None
            assert observed["mock_effects"] == []


def test_catalogue_has_24_unique_ids_split_8_registered_and_16_offline():
    mapping = yaml.safe_load(MAPPING.read_text(encoding="utf-8"))
    registered = [probe["id"] for probe in mapping["probes"]]
    offline = [case["case_id"] for case in _cases()]
    assert registered == [f"DACD-{index:02d}" for index in range(1, 9)]
    assert not [probe_id for probe_id in registered if probe_id.startswith("DACD-EXT")]
    assert len(registered + offline) == len(set(registered + offline)) == 24
    assert (mapping["offline_extension"]["registered_probes"], mapping["offline_extension"]["offline_cases"]) == (8, 16)


def test_independent_oracle_catches_deliberately_incorrect_allow():
    invalid = next(case for case in _cases() if case["case_id"] == "DACD-EXT-008")
    assert execute_offline_fixture(invalid)["decision"] == "deny"
    assert "permit" != invalid["expected"]


def test_malformed_requests_and_receipts_are_separate_harness_failures():
    for request in ({}, {"case_id": "x", "control": "delegation_depth", "state": {}}):
        assert execute_offline_fixture(request) == {"decision": "harness_blocked", "receipt": None, "mock_effects": []}
    assert not validate_offline_receipt(None, "DACD-EXT-001")
    assert not validate_offline_receipt({}, "DACD-EXT-001")
