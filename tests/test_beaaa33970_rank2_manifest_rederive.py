"""BEAAA-33970: the pinned rank-2 manifest carries declared capabilities, not None.

The twenty-six capability-gated rows used to embed
``unavailable_capability:<cap>=None``.  The literal ``None`` was the defect: the
manifest's own capability state read undeclared even after BEAAA-33966 recorded
eleven ``false`` declarations in a side artifact.  These tests pin the derived
result and the provenance that authorises it.

They also pin what the re-derivation does *not* fix.  Twenty-four BEAAA-33228
rows carry a verdict the scan never measured, so the fourth acceptance-gate
term stays false and ``build_ledger`` still refuses.  A test that quietly
tolerated that would let a later change hide it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.beaaa31092_content_slice import RUN_MANIFEST_SHA256
from tools.beaaa33970_rank2_manifest_rederive import (
    SOURCE_MANIFEST_SHA256,
    load_declarations,
    rederive,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "artifacts/beaaa-31092/rank2-run-manifest.json"
DECLARATIONS_PATH = REPO_ROOT / "artifacts/beaaa-33966/capability-declarations.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_no_row_embeds_an_undeclared_capability(manifest: dict) -> None:
    undeclared = [
        row["probe_id"]
        for row in manifest["evidence_rows"]
        if str(row.get("incomplete_reason") or "").endswith("=None")
    ]
    assert undeclared == []


def test_twenty_six_rows_carry_capability_na_evidence(manifest: dict) -> None:
    converted = [
        row
        for row in manifest["evidence_rows"]
        if (row.get("na_evidence") or {}).get("capability_gate")
    ]
    assert len(converted) == 26
    for row in converted:
        evidence = row["na_evidence"]
        assert row["status"] == "complete"
        assert row["verdict"] == "not_applicable"
        assert row["acquisition_basis"] == "not-applicable"
        assert row["target_calls"] == 0
        assert evidence["declared_value"] is False
        # A disposition has to name the record that authorised it.
        assert evidence["basis_artifact"] == "artifacts/BEAAA-33199/capability-manifest.json"
        assert evidence["basis_artifact_sha256"] == (
            "1e1981936009a95cd5f08d9f33e9b833e4178886f1fb10d5a5c9681d184f73be"
        )
        assert evidence["missing_capability_name"] in evidence["profile_capability_manifest"]


def test_missing_required_is_zero_and_the_manifest_is_bind_eligible(manifest: dict) -> None:
    assert manifest["counts"]["missing_required"] == 0
    assert manifest["incomplete"] is False
    assert manifest["analysis_status"] == "complete"
    assert manifest["bind_eligibility"] == "eligible"


def test_the_twenty_four_rows_no_longer_block_the_gate(manifest: dict) -> None:
    """The gap the re-derivation left open, closed by BEAAA-33992.

    CTO ruling 2026-09-18 (``surface``): each of the twenty-four rows keeps its
    fail-closed ``fail`` and carries the ``observational_rejection`` its own
    evidence file already records, copied into ``na_evidence`` by the branch at
    ``api/full_scan_executor.py:1313``.  ``api/full_scan_executor.py:1597``
    already exempts a zero-judge-call ``fail`` that carries one, so these rows
    were never in the BEAAA-33228 defect class - the manifest projection simply
    dropped the field.  Both halves are pinned: the count stays at twenty-four
    so none can be quietly dispositioned away, and the gate they used to block
    now passes.
    """
    gate = manifest["acceptance_gate"]
    assert manifest["counts"]["unjustified_non_acquisition"] == 0
    assert manifest["counts"]["evidenced"] == 187
    surfaced = [
        row for row in manifest["evidence_rows"]
        if (row.get("na_evidence") or {}).get("observational_rejection")
        and row.get("test_id") in {
            "tool_misuse_unauthorized_call", "tool_misuse_scope_escape"}
    ]
    assert len(surfaced) == 24
    assert all(row["verdict"] == "fail" for row in surfaced)
    assert all(row["target_calls"] == 0 for row in surfaced)
    assert gate["canonical_reconciliation_equal"] is True
    assert gate["incomplete_false"] is True
    assert gate["zero_unapproved_missing_required"] is True
    assert gate["expected_attempted_terminal_evidenced_equal"] is True
    assert gate["passed"] is True


def test_the_pin_tracks_the_committed_bytes() -> None:
    import hashlib

    assert hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest() == RUN_MANIFEST_SHA256
    assert manifest_provenance()["source_manifest_sha256"] == SOURCE_MANIFEST_SHA256


def manifest_provenance() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["rederivation"]


def test_a_true_declaration_is_refused(tmp_path: Path) -> None:
    """A ``true`` unlocks a claim about the target, and the 33199 record is a
    derivation.  The CEO's 2026-09-18 ruling admits it as basis for a
    disposition only, so the tool must refuse rather than widen it."""
    document = json.loads(DECLARATIONS_PATH.read_text(encoding="utf-8"))
    document["capability_manifest"]["approval_binding"] = True
    poisoned = tmp_path / "capability-declarations.json"
    poisoned.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="approval_binding"):
        load_declarations(poisoned)


def test_rederivation_is_idempotent(tmp_path: Path) -> None:
    """Re-running against the committed manifest reproduces it byte for byte
    apart from its own provenance stamp, so the artifact is derived, not typed."""
    out = tmp_path / "rank2-run-manifest.json"
    summary = rederive(MANIFEST_PATH, DECLARATIONS_PATH, out)
    assert summary["changed"] == 0
    assert summary["missing_required"] == 0
    # BEAAA-33992 surfaced the other 24 rows' recorded observational rejection,
    # so every row in the population is now evidenced.
    assert summary["evidenced"] == 187
    assert summary["unjustified_non_acquisition"] == 0
    assert summary["acceptance_gate_passed"] is True

    produced = json.loads(out.read_text(encoding="utf-8"))
    committed = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for document in (produced, committed):
        document.pop("rederivation")
        document.pop("manifest_hash", None)
        document.pop("signed_manifest", None)
    assert produced == committed
