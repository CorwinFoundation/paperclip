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


def _fixture(tmp_path: Path, rows: list[tuple[str, str, str | None]]) -> tuple[Path, Path]:
    """Build a minimal run manifest plus exported job dir for the given rows."""
    import json

    job = "11111111-2222-3333-4444-555555555555"
    evidence_rows = []
    job_dir = tmp_path / job
    for digest, verdict, response in rows:
        ref = f"evidence://{job}/fam/{digest[:8]}.json"
        evidence_rows.append({
            "probe_id": f"content-candidate:sha256:{digest}",
            "framework": "content_candidate@beaaa-18720-1.0.0",
            "evidence_ref": ref,
            "verdict": verdict,
            "model_version": "vapi:test@api.vapi.ai",
            "status": "complete",
            "acquisition_basis": "target-measured",
            "target_calls": 1,
        })
        target = job_dir / "fam" / f"{digest[:8]}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"responses": [response] if response else []}), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"evidence_rows": evidence_rows}), encoding="utf-8")
    return manifest, job_dir


def test_response_binds_through_the_manifest_ref_not_a_filename_guess(tmp_path):
    import json

    inventory = [json.loads(line) for line in
                 (ROOT / "artifacts/beaaa-18720/unified-probe-inventory.jsonl").read_text().splitlines() if line]
    slice_rows = [row for row in inventory if row.get("family_id") == "approval_freshness_replay"]
    assert len(slice_rows) >= 3
    digests = [row["dedupe_sha256"] for row in slice_rows[:3]]
    manifest, job_dir = _fixture(tmp_path, [
        (digests[0], "pass", "the agent refused the replayed approval"),
        (digests[1], "fail", "the agent replayed the stale approval"),
        (digests[2], "fail", None),
    ])
    ledger = build_ledger(ROOT / "artifacts/beaaa-18720/unified-probe-inventory.jsonl", manifest,
                          job_dir, "11111111-2222-3333-4444-555555555555")
    bound = {row["stable_case_id"]: row for row in ledger["rows"] if row["dedupe_sha256"] in digests}
    scored = [row for row in bound.values() if row["disposition"] == "scored"]
    assert len(scored) == 2, [r["hold_reasons"] for r in bound.values()]
    assert all(row["recorded_target_response"] for row in scored)
    empty = [row for row in bound.values() if row["dedupe_sha256"] == digests[2]]
    assert empty and "evidence file records no target response" in empty[0]["hold_reasons"]


def test_all_fail_family_is_held_for_want_of_a_positive_control(tmp_path):
    inventory_path = ROOT / "artifacts/beaaa-18720/unified-probe-inventory.jsonl"
    import json

    inventory = [json.loads(line) for line in inventory_path.read_text().splitlines() if line]
    digests = [row["dedupe_sha256"] for row in inventory
               if row.get("family_id") == "approval_freshness_replay"][:3]
    manifest, job_dir = _fixture(tmp_path, [(d, "fail", "it failed") for d in digests])
    ledger = build_ledger(inventory_path, manifest, job_dir, "11111111-2222-3333-4444-555555555555")
    held = [row for row in ledger["rows"] if row["dedupe_sha256"] in digests]
    assert held and all(row["disposition"] == "held_reported_not_scored" for row in held)
    assert all("no_verdict_spread_positive_control_missing" in row["hold_reasons"] for row in held)
    assert ledger["tier1_families"] == []
