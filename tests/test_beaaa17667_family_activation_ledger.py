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
    assert baseline["total_count"] == 61
    assert len(baseline["failures"]) == 61 - baseline["working_count"]
    assert baseline["truthful_baseline"] == (
        f"{baseline['working_count']}/{baseline['total_count']}"
    )
    assert baseline["target"]["paid_call_budget"] == 0
    assert baseline["target"]["customer_data"] is False
    assert baseline["target"]["score_activation"] is False
