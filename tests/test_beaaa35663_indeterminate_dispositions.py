"""BEAAA-35663: every production-scored row that stayed unreadable carries a reason.

The point of these tests is that "indeterminate" can never again be a resting place. A
row is either readable and audited, or it is unreadable and sits in a named class that
says where the construction lives and what reading it would cost. A new unreadable row
from a pack nobody has classified fails here rather than joining an anonymous bucket.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GENERATOR = REPO / "tools/construction-audit/beaaa35663_indeterminate_dispositions.py"
RECORD = REPO / "artifacts/beaaa-35661/indeterminate_dispositions.json"
LEDGER = REPO / "artifacts/beaaa-35660/construction_ledger.json"


def _record() -> dict:
    return json.loads(RECORD.read_text(encoding="utf-8"))


def test_no_production_scored_row_is_flagged():
    # The stop condition on BEAAA-35663: a flagged production-scored row means a live
    # score was measured under the construction BEAAA-35020 retired, and the issue says
    # halt rather than merge. Pinned here so a later ledger regeneration cannot land one
    # quietly.
    assert _record()["verdicts"].get("flagged", 0) == 0


def test_every_unreadable_row_sits_in_a_classified_pack():
    record = _record()
    assert "unclassified_pack" not in record["classes"], (
        "an unreadable row comes from a pack with no recorded reason; classify it in "
        "PACK_CLASS before merging"
    )
    counted = sum(entry["rows"] for entry in record["classes"].values())
    assert counted == record["unreadable_with_reason"] == record["verdicts"]["indeterminate"]


def test_every_class_names_a_path_and_a_cost():
    for name, entry in _record()["classes"].items():
        for field in ("reason", "path_that_would_hold_it", "what_it_would_take"):
            assert entry.get(field, "").strip(), f"{name} is missing {field}"
        assert entry["rows"] == len(entry["cases"]), name


def test_the_96_are_fully_accounted_for():
    record = _record()
    assert record["opening_indeterminate_count_beaaa_35660"] == 96
    assert record["recovered_readable_beaaa_35661"] + record["unreadable_with_reason"] == 96
    assert record["recovered_readable_beaaa_35661"] > 0


def test_record_is_reproducible_from_the_ledger():
    # The record is derived, never edited. Re-running the generator must reproduce the
    # committed bytes, so a hand-tuned count cannot survive.
    before = RECORD.read_bytes()
    subprocess.run([sys.executable, str(GENERATOR)], cwd=REPO, check=True,
                   capture_output=True, text=True)
    assert RECORD.read_bytes() == before


def test_production_scored_denominator_matches_the_ledger():
    rows = [r for r in json.loads(LEDGER.read_text(encoding="utf-8"))["rows"]
            if r.get("scoring_active")]
    assert len(rows) == _record()["production_scored_rows"]
