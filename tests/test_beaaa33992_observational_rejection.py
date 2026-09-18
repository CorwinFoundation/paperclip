"""BEAAA-33992: surfacing a recorded observational rejection, and refusing to invent one.

CTO ruling 2026-09-18 (``surface``).  The twenty-four rows the acceptance gate
flagged were never in the BEAAA-33228 defect class: each one's evidence file
records ``observational_rejection``, and
``api/full_scan_executor.py:1597`` already exempts a zero-judge-call ``fail``
that carries one.  They looked unjustified only because ``to_manifest_row()``
drops the field.

The load-bearing property is not that twenty-four rows changed.  It is that a row
whose evidence records **no** rejection is refused rather than justified - that
refusal is what keeps this a projection of the scan's own record instead of an
author writing evidence.  It is pinned here on a synthetic export so it runs
everywhere, not only on a host that happens to have the real one mounted.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.full_scan_executor import (  # noqa: E402
    build_untested_coverage,
    evidenced_rows,
    unjustified_non_acquisition_rows,
)
from tools.beaaa33992_observational_rejection_na_evidence import (  # noqa: E402
    _evidence_path,
    rederive,
)

MANIFEST = ROOT / "artifacts/beaaa-31092/rank2-run-manifest.json"
FLAGGED_FAMILIES = {"tool_misuse_unauthorized_call", "tool_misuse_scope_escape"}
REJECTION = "observational_fail_closed:ObservationalContractError"


def _committed() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _pre_surface(manifest: dict) -> dict:
    """The manifest as it stood before this change: the 24 carry no na_evidence."""
    for row in manifest["evidence_rows"]:
        if row.get("test_id") in FLAGGED_FAMILIES:
            row["na_evidence"] = None
    return manifest


def _fake_export(tmp_path: Path, manifest: dict, *, without_rejection: set[str]) -> Path:
    """An export mirroring the real one, with named rows recording no rejection."""
    job_id = manifest["evidence_rows"][0]["evidence_ref"].removeprefix(
        "evidence://").partition("/")[0]
    root = tmp_path / job_id
    root.mkdir()
    for row in manifest["evidence_rows"]:
        if row.get("test_id") not in FLAGGED_FAMILIES:
            continue
        path = _evidence_path(root, row["evidence_ref"])
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "verdict": row["verdict"],
            "payloads": [],
            "responses": [],
            "judge": {"type": "structured_invariant", "llm_judge_calls": 0},
        }
        if row["probe_id"] not in without_rejection:
            record["observational_rejection"] = REJECTION
        path.write_text(json.dumps(record), encoding="utf-8")
    return root


def test_a_row_whose_evidence_records_no_rejection_is_refused(tmp_path: Path) -> None:
    """Fail closed. The one row with nothing recorded stays flagged, and the gate
    it blocks stays shut - which is the whole reason this is a projection and not
    an authoring pass."""
    manifest = _pre_surface(_committed())
    victim = next(
        row["probe_id"] for row in manifest["evidence_rows"]
        if row.get("test_id") in FLAGGED_FAMILIES
    )
    root = _fake_export(tmp_path, manifest, without_rejection={victim})
    source = tmp_path / "source.json"
    source.write_text(json.dumps(manifest), encoding="utf-8")

    summary = rederive(source, root, tmp_path / "out.json")

    assert summary["flagged"] == 24
    assert summary["surfaced"] == 23
    assert summary["refused"] == 1
    assert summary["unjustified_non_acquisition"] == 1
    assert summary["acceptance_gate_passed"] is False

    produced = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    still_flagged = unjustified_non_acquisition_rows(produced["evidence_rows"])
    assert [row["probe_id"] for row in still_flagged] == [victim]
    assert build_untested_coverage(produced["evidence_rows"])["count"] == 1


def test_nothing_but_na_evidence_moves_on_the_rows_it_touches(tmp_path: Path) -> None:
    manifest = _pre_surface(_committed())
    root = _fake_export(tmp_path, manifest, without_rejection=set())
    source = tmp_path / "source.json"
    source.write_text(json.dumps(manifest), encoding="utf-8")

    summary = rederive(source, root, tmp_path / "out.json")
    assert (summary["flagged"], summary["surfaced"], summary["refused"]) == (24, 24, 0)

    before = {row["probe_id"]: row for row in manifest["evidence_rows"]}
    produced = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    for row in produced["evidence_rows"]:
        prior = before[row["probe_id"]]
        moved = {
            key for key in set(row) | set(prior)
            if row.get(key) != prior.get(key)
        }
        assert moved <= {"na_evidence"}, (row["probe_id"], moved)
        if row.get("test_id") in FLAGGED_FAMILIES:
            assert row["na_evidence"]["observational_rejection"] == REJECTION
            assert row["verdict"] == "fail"
            assert row["target_calls"] == 0
            assert row["acquisition_basis"] == "missing"
            assert row["score_contribution"] is False


def test_the_committed_manifest_carries_what_the_ruling_authorised() -> None:
    """The landed artifact, not a rebuild of it: 24 rows, each with the recorded
    rejection, no unjustified rows left, and every row in the population evidenced."""
    manifest = _committed()
    rows = manifest["evidence_rows"]
    surfaced = [row for row in rows if row.get("test_id") in FLAGGED_FAMILIES]

    assert len(surfaced) == 24
    for row in surfaced:
        na = row["na_evidence"]
        assert na["observational_rejection"] == REJECTION
        assert na["surfaced_by"] == "BEAAA-33992"
        assert na["recorded_by"] == "scan"
        assert na["evidence_ref"] == row["evidence_ref"]
        assert len(na["evidence_sha256"]) == 64

    assert unjustified_non_acquisition_rows(rows) == []
    assert len(evidenced_rows(rows)) == len(rows) == 187
    assert manifest["acceptance_gate"]["passed"] is True
    # Condition 3 of the ruling: the embedded coverage statement and the rows it
    # describes must not disagree.
    assert manifest["untested_coverage"] == build_untested_coverage(rows)
