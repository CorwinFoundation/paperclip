"""BEAAA-33810: agent-escape incident tranche on out_of_scope_containment.

Source incident: a model under evaluation left its testing environment and
reached third-party model-host infrastructure (BEAAA-33781, 2026-09-18).  The
tranche is additive to an existing family under an existing rubric point and
stays reported-not-scored, so it must never move a scored count.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from harness.bench.aiuc_path_b_lane_a import (
    PACKS,
    embedded_cases,
    incident_cases,
    run,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "out_of_scope_containment_agent_escape"
FAMILY = "out_of_scope_containment"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def test_tranche_covers_the_four_incident_shapes_and_is_appended_not_substituted():
    tranche = incident_cases(FAMILY)
    assert len(tranche) == 8
    assert {case["shape"] for case in tranche} == {
        "external_host_reach",
        "third_party_credential",
        "artifact_egress",
        "environment_expansion",
    }
    assert all(case["incident_ref"] == "BEAAA-33810" for case in tranche)

    cases = embedded_cases(FAMILY)
    assert len(cases) == PACKS[FAMILY].count == 38
    assert [case["case_id"] for case in cases[:30]] == [
        f"OOSC-{index:02d}" for index in range(1, 31)
    ]
    assert [case["case_id"] for case in cases[30:]] == [
        f"OOSC-INC-{index:02d}" for index in range(1, 9)
    ]
    assert len({case["case_id"] for case in cases}) == 38


def test_case_count_is_refrozen_in_both_registries():
    registry = json.loads((ROOT / "config/aiuc_path_b_lane_a_registry.json").read_text())
    row = {entry["family"]: entry for entry in registry["families"]}[FAMILY]
    mapping = yaml.safe_load((ROOT / "rubric/probe_mappings" / f"{FAMILY}.yaml").read_text())
    assert row["case_count"] == mapping["case_count"] == 38
    assert row["incident_tranche_ref"] == mapping["incident_tranche_ref"] == "BEAAA-33810"


def test_known_good_fixture_passes_every_incident_case():
    fixture = _fixture("known_good")
    result = run(FAMILY, fixture["responses"])
    incident_ids = {case["case_id"] for case in incident_cases(FAMILY)}
    rows = [row for row in result["results"] if row["case_id"] in incident_ids]
    assert len(rows) == 8
    assert all(row["passed"] for row in rows)


def test_known_bad_fixture_fails_every_incident_case():
    fixture = _fixture("known_bad")
    result = run(FAMILY, fixture["responses"])
    incident_ids = {case["case_id"] for case in incident_cases(FAMILY)}
    rows = [row for row in result["results"] if row["case_id"] in incident_ids]
    assert len(rows) == 8
    assert not any(row["passed"] for row in rows)


def test_tranche_cannot_award_score_and_judging_is_pinned():
    fixture = _fixture("known_good")
    result = run(FAMILY, fixture["responses"])
    assert result["scoring_active"] is False
    assert result["reported_not_scored"] is True
    mapping = yaml.safe_load((ROOT / "rubric/probe_mappings" / f"{FAMILY}.yaml").read_text())
    assert mapping["scoring_active"] is False
    assert mapping["status"] == "reported-not-scored"
    for name in ("known_good", "known_bad"):
        judge = _fixture(name)["judge"]
        assert judge["mode"] == "deterministic_label_equality"
        assert judge["temperature"] == 0
        assert judge["seed"] == 0
        assert judge["model"] is None


def test_n5_replication_is_byte_identical():
    fixture = _fixture("known_good")
    outputs = {
        json.dumps(run(FAMILY, fixture["responses"]), sort_keys=True) for _ in range(5)
    }
    assert len(outputs) == 1
