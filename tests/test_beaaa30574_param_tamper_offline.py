from __future__ import annotations

import json
from pathlib import Path

import yaml

from harness.bench.tool_misuse_param_tamper import execute_offline_fixture, validate_offline_receipt


FIXTURES = Path(__file__).parent / "fixtures/r2_rob/tool_misuse_param_tamper/offline_ext_v1.json"


def _cases():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def test_twenty_distinct_extension_cases_execute_against_policy():
    cases = _cases()
    assert len(cases) == 20
    assert len({case["case_id"] for case in cases}) == 20
    for case in cases:
        observed = execute_offline_fixture(case)
        assert observed["decision"] == case["expected"], case["case_id"]
        if case["expected"] == "permit":
            assert observed["receipt"] == {"case_id": case["case_id"], "status": "simulated"}
            assert observed["mock_effects"] == [
                {"operation": "synthetic_fixture_action", "case_id": case["case_id"]}
            ]
        else:
            assert observed["receipt"] is None
            assert observed["mock_effects"] == []


def test_offline_catalogue_reaches_32_cases_without_touching_the_scoring_mapping():
    mapping = yaml.safe_load(Path("rubric/probe_mappings/tool_misuse_param_tamper.yaml").read_text())
    ids = [probe["id"] for probe in mapping["probes"]]
    assert len(ids) == len(set(ids)) == 12
    assert {f"TOOL_MISUSE_PT-{index:02d}" for index in range(1, 13)} == set(ids)
    # The offline extension is additive local coverage only: 12 scored probes
    # plus 20 offline fixtures, and no fixture id leaks into the scored roster.
    case_ids = {case["case_id"] for case in _cases()}
    assert len(ids) + len(case_ids) == 32
    assert case_ids.isdisjoint(set(ids))


def test_every_case_carries_a_distinct_state():
    states = {
        json.dumps({k: v for k, v in case.items() if k != "case_id"}, sort_keys=True)
        for case in _cases()
    }
    assert len(states) == 20


def test_independent_oracle_detects_deliberately_incorrect_behavior():
    invalid = next(case for case in _cases() if case["case_id"] == "PT-EXT-004")
    deliberately_wrong = {"decision": "permit", "receipt": {"status": "simulated"}, "mock_effects": [{}]}
    assert deliberately_wrong["decision"] != invalid["expected"]
    assert execute_offline_fixture(invalid)["decision"] == "deny"


def test_empty_and_malformed_requests_are_harness_blocked():
    for request in ({}, {"control": "amount_binding", "approved": [], "final": {}}):
        assert execute_offline_fixture(request) == {
            "decision": "harness_blocked", "receipt": None, "mock_effects": []
        }


def test_empty_and_malformed_receipts_fail_validation():
    assert not validate_offline_receipt(None, "PT-EXT-001")
    assert not validate_offline_receipt({}, "PT-EXT-001")
    assert not validate_offline_receipt({"case_id": "PT-EXT-001"}, "PT-EXT-001")
    assert not validate_offline_receipt(
        {"case_id": "PT-EXT-002", "status": "simulated"}, "PT-EXT-001"
    )
    assert validate_offline_receipt(
        {"case_id": "PT-EXT-001", "status": "simulated"}, "PT-EXT-001"
    )
