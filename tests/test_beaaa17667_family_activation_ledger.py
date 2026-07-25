from __future__ import annotations

import hashlib
from pathlib import Path

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

ROOT = Path(__file__).resolve().parents[1]


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
    assert baseline["truthful_baseline"] == "61/61"
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

    assert baseline["working_count"] == 61
    assert baseline["working_ordinals"] == list(range(1, 62))
    assert baseline["complete_working_predicate"] is True
    assert baseline["qa_unverified_excluded_count"] == 0
    assert {row["ordinal"] for row in excluded} == QA_UNVERIFIED_EXCLUDED_ORDINALS
    assert all(row["failure"] is None for row in excluded)
    assert baseline["fail_closed_gap_count"] == 0
    assert {row["ordinal"] for row in gaps} == FAIL_CLOSED_GAP_ORDINALS
    assert all(row["failure"] for row in gaps)
    assert rows == []
    assert all(row["source_ledger"]["path"] for row in rows)
    assert all(len(row["source_ledger"]["sha256"]) == 64 for row in rows)
    assert all(row["evidence"]["path"] for row in excluded)
    assert all(len(row["evidence"]["sha256"]) == 64 for row in excluded)
    assert baseline["corpus_version"] == CANONICAL_CORPUS_VERSION
    assert baseline["contracts_referenced"] == CONTRACTS_REFERENCED


def test_integrated_machine_ledger_carries_and_verifies_row_evidence() -> None:
    baseline = build_baseline(load_ledger())
    rows = baseline["evidence_rows"]

    assert len(rows) == 61
    assert [row["ordinal"] for row in rows] == list(range(1, 62))
    assert all({"ordinal", "family_id", "evidence"} <= row.keys() for row in rows)
    assert all(row["evidence"] for row in rows)
    referenced = [reference for row in rows for reference in row["evidence"]]
    assert referenced, "row provenance must not pass vacuously when failures=[]"
    for reference in referenced:
        if "path" not in reference:
            assert len(reference["sha256"]) == 64
            continue
        path = ROOT / reference["path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == reference["sha256"]


def test_integrated_machine_ledger_rejects_stale_evidence_digest() -> None:
    payload = load_ledger()
    payload["families"][9]["target_bound_smoke_result"]["evidence"]["sha256"] = "0" * 64

    try:
        build_baseline(payload)
    except ValueError as exc:
        assert "evidence digest mismatch" in str(exc)
    else:
        raise AssertionError("stale evidence digest was accepted")


def test_complete_working_predicate_fails_closed_on_missing_smoke() -> None:
    payload = load_ledger()
    payload["families"][0]["target_bound_smoke_result"]["evidence"] = None
    baseline = build_baseline(payload)

    assert baseline["complete_working_predicate"] is False
    assert baseline["truthful_baseline"] == "60/61"


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
