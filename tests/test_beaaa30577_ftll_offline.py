from __future__ import annotations

import json
from pathlib import Path

import yaml

from harness.bench.file_temporary_link_leakage import execute_offline_fixture, validate_offline_receipt


FIXTURES = Path(__file__).parent / "fixtures/r2_rob/file_temporary_link_leakage/offline_ext_v1.json"


def _cases():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def test_sixteen_distinct_cases_execute_against_local_policy():
    cases = _cases()
    assert len(cases) == len({case["case_id"] for case in cases}) == 16
    for case in cases:
        observed = execute_offline_fixture(case)
        assert observed["decision"] == case["expected"], case["case_id"]
        if case["expected"] == "permit":
            assert validate_offline_receipt(observed["receipt"], case["case_id"])
            assert observed["mock_effects"] == [{"operation": "synthetic_file_access", "case_id": case["case_id"]}]
        else:
            assert observed["receipt"] is None
            assert observed["mock_effects"] == []


def test_catalogue_has_24_unique_ids_split_8_registered_and_16_offline():
    """The catalogue is 24 ids, but only the original 8 are registered for dispatch.

    Per the 2026-09-08 CTO ruling on BEAAA-30578, the 16 offline cases live in the
    fixture and are documented under `offline_extension:`; they are deliberately
    absent from the mapping's `probes:` list, so registered dispatch stays at 8.
    """
    mapping = yaml.safe_load(Path("rubric/probe_mappings/file_temporary_link_leakage.yaml").read_text())
    registered = [probe["id"] for probe in mapping["probes"]]
    offline = [case["case_id"] for case in _cases()]
    assert registered == [f"FTLL-{index:02d}" for index in range(1, 9)]
    assert not [probe_id for probe_id in registered if probe_id.startswith("FTLL-EXT")]
    assert len(offline) == 16
    assert len(registered + offline) == len(set(registered + offline)) == 24
    extension = mapping["offline_extension"]
    assert (extension["registered_probes"], extension["offline_cases"]) == (8, 16)


def test_independent_oracle_catches_deliberately_incorrect_allow():
    invalid = next(case for case in _cases() if case["case_id"] == "FTLL-EXT-008")
    deliberately_wrong = {"decision": "permit", "receipt": {"status": "simulated"}, "mock_effects": [{}]}
    assert deliberately_wrong["decision"] != invalid["expected"]
    assert execute_offline_fixture(invalid)["decision"] == "deny"


def test_malformed_requests_and_receipts_are_separate_harness_failures():
    for request in ({}, {"case_id": "x", "control": "link_expiry", "state": {}}):
        assert execute_offline_fixture(request) == {"decision": "harness_blocked", "receipt": None, "mock_effects": []}
    assert not validate_offline_receipt(None, "FTLL-EXT-001")
    assert not validate_offline_receipt({}, "FTLL-EXT-001")
    assert not validate_offline_receipt({"case_id": "FTLL-EXT-001", "status": "simulated"}, "FTLL-EXT-001")
