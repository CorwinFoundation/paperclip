"""BEAAA-35380: 23 of 24 TOOL_MISUSE UC/PT rows activate under dev-mode only.

Mirrors the test shape from test_beaaa31092_dev_scoring_activation.py.
Evidence: artifacts/beaaa-35020/tool_misuse_corrected_n5_final.json at
master 1ba1e8a0; 120 calls, openrouter:openai/gpt-4.1-mini, temp 0.0, seeds 0..4.

TOOL_MISUSE_PT-05 (measured 1 of 5 seeds) is held as unmeasured per CEO
ruling on BEAAA-35020; its digest is absent from the scored set.
"""
import importlib
import json
from pathlib import Path

import tools.active_test_registry as registry
from tools.founder_dev_mode import FOUNDER_DEV_MODE_ENV

ROOT = Path(__file__).resolve().parents[1]
LEDGER = json.loads(
    (ROOT / "artifacts/beaaa-35380/tool-misuse-dev-activation-ledger.json").read_text(
        encoding="utf-8"
    )
)
SCORED = frozenset(
    row["dedupe_sha256"] for row in LEDGER["rows"] if row["disposition"] == "scored"
)
HELD = frozenset(
    row["dedupe_sha256"] for row in LEDGER["rows"] if row["disposition"] != "scored"
)
ALL_DIGESTS = SCORED | HELD


def _rows(monkeypatch, flag):
    monkeypatch.delenv(FOUNDER_DEV_MODE_ENV, raising=False)
    if flag is not None:
        monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, flag)
    importlib.reload(registry)
    return registry.build_registry(include_content_candidates=True)


def test_ledger_activation_invariants():
    assert len(SCORED) == 23, "expected 23 scored rows"
    assert len(HELD) == 1, "expected exactly 1 held row (PT-05)"
    held_probes = [r["probe_id"] for r in LEDGER["rows"] if r["disposition"] != "scored"]
    assert held_probes == ["TOOL_MISUSE_PT-05"], "PT-05 is the only held row"
    assert LEDGER["production_activation"] is False
    assert LEDGER["underwriting_active"] is False
    assert LEDGER["priced_score_weight"] == 0.0
    assert registry._beaaa35380_dev_scored_digests() == SCORED


def test_flag_off_leaves_all_24_rows_reported_not_scored(monkeypatch):
    rows = _rows(monkeypatch, None)
    mine = [r for r in rows if r.get("candidate_dedupe_sha256") in ALL_DIGESTS]
    assert len(mine) == 24, f"expected 24 rows, got {len(mine)}"
    assert {r["scoring_active"] for r in mine} == {False}
    assert {r["reporting_status"] for r in mine} == {"reported_not_scored"}


def test_flag_off_production_count_unchanged(monkeypatch):
    rows = _rows(monkeypatch, None)
    assert registry.count_active_executable(rows) == 2373


def test_flag_on_promotes_exactly_23_scored_rows(monkeypatch):
    rows = _rows(monkeypatch, "on")
    mine_scored = [r for r in rows if r.get("candidate_dedupe_sha256") in SCORED]
    mine_held = [r for r in rows if r.get("candidate_dedupe_sha256") in HELD]
    assert len(mine_scored) == 23
    assert {r["dev_scoring_active"] for r in mine_scored} == {True}
    assert {r["attestation_tier"] for r in mine_scored} == {"founder_dev_mode"}
    assert {r["scoring_active"] for r in mine_scored} == {True}
    # dev-mode reporting vocabulary: "scoring_active" (not "scored") when flag ON
    assert {r["reporting_status"] for r in mine_scored} == {"scoring_active"}
    # Held row (PT-05) must stay out regardless of the flag
    assert len(mine_held) == 1
    assert mine_held[0]["scoring_active"] is False
    assert mine_held[0]["reporting_status"] == "reported_not_scored"


def test_flag_on_pt05_never_promoted(monkeypatch):
    """PT-05 must not be promoted even though its family is in FOUNDER_DEV_SCORED_FAMILIES."""
    rows = _rows(monkeypatch, "on")
    mine_held = [r for r in rows if r.get("candidate_dedupe_sha256") in HELD]
    assert not any(r.get("dev_scoring_active") is True for r in mine_held)


def test_flag_on_rubric_points_bound(monkeypatch):
    rows = _rows(monkeypatch, "on")
    mine_scored = [r for r in rows if r.get("candidate_dedupe_sha256") in SCORED]
    pt_rows = [r for r in mine_scored if r.get("test_id") == "tool_misuse_param_tamper"]
    uc_rows = [r for r in mine_scored if r.get("test_id") == "tool_misuse_unauthorized_call"]
    assert all(
        r.get("candidate_rubric_point_id") == "ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER"
        for r in pt_rows
    )
    assert all(
        r.get("candidate_rubric_point_id") == "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL"
        for r in uc_rows
    )
    assert all(r.get("execution_evidenced") is True for r in mine_scored)
    assert all(r.get("execution_evidence_status") == "beaaa35380_n5_target_measured" for r in mine_scored)
