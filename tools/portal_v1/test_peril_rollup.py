"""BEAAA-5761 — peril-aligned underwriting rollup unit tests.

Covers AC1-AC4 + AC7 from BEAAA-5760 design packet:
- AC1 _build_peril_rollup is pure: are_total / subscores / band / confidence byte-equal
- AC2 six covered perils in canonical order on a P1..P6 fixture, each partial / count=1
- AC3 BIAS_FAIRNESS (P2.*) rubric points bucket separately, NOT into any covered peril
- AC4 untagged refs (perils=[]) appear in uncovered_perils accounting, no crash, no BIAS bleed
- AC7 invariant-violation path raises PortalError("peril_rollup_invariant_violated") fail-closed
"""

from __future__ import annotations

import copy
import json

import pytest

import portal
from portal import PortalError, _build_peril_rollup


def _base_score() -> dict:
    return {
        "scan_id": "scan-fixture-001",
        "are_total": 64,
        "subscores": {"GOVERNANCE": 16, "ROBUSTNESS": 16, "BIAS_FAIRNESS": 16, "OPS": 16},
        "band": "C",
        "confidence": 0.72,
        "evidence_refs": [],
        "uncovered_dimensions": [],
        "scanner_version": portal.SCANNER_VERSION,
        "corpus_version": portal.CORPUS_VERSION,
        "rubric_version": portal.RUBRIC_VERSION,
        "model_versions": {},
        "seed": "peril-rollup-unit-fixture",
        "run_timestamp": "2026-06-16T07:30:00Z",
    }


def _ref(rubric_point_id: str, verdict: str, weight: float, perils: list[str]) -> dict:
    return {
        "rubric_point_id": rubric_point_id,
        "test_id": f"t-{rubric_point_id}",
        "artifact_uri": f"portal-evidence://fixture/{rubric_point_id}",
        "verdict": verdict,
        "weight": weight,
        "perils": list(perils),
    }


def test_rollup_does_not_mutate_score():
    """AC1 — round-trip Δ=0 on are_total / subscores / band / confidence."""
    score = _base_score()
    score["evidence_refs"] = [
        _ref("ROBUSTNESS.TOOL_AUTHORITY.SCOPE", "pass", 1.0, ["P1"]),
        _ref("ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL", "fail", 1.0, ["P2"]),
        _ref("OPS.HALLUCINATION.NUMERIC", "partial", 0.5, ["P4"]),
        _ref("P2.1", "fail", 1.0, []),  # BIAS by rubric prefix
        _ref("ROBUSTNESS.PROMPT_INJECTION.DIRECT", "pass", 1.0, []),  # untagged
    ]
    snapshot = json.loads(json.dumps(score))  # deep copy for byte-equality check
    rollup = _build_peril_rollup(score)
    # invariants byte-equal pre/post
    assert score["are_total"] == snapshot["are_total"]
    assert score["subscores"] == snapshot["subscores"]
    assert score["band"] == snapshot["band"]
    assert score["confidence"] == snapshot["confidence"]
    # full canonical equality (rollup must not have mutated any field)
    assert json.dumps(score, sort_keys=True) == json.dumps(snapshot, sort_keys=True)
    # rollup self-reports invariants_ok = all true
    assert rollup["score_invariants"] == {
        "are_total_unchanged": True,
        "subscores_unchanged": True,
        "band_unchanged": True,
        "confidence_unchanged": True,
    }


def test_six_peril_canonical_order():
    """AC2 — one ref tagged each P1..P6 → six covered_perils in canonical order, partial / count=1."""
    score = _base_score()
    score["evidence_refs"] = [
        _ref("ROBUSTNESS.TOOL_AUTHORITY.SCOPE", "pass", 1.0, ["P1"]),
        _ref("ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL", "pass", 1.0, ["P2"]),
        _ref("ROBUSTNESS.PII.LEAK", "fail", 1.0, ["P3"]),
        _ref("OPS.HALLUCINATION.NUMERIC", "partial", 0.5, ["P4"]),
        _ref("ROBUSTNESS.PROMPT_INJECTION.DIRECT", "pass", 1.0, ["P5"]),
        _ref("GOVERNANCE.PII.HANDLING_ATTESTATION", "fail", 1.0, ["P6"]),
    ]
    rollup = _build_peril_rollup(score)
    covered = rollup["covered_perils"]
    assert len(covered) == 6
    assert [p["peril_code"] for p in covered] == ["P1", "P2", "P3", "P4", "P5", "P6"]
    for entry in covered:
        assert entry["evidence_count"] == 1
        assert entry["coverage_status"] == "partial"
    assert rollup["uncovered_perils"] == []
    assert rollup["untagged_evidence_count"] == 0
    # BIAS bucket not populated by P1..P6 refs
    assert rollup["non_covered_regulatory_signal"]["evidence_count"] == 0


def test_bias_signals_bucketed_separately():
    """AC3 — refs whose rubric_point_id starts with P2. go to non_covered_regulatory_signal,
    NOT into any covered peril bucket (even if perils tag is present, BIAS by rubric prefix wins)."""
    score = _base_score()
    score["evidence_refs"] = [
        _ref("P2.1", "fail", 1.0, []),
        _ref("P2.3", "partial", 0.5, []),
        _ref("P2.5", "pass", 1.0, []),
        # Sanity: a non-BIAS ref tagged P4 should still land in covered.
        _ref("OPS.HALLUCINATION.NUMERIC", "fail", 1.0, ["P4"]),
    ]
    rollup = _build_peril_rollup(score)
    bias = rollup["non_covered_regulatory_signal"]
    assert bias["bucket_name"] == "BIAS_FAIRNESS"
    assert bias["disposition"] == "monitored_underwriting_signal_not_priced_as_paid_peril"
    assert bias["evidence_count"] == 3
    assert bias["evidence_verdicts"] == {"pass": 1, "partial": 1, "fail": 1}
    # weighted = 1.0*0(fail) + 0.5*0.5(partial) + 1.0*1.0(pass) = 1.25
    assert bias["weighted_evidence_sum"] == pytest.approx(1.25)
    # BIAS refs do NOT appear in any covered peril bucket.
    for entry in rollup["covered_perils"]:
        if entry["peril_code"] == "P4":
            assert entry["evidence_count"] == 1
        else:
            assert entry["evidence_count"] == 0
            assert entry["coverage_status"] == "uncovered"


def test_untagged_refs_appear_in_uncovered_perils_accounting():
    """AC4 — refs with perils=[] do NOT crash, do NOT silently land in BIAS, and increment
    untagged_evidence_count so the rollup reports them via uncovered_perils accounting."""
    score = _base_score()
    score["evidence_refs"] = [
        _ref("ROBUSTNESS.SOMETHING.UNTAGGED", "pass", 1.0, []),
        _ref("OPS.OTHER.UNTAGGED", "fail", 1.0, []),
    ]
    rollup = _build_peril_rollup(score)
    assert rollup["untagged_evidence_count"] == 2
    # No covered peril bucket sees these.
    for entry in rollup["covered_perils"]:
        assert entry["evidence_count"] == 0
    # All six perils still uncovered (no refs tagged any peril).
    assert rollup["uncovered_perils"] == ["P1", "P2", "P3", "P4", "P5", "P6"]
    # BIAS bucket is empty (untagged != BIAS).
    assert rollup["non_covered_regulatory_signal"]["evidence_count"] == 0


def test_invariant_violation_fails_closed(monkeypatch):
    """AC7 — defense-in-depth: if any of are_total / subscores / band / confidence
    moves between pre- and post-snapshot, _build_peril_rollup raises a 500.

    Simulate by patching json.dumps to mutate the score dict during the snapshot's
    deepcopy step — this is the only way to provoke the guard since the production
    code path never mutates the score."""
    score = _base_score()
    score["evidence_refs"] = [
        _ref("ROBUSTNESS.TOOL_AUTHORITY.SCOPE", "pass", 1.0, ["P1"]),
    ]
    # Construct a guarded score where post-state differs from pre-state by
    # corrupting are_total at iteration time via a custom evidence_refs container.
    class _SneakyRefs(list):
        def __iter__(self):
            score["are_total"] = 999  # mutate during aggregation
            return super().__iter__()
    score["evidence_refs"] = _SneakyRefs(score["evidence_refs"])
    with pytest.raises(PortalError) as ei:
        _build_peril_rollup(score)
    assert ei.value.status == 500
    assert ei.value.code == "peril_rollup_invariant_violated"
