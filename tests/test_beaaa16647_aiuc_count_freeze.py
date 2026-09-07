from __future__ import annotations

import json
from pathlib import Path

import yaml

from tools.active_test_registry import (
    AIUC_PATH_B_SCORING_ACTIVE_FAMILIES,
    AIUC_PATH_B_REPORTED_NOT_SCORED_FAMILIES,
    BEAAA_21704_REPORTED_NOT_SCORED_CASES,
    BEAAA_21704_RETAINED_RUNTIME_CASES,
    build_registry,
    count_active_executable,
    count_target_bound_runtime,
    load_published_count,
    summarize,
)


ROOT = Path(__file__).resolve().parents[1]


def test_beaaa16647_frozen_registry_counts_are_restored() -> None:
    # BEAAA-24273 adds 60 scored cases to this no-content-candidate view, so the
    # BEAAA-24445 refreeze constants move by +60. The customer-visible published
    # count is not involved: 417 is a live count that this test passes into
    # ``summarize`` as an input, not the published claim, which stays at 2,279.
    rows = build_registry(include_content_candidates=False)
    summary = summarize(rows, published_count=487)

    assert count_active_executable(rows) == 487
    assert summary["active_executable_count"] == 487
    assert count_target_bound_runtime(rows) == 509
    assert summary["target_bound_runtime_count"] == 509
    assert summary["published_active_test_count"] == 487


def test_beaaa21704_report_only_cases_keep_runtime_coverage_not_score_countability() -> None:
    rows = build_registry(include_content_candidates=False)
    cases = [
        row
        for row in rows
        if (row["test_id"], row["case_id"])
        in BEAAA_21704_REPORTED_NOT_SCORED_CASES
    ]

    assert len(cases) == 17
    assert count_active_executable(cases) == 0
    assert len(BEAAA_21704_RETAINED_RUNTIME_CASES) == 12
    assert count_target_bound_runtime(cases) == 12
    assert {row["reporting_status"] for row in cases} == {"reported_not_scored"}
    assert {row["scoring_active"] for row in cases} == {False}


def test_beaaa16647_non_promoted_aiuc_families_remain_reported_not_scored_and_non_countable() -> None:
    rows = build_registry(include_content_candidates=False)
    rows_by_family = {
        family: [row for row in rows if row["test_id"] == family]
        for family in AIUC_PATH_B_REPORTED_NOT_SCORED_FAMILIES
    }

    lane_a = {
        row["family"]
        for row in json.loads((ROOT / "config" / "aiuc_path_b_lane_a_registry.json").read_text())[
            "families"
        ]
    }
    lane_c = {
        row["family"]
        for row in yaml.safe_load(
            (ROOT / "rubric" / "aiuc_path_b" / "family_registry.yaml").read_text()
        )["families"]
    }
    direct_report_only = {
        "sensitive_data_disclosure_resistance",
        "tool_log_trace_secret_leakage",
        "delegated_authority_confused_deputy",
        "revocation_execution_time_authorization",
        "file_temporary_link_leakage",
    }
    assert AIUC_PATH_B_REPORTED_NOT_SCORED_FAMILIES == ({
        "p3_5_multiturn_jailbreak",
        "p3_6_output_attack",
        "system_prompt_extraction",
        "insecure_code_generation",
    } | lane_c | direct_report_only) - AIUC_PATH_B_SCORING_ACTIVE_FAMILIES
    assert AIUC_PATH_B_REPORTED_NOT_SCORED_FAMILIES.isdisjoint(lane_a)
    assert len(AIUC_PATH_B_REPORTED_NOT_SCORED_FAMILIES) == 13
    assert AIUC_PATH_B_SCORING_ACTIVE_FAMILIES == {
        "cross_session_data_leak",
        "consequential_action_confirmation",
    }

    for family in AIUC_PATH_B_REPORTED_NOT_SCORED_FAMILIES:
        family_rows = rows_by_family[family]
        if family in lane_c and not family_rows:
            assert family_rows == []
            continue
        assert family_rows, family
        assert {row["reporting_status"] for row in family_rows} == {"reported_not_scored"}
        assert {row["scoring_active"] for row in family_rows} == {False}
        assert count_active_executable(family_rows) == 0
        assert count_target_bound_runtime(family_rows) == 0


def test_beaaa16972_scored_wave1a_tranche_is_countable_with_zero_score_weight() -> None:
    rows = build_registry(include_content_candidates=False)
    for family in AIUC_PATH_B_SCORING_ACTIVE_FAMILIES:
        family_rows = [row for row in rows if row["test_id"] == family]
        assert family_rows, family
        assert {row["reporting_status"] for row in family_rows} == {"scoring_active"}
        assert {row["scoring_active"] for row in family_rows} == {True}
        assert count_active_executable(family_rows) == (
            20 if family == "cross_session_data_leak" else 15
        )


def test_beaaa16647_requirement_roster_matches_corrected_d407401_source() -> None:
    roster = yaml.safe_load(
        (ROOT / "artifacts" / "beaaa-16647" / "aiuc-49-requirement-roster.yaml").read_text()
    )
    expected_included_ids = {
        *(f"A{i:03d}" for i in range(1, 8)),
        *(f"B{i:03d}" for i in range(1, 10)),
        *(f"C{i:03d}" for i in range(1, 13)),
        *(f"D{i:03d}" for i in range(1, 5)),
        *(f"E{i:03d}" for i in (1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 15, 16, 17)),
        "F001",
        "F002",
    }

    assert roster["upstream_pin"] == "d407401393de5629745de6accb35e000e674043f"
    assert roster["upstream_heading_count"] == 53
    assert roster["upstream_retired_count"] == 2
    assert roster["upstream_non_retired_count"] == 51
    assert roster["active_requirement_count"] == 49
    assert len(roster["included_requirements"]) == 49
    assert {row["aiuc_id"] for row in roster["included_requirements"]} == expected_included_ids
    assert not {
        "A009",
        "A010",
        "D005",
        "D006",
        "D007",
        "D008",
        "D009",
        "D010",
    } & {row["aiuc_id"] for row in roster["included_requirements"]}
    assert roster["retired_requirements"] == [
        {"aiuc_id": "E007", "disposition": "retired_upstream"},
        {"aiuc_id": "E014", "disposition": "retired_upstream"},
    ]
    assert roster["additional_non_retired_dispositions"] == [
        {
            "aiuc_id": "A008",
            "disposition": "reported_not_scored_excluded_from_49_active_contract",
            "rationale": (
                "Non-retired upstream heading remains tracked as reported-not-scored evidence only; "
                "no silent expansion of the 49 active requirement contract."
            ),
        },
        {
            "aiuc_id": "B010",
            "disposition": "reported_not_scored_excluded_from_49_active_contract",
            "rationale": (
                "Non-retired upstream heading remains tracked as reported-not-scored evidence only; "
                "no silent expansion of the 49 active requirement contract."
            ),
        },
    ]
