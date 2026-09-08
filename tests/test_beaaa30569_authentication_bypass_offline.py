from __future__ import annotations

import json
from pathlib import Path

import yaml

from harness.bench.authentication_bypass_resistance import execute_offline_fixture, validate_offline_receipt


FIXTURES = Path(__file__).parent / "fixtures/r2_rob/authentication_bypass_resistance/offline_ext_v1.json"


def _cases():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def test_thirty_two_distinct_cases_and_states_execute_against_local_policy():
    cases = _cases()
    assert len(cases) == len({case["case_id"] for case in cases}) == 32
    assert len({json.dumps(case["state"], sort_keys=True) for case in cases}) == 32
    for case in cases:
        observed = execute_offline_fixture(case)
        assert observed["decision"] == case["expected"], case["case_id"]
        if case["expected"] == "permit":
            assert validate_offline_receipt(observed["receipt"], case["case_id"])
            assert observed["mock_effects"] == [{"operation": "synthetic_authorized_action", "case_id": case["case_id"]}]
        else:
            assert observed["receipt"] is None
            assert observed["mock_effects"] == []


def test_catalogue_has_64_unique_ids_split_32_registered_and_32_offline():
    mapping = yaml.safe_load(Path("rubric/probe_mappings/authentication_bypass_resistance.yaml").read_text())
    from evals.aiuc_path_b.authentication_bypass_resistance import SPEC

    registered = [case.case_id for case in SPEC.cases]
    offline = [case["case_id"] for case in _cases()]
    assert registered == [f"abr-{index:02d}" for index in range(1, 33)]
    assert len(registered + offline) == len(set(registered + offline)) == 64
    assert mapping["offline_extension"]["offline_cases"] == 32
    assert mapping["offline_extension"]["registered_cases"] == 32


def test_malformed_requests_and_receipts_fail_separately():
    for request in ({}, {"case_id": "x", "control": "session_lifetime", "state": {}}):
        assert execute_offline_fixture(request) == {"decision": "harness_blocked", "receipt": None, "mock_effects": []}
    assert not validate_offline_receipt(None, "ABR-EXT-001")
    assert not validate_offline_receipt({}, "ABR-EXT-001")
