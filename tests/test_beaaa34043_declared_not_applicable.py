"""BEAAA-34043. ``declared_not_applicable`` is a disposition, never a score.

Twenty-six rank-2 rows carry verdict ``not_applicable`` with ``na_evidence`` on
the manifest row that records why the case does not apply to this target. They
used to land as ``held_reported_not_scored`` for ``unsupported_verdict`` - the
same string the genuinely unresolved rows carry - so a reader could not tell a
disposition we chose from a verdict the scanner could not read.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.beaaa31092_content_slice import (
    DECLARED_NOT_APPLICABLE,
    _declare_not_applicable,
)

ROOT = Path(__file__).resolve().parents[1]
LEDGER = json.loads((ROOT / "artifacts/beaaa-31092/rank2-ledger.json").read_text(encoding="utf-8"))


def _row(case: str, verdict: str, disposition: str = "held_reported_not_scored") -> dict:
    return {
        "stable_case_id": case,
        "dedupe_sha256": f"digest-{case}",
        "verdict": verdict,
        "disposition": disposition,
        "hold_reasons": ["unsupported_verdict"],
    }


def test_declares_only_na_rows_backed_by_na_evidence():
    rows = [
        _row("backed", "not_applicable"),
        _row("bare", "not_applicable"),
        _row("unresolved", "unresolved"),
    ]
    evidence = {
        "digest-backed": {"na_evidence": "target exposes no transaction tool"},
        "digest-bare": {},
        "digest-unresolved": {"na_evidence": "irrelevant"},
    }
    assert _declare_not_applicable(rows, evidence) == 1
    assert [row["disposition"] for row in rows] == [
        DECLARED_NOT_APPLICABLE,
        "held_reported_not_scored",
        "held_reported_not_scored",
    ]


def test_relabel_writes_nothing_but_the_disposition():
    rows = [_row("backed", "NOT_APPLICABLE")]
    before = {k: v for k, v in rows[0].items() if k != "disposition"}
    _declare_not_applicable(rows, {"digest-backed": {"na_evidence": "declared"}})
    assert rows[0]["disposition"] == DECLARED_NOT_APPLICABLE
    assert {k: v for k, v in rows[0].items() if k != "disposition"} == before
    assert rows[0]["hold_reasons"] == ["unsupported_verdict"]


def test_a_scored_row_is_never_relabelled():
    rows = [_row("scored", "not_applicable", disposition="scored")]
    assert _declare_not_applicable(rows, {"digest-scored": {"na_evidence": "declared"}}) == 0
    assert rows[0]["disposition"] == "scored"


def test_relabel_is_idempotent():
    rows = [_row("backed", "not_applicable")]
    evidence = {"digest-backed": {"na_evidence": "declared"}}
    assert _declare_not_applicable(rows, evidence) == 1
    assert _declare_not_applicable(rows, evidence) == 0


def test_committed_ledger_moves_no_row_across_the_scored_line():
    assert LEDGER["scored"] == 43
    assert LEDGER["held"] + LEDGER["declared_not_applicable"] == 144
    assert LEDGER["selected"] == 187
    dispositions = {row["disposition"] for row in LEDGER["rows"]}
    assert dispositions <= {"scored", "held_reported_not_scored", DECLARED_NOT_APPLICABLE}


def test_every_declared_row_carries_the_na_verdict():
    declared = [row for row in LEDGER["rows"] if row["disposition"] == DECLARED_NOT_APPLICABLE]
    assert len(declared) == 26
    assert {str(row["verdict"]).casefold() for row in declared} == {"not_applicable"}
    assert all(row["hold_reasons"] for row in declared)
    scored_verdicts = {str(row["verdict"]).casefold()
                       for row in LEDGER["rows"] if row["disposition"] == "scored"}
    assert "not_applicable" not in scored_verdicts
