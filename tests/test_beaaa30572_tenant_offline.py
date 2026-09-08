import json
from pathlib import Path

from harness.bench.cross_customer_data_exposure import execute_offline_fixture, validate_offline_receipt


FIXTURE = Path("tests/fixtures/r2_rob/cross_customer_data_exposure/offline_ext_v1.json")


def test_executes_16_distinct_offline_cases_with_split_catalogue():
    cases = json.loads(FIXTURE.read_text())
    assert len(cases) == 16
    assert len({case["case_id"] for case in cases}) == 16
    assert len({json.dumps(case["state"], sort_keys=True) for case in cases}) == 16
    assert {case["expected"] for case in cases} == {"permit", "deny"}
    assert sum(case["expected"] == "permit" for case in cases) == 8
    assert sum(case["expected"] == "deny" for case in cases) == 8

    for case in cases:
        observed = execute_offline_fixture(case)
        assert observed["decision"] == case["expected"]
        if case["expected"] == "permit":
            assert validate_offline_receipt(observed["receipt"], case["case_id"])
            assert observed["mock_effects"] == [{"operation": "synthetic_tenant_operation", "case_id": case["case_id"]}]
        else:
            assert observed["receipt"] is None
            assert observed["mock_effects"] == []


def test_incorrect_allow_and_malformed_receipts_fail_the_oracle():
    invalid_case = json.loads(FIXTURE.read_text())[1]
    incorrect = {"decision": "permit", "receipt": {}, "mock_effects": [{"operation": "synthetic_tenant_operation"}]}
    assert incorrect["decision"] != invalid_case["expected"]
    assert not validate_offline_receipt(incorrect["receipt"], invalid_case["case_id"])
    assert not validate_offline_receipt(None, invalid_case["case_id"])


def test_malformed_request_is_harness_blocked_without_effects():
    assert execute_offline_fixture({"case_id": "TENANT-MALFORMED", "control": "search_results", "state": None}) == {
        "decision": "harness_blocked",
        "receipt": None,
        "mock_effects": [],
    }
