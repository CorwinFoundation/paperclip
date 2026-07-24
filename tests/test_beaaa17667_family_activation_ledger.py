from __future__ import annotations

from tools.family_activation_ledger import (
    NON_WORKING_STATES,
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
    assert build_baseline(payload)["working_ordinals"] == list(range(41, 62))
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
