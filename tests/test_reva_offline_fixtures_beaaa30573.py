from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.bench.day1_supporting_families import evaluate_revocation_authorization
from harness.direct_family_adapter import cases_for_family

FAMILY = "revocation_execution_time_authorization"
FIXTURE = Path("tests/fixtures/r2_rob/revocation_execution_time_authorization/offline_ext_v1.json")


def offline_cases():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def test_reva_catalogue_retains_eight_and_adds_sixteen_distinct_cases():
    registered_ids = [case.case_id for case in cases_for_family(FAMILY)]
    extension_ids = [case["case_id"] for case in offline_cases()]
    assert registered_ids == [f"RETA-{number:02d}" for number in range(1, 9)]
    assert extension_ids == [f"REVA-EXT-{number:03d}" for number in range(1, 17)]
    ids = registered_ids + extension_ids
    assert len(ids) == len(set(ids)) == 24


def test_reva_extension_uses_independent_policy_oracle_and_local_adapter():
    cases = offline_cases()
    assert len(cases) == 16
    for case in cases:
        assert evaluate_revocation_authorization(case["authorization_state"]) is case["expected_allow"]


def test_reva_oracle_detects_deliberately_incorrect_policy_behavior():
    assert evaluate_revocation_authorization({"session_revoked": True}) is False
    with pytest.raises(ValueError, match="approval_deadline"):
        evaluate_revocation_authorization({"execution_time": 9})
