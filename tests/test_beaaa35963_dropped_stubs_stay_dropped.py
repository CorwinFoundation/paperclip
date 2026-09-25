"""BEAAA-35963: the 36 prompt-less fairness stubs must never become scoring-active.

They were dropped on a CEO ruling because they carry no prompt text at all -- turn 0 is
a metadata descriptor and nothing else -- so the capture rendered the descriptor at the
target and graded whatever came back. One of them, P2.5-BIAS-07, fabricated a
statistically-significant lending-sentiment disparity complete with an invented metrics
block. Fabricated evidence fails closed.

The drop is recorded as data rather than as prose in a document, and this test is what
makes it enforceable: a future promotion walk that sweeps these rows back into the pool
fails here rather than shipping a manufactured finding in a released score.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "artifacts" / "beaaa-35963" / "dropped_stub_rows.json"
CATALOGUE = ROOT / "artifacts" / "beaaa-18720" / "unified-probe-inventory.jsonl"

_PROMPT_KEYS = ("pair_a", "pair_b", "prompt", "content", "text", "user", "concrete_input_or_action")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_names_all_thirty_six() -> None:
    manifest = _manifest()
    assert len(manifest["rows"]) == 36
    assert manifest["candidate_pool_before"] - len(manifest["rows"]) == manifest["candidate_pool_after"]


def test_dropped_rows_still_carry_no_prompt() -> None:
    """The stated reason for the drop must remain true of the catalogue as it stands.

    If someone authors real prompts for these case ids, this test fails -- correctly.
    That is a new decision to bring back to the CEO, not a silent re-entry.
    """
    catalogue = {
        row["stable_case_id"]: row
        for row in (json.loads(line) for line in CATALOGUE.read_text(encoding="utf-8").splitlines() if line.strip())
    }
    with_prompt = []
    for entry in _manifest()["rows"]:
        row = catalogue.get(entry["stable_case_id"])
        if row is None:
            continue
        turn_0 = (row.get("prompt_turn_sequence") or [{}])[0]
        if any(key in turn_0 for key in _PROMPT_KEYS):
            with_prompt.append(entry["stable_case_id"])
    assert with_prompt == [], (
        f"{len(with_prompt)} dropped stub(s) have gained prompt text: {with_prompt}. "
        "Re-entry into the scoring pool is a CEO decision, not a test fix."
    )


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
    assert active == [], f"dropped stub rows are scoring-active: {active}"
