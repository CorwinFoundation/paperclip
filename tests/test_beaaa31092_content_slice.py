from pathlib import Path

from tools.beaaa31092_content_slice import build_ledger

ROOT = Path(__file__).resolve().parents[1]


def test_rank_2_slice_fails_closed_without_recorded_responses():
    ledger = build_ledger(
        ROOT / "artifacts/beaaa-18720/unified-probe-inventory.jsonl",
        ROOT / "artifacts/beaaa-30720-manifest.json",
    )
    assert ledger["selected"] == ledger["mapped"] == 187
    assert ledger["evidenced"] == ledger["scored"] == 0
    assert ledger["dropped_duplicate"] == 0
    assert ledger["held"] == 187
    assert ledger["underwriting_active"] is False
    assert ledger["priced_score_weight"] == 0.0
    assert {row["rubric_point_id"] for row in ledger["rows"]} == {"P3.4"}
    assert all(
        "missing_recorded_target_response" in row["hold_reasons"]
        or "missing_manifest_evidence" in row["hold_reasons"]
        for row in ledger["rows"]
    )


def test_every_selected_row_has_manifest_verdict_and_unique_identity():
    ledger = build_ledger(
        ROOT / "artifacts/beaaa-18720/unified-probe-inventory.jsonl",
        ROOT / "artifacts/beaaa-30720-manifest.json",
    )
    assert len({row["stable_case_id"] for row in ledger["rows"]}) == 187
    assert len({row["dedupe_sha256"] for row in ledger["rows"]}) == 187
    assert len({row["semantic_behavior_fingerprint"] for row in ledger["rows"]}) == 187
    assert all(row["verdict"] in {"pass", "fail", "partial", "unresolved", None} for row in ledger["rows"])
