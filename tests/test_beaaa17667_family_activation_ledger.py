from __future__ import annotations

from tools.family_activation_ledger import (
    CANONICAL_CORPUS_VERSION,
    CONTRACTS_REFERENCED,
    FAIL_CLOSED_GAP_ORDINALS,
    NON_WORKING_STATES,
    QA_UNVERIFIED_EXCLUDED_ORDINALS,
    REQUIRED_ROW_FIELDS,
    build_baseline,
    family_is_working,
    load_ledger,
)


def test_frozen_ledger_has_exactly_61_complete_unique_family_rows() -> None:
    payload = load_ledger()
    rows = payload["families"]

    assert len(rows) == 61
    assert len({row["family_id"] for row in rows}) == 61
    assert all(REQUIRED_ROW_FIELDS <= row.keys() for row in rows)
    assert {row["scanner_version"] for row in rows} == {"0.5.0"}
    assert all(row["corpus_version"] for row in rows)


def test_non_working_surfaces_fail_closed_and_baseline_is_truthful() -> None:
    payload = load_ledger()
    baseline = build_baseline(payload)

    for row in payload["families"]:
        if row["current_state"] in NON_WORKING_STATES:
            assert not family_is_working(row)
    assert baseline["working_count"] == len(baseline["working_family_ids"])
    assert baseline["working_count"] == len(baseline["working_ordinals"])
    assert baseline["total_count"] == 61
    assert len(baseline["failures"]) == 61 - baseline["working_count"]
    assert baseline["truthful_baseline"] == (
        f"{baseline['working_count']}/{baseline['total_count']}"
    )
    assert baseline["brand_backbone_version"] == "v1.6"
    assert (
        baseline["brand_backbone_sha256"]
        == "a2ffc4f919b0f71a17b9f4506ac4edf11cd37956ff494f00d7a45f0b166ea090"
    )
    assert baseline["brand_backbone_verified"] is True
    assert baseline["target"]["paid_call_budget"] == 0
    assert baseline["target"]["customer_data"] is False
    assert baseline["target"]["score_activation"] is False


def test_beaaa17670_batch_c_ordinals_are_target_bound_working() -> None:
    payload = load_ledger()
    rows = [
        row
        for row in payload["families"]
        if 41 <= row["ordinal"] <= 61
    ]

    assert len(rows) == 21
    baseline = build_baseline(payload)
    assert baseline["working_ordinals"][-21:] == list(range(41, 62))
    assert baseline["truthful_baseline"] == "50/61"
    assert all(family_is_working(row) for row in rows)
    assert all(row["real_blocker"] is None for row in rows)
    assert all(row["target_bound_smoke_result"]["paid_calls"] == 0 for row in rows)
    assert all(
        row["target_bound_smoke_result"]["customer_data"] is False for row in rows
    )
    assert all(
        row["target_bound_smoke_result"]["score_activation"] is False for row in rows
    )
    assert all(row["target_bound_smoke_result"]["evidence"]["sha256"] for row in rows)
    assert all(row["automated_tests"] for row in rows)


def test_integrated_ledger_classification_provenance_and_metadata_are_truthful() -> None:
    baseline = build_baseline(load_ledger())
    rows = baseline["failures"]
    excluded = [row for row in rows if row["classification"] == "qa_unverified_excluded"]
    gaps = [row for row in rows if row["classification"] == "fail_closed_gap"]

    assert baseline["working_count"] == 50
    assert baseline["working_ordinals"] == [
        *range(1, 10), 12, 13, 14, 15, 16, 17, 18, 19,
        *range(21, 33), *range(41, 62)
    ]
    assert baseline["qa_unverified_excluded_count"] == 0
    assert {row["ordinal"] for row in excluded} == QA_UNVERIFIED_EXCLUDED_ORDINALS
    assert all(row["failure"] is None for row in excluded)
    assert baseline["fail_closed_gap_count"] == 11
    assert {row["ordinal"] for row in gaps} == FAIL_CLOSED_GAP_ORDINALS
    assert all(row["failure"] for row in gaps)
    assert len(rows) == 11
    assert all(row["source_ledger"]["path"] for row in rows)
    assert all(len(row["source_ledger"]["sha256"]) == 64 for row in rows)
    assert all(row["evidence"]["path"] for row in excluded)
    assert all(len(row["evidence"]["sha256"]) == 64 for row in excluded)
    assert baseline["corpus_version"] == CANONICAL_CORPUS_VERSION
    assert baseline["contracts_referenced"] == CONTRACTS_REFERENCED


def test_beaaa17709_batch_a_pinned_jsonl_rows_are_target_bound_working() -> None:
    payload = load_ledger()
    rows = [
        row
        for row in payload["families"]
        if row["family_id"] in {"advbench", "factscore", "halueval", "jailbreakbench"}
    ]

    assert {row["family_id"] for row in rows} == {
        "advbench",
        "factscore",
        "halueval",
        "jailbreakbench",
    }
    assert all(family_is_working(row) for row in rows)
    assert all(row["real_blocker"] is None for row in rows)
    assert {row["required_cases"] for row in rows} == {6, 8, 12}
    assert all(
        row["target_bound_smoke_result"]["summary_evidence"]["path"]
        == "artifacts/beaaa-17709/target-smoke/summary.json"
        for row in rows
    )
    assert all(row["target_bound_smoke_result"]["paid_calls"] == 0 for row in rows)
    assert all(
        row["target_bound_smoke_result"]["customer_data"] is False for row in rows
    )
    assert all(
        row["target_bound_smoke_result"]["score_activation"] is False for row in rows
    )


def test_beaaa17671_qa_approved_isolated_framework_rows_are_integrated() -> None:
    payload = load_ledger()
    rows = [
        row
        for row in payload["families"]
        if row["family_id"] in {"agentdojo", "garak", "garak_beaaa152", "harmbench"}
    ]

    assert {row["family_id"] for row in rows} == {
        "agentdojo",
        "garak",
        "garak_beaaa152",
        "harmbench",
    }
    assert all(row["current_state"] == "target_bound_working" for row in rows)
    assert all(family_is_working(row) for row in rows)
    assert all(
        "tests/test_beaaa17707_batch_a_isolated_target_runner.py"
        in row["automated_tests"]
        for row in rows
    )
    assert all(
        row["target_bound_smoke_result"]["status"] == "passed" for row in rows
    )
