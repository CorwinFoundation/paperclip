"""BEAAA-38568: peril_data_containment is dropped from the candidate pool (330 -> 324).

The family has six rows. Its pass rate on the BEAAA-33706 capture was 0.0000, and the
patched refusal classifier reads five of the six as refusals, which leaves one gradeable
row. One gradeable row cannot clear a discrimination gate on any reading.

As with the BEAAA-35963 stub drop, the decision is recorded as data and this test makes
it enforceable: a promotion walk that sweeps these rows back in fails here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "artifacts" / "beaaa-38568" / "dropped_peril_data_containment.json"
CATALOGUE = ROOT / "artifacts" / "beaaa-18720" / "unified-probe-inventory.jsonl"
FAMILY = "peril_data_containment"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_names_all_six() -> None:
    manifest = _manifest()
    assert manifest["family_id"] == FAMILY
    assert len(manifest["rows"]) == 6
    assert {r["family_id"] for r in manifest["rows"]} == {FAMILY}
    assert manifest["candidate_pool_before"] - len(manifest["rows"]) == manifest["candidate_pool_after"]


def test_manifest_covers_every_catalogue_row_of_the_family() -> None:
    """A new row authored into the family is a new decision, not a silent re-entry."""
    catalogue = {
        row["dedupe_sha256"]
        for row in (json.loads(line) for line in CATALOGUE.read_text(encoding="utf-8").splitlines() if line.strip())
        if row.get("family_id") == FAMILY
    }
    assert catalogue == {r["dedupe_sha256"] for r in _manifest()["rows"]}


def test_dropped_rows_are_not_scoring_active() -> None:
    registry = pytest.importorskip("tools.active_test_registry")
    build = getattr(registry, "build_registry", None)
    if build is None:
        pytest.skip("active_test_registry exposes no build_registry()")
    rows = build()
    rows = rows if isinstance(rows, list) else rows.get("rows", [])
    by_digest = {r.get("candidate_dedupe_sha256"): r for r in rows if r.get("candidate_dedupe_sha256")}
    active = [
        entry["stable_case_id"]
        for entry in _manifest()["rows"]
        if by_digest.get(entry["dedupe_sha256"], {}).get("scoring_active")
    ]
    assert active == [], f"dropped peril_data_containment rows are scoring-active: {active}"
