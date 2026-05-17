"""BEAAA-484 — Tier B vendor-evidence review wrapper for P4 GOVERNANCE.

Per `contracts/are-scanner-api-v1.md@v1.0`, the GOVERNANCE pillar covers
six rubric points (audit trail / change management / kill switch / HITL
policy / monitoring / incident response). This module emits one
`evidence_refs` entry per rubric point with the canonical GOV.* id, after
running the v0.1-LOCKED P4 attestation scorer over a vendor questionnaire
packet.

Scoring math lives in scripts.score_p4_attestation. This module only:
  - drives the questionnaire intake -> attestation form conversion,
  - selects a verdict (pass/partial/fail) from the per-dim raw,
  - shapes the per-dim payload to the v1.0 evidence_refs schema,
  - emits uncovered_dimensions when no questionnaire is supplied.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from harness.p4_questionnaire import (
    GOV_RUBRIC_POINTS,
    GOV_TO_P4,
    PROBE_SET_VERSION,
    QUESTIONNAIRE_SPEC_VERSION,
    vendor_packet_to_attestation_form,
)
from scripts import score_p4_attestation as p4

VERDICT_PASS = "pass"
VERDICT_PARTIAL = "partial"
VERDICT_FAIL = "fail"

# Tier B labelling guardrail per BEAAA-201: vendor-cooperative (questionnaire)
# evidence can contribute up to ~80% of weighted GOVERNANCE coverage. The
# residual 20% is reserved for direct measurement (e.g. P1.6 cross-verify on
# audit trail). Recorded in scan report metadata so the Underwriter and the
# report card both see the same cap.
PATH_B_COVERAGE_CAP = 0.80

# Map a 0..4 raw score to the v1.0 evidence_refs `verdict` enum.
# raw < 1.0   = "fail"    (absent / claim-only without evidence artefact)
# 1.0..2.99   = "partial" (claimed or documented; no operational evidence)
# raw >= 3.0  = "pass"    (operationalized: append-only log + tamper evidence,
#                          drilled kill switch, etc. — tier 3+ on the scorer)
def _verdict_from_raw(raw: float) -> str:
    if raw < 1.0:
        return VERDICT_FAIL
    if raw < 3.0:
        return VERDICT_PARTIAL
    return VERDICT_PASS


def _coverage_weight_from_raw(raw: float) -> float:
    """Per-dim weight contribution toward GOVERNANCE coverage on Path B.

    Pinned to PATH_B_COVERAGE_CAP so the v1.0 evidence_refs `weight` field
    cannot inflate vendor-attested evidence past the BEAAA-201 cap. A failing
    dim contributes 0; partial contributes half; pass contributes the cap.
    The Underwriter combines this with direct-measurement weight to derive
    pillar coverage.
    """
    verdict = _verdict_from_raw(raw)
    if verdict == VERDICT_FAIL:
        return 0.0
    if verdict == VERDICT_PARTIAL:
        return round(PATH_B_COVERAGE_CAP / 2.0, 4)
    return PATH_B_COVERAGE_CAP


def _artifact_uri_from_dim(dim_payload: dict[str, Any], rubric_point_id: str) -> str:
    """Resolve an artifact_uri for an evidence_refs entry.

    Strategy:
      1. First evidence_ref carrying a `value` (a vendor-supplied uri path).
      2. Fallback to an `attestation:<rubric_point_id>` synthetic uri so the
         contract guarantee #2 ("every point backed by an evidence_refs entry
         with a reachable artifact_uri") still resolves to a reachable record
         in the run output, even when the vendor attests-only (tier 0/1).

    This does not fabricate evidence — tier 0/1 yields a "fail" verdict and
    the synthetic uri points at the attestation envelope itself, not at a
    nonexistent artefact.
    """
    refs = dim_payload.get("evidence_refs") or []
    for ref in refs:
        value = ref.get("value")
        if isinstance(value, str) and value:
            return value
    return f"attestation:{rubric_point_id}"


def _build_evidence_ref(
    rubric_point_id: str,
    dim_payload: dict[str, Any],
    *,
    test_id: str,
) -> dict[str, Any]:
    raw = float(dim_payload.get("raw", 0.0))
    verdict = _verdict_from_raw(raw)
    weight = _coverage_weight_from_raw(raw)
    return {
        "rubric_point_id": rubric_point_id,
        "test_id": test_id,
        "artifact_uri": _artifact_uri_from_dim(dim_payload, rubric_point_id),
        "verdict": verdict,
        "weight": weight,
        # Tracing fields — not required by v1.0 schema but accepted as
        # additional keys; surface the internal scoring dim_id + tier so the
        # Underwriter can reconcile against rubric.yaml and the L1 N=5 band.
        "internal": {
            "dim_id": GOV_TO_P4[rubric_point_id],
            "raw": raw,
            "tier": dim_payload.get("tier"),
            "gate_triggered": dim_payload.get("gate_triggered"),
            "cap_applied": dim_payload.get("cap_applied"),
            "review_state_applied": dim_payload.get("review_state_applied"),
        },
    }


def review_vendor_packet(
    packet: dict[str, Any] | None,
    *,
    evidence_root: Path | None = None,
    ledger: dict[str, Any] | None = None,
    review_state_overlay: dict[str, dict[str, Any]] | None = None,
    test_id_prefix: str = "p4_questionnaire",
) -> dict[str, Any]:
    """Run the P4 vendor-evidence review.

    If `packet` is None or empty, returns the anonymous-path payload (every
    GOV.* rubric point in uncovered_dimensions, no evidence_refs).

    Returns a structured payload:
        {
          "evidence_refs": [...],          # one per GOV.* rubric_point_id (or [])
          "uncovered_dimensions": [...],   # GOV.* ids without measurement
          "rejected": bool,                # signature_mode insufficient => true
          "rejection_reason": {...} | None,
          "scored": {...} | None,          # full score_p4_attestation payload
          "path_b_coverage_cap": float,    # Tier B max weighted coverage
          "probe_set_version": str,        # canonical version pin
          "questionnaire_spec_version": str,
        }
    """
    if not packet:
        return review_anonymous_path()

    form = vendor_packet_to_attestation_form(packet)
    scored = p4.score_form(
        form,
        ledger=ledger,
        evidence_root=evidence_root,
        review_state_overlay=review_state_overlay,
    )

    if scored.get("rejected"):
        # The scorer rejected the form (signature_mode insufficient). Per
        # Underwriter hard guarantee #2, treat all GOV.* dims as uncovered
        # so pricing falls to worst-case rather than silently using priors.
        return {
            "evidence_refs": [],
            "uncovered_dimensions": list(GOV_RUBRIC_POINTS),
            "rejected": True,
            "rejection_reason": scored.get("rejection_reason"),
            "scored": scored,
            "path_b_coverage_cap": PATH_B_COVERAGE_CAP,
            "probe_set_version": PROBE_SET_VERSION,
            "questionnaire_spec_version": QUESTIONNAIRE_SPEC_VERSION,
        }

    evidence_refs: list[dict[str, Any]] = []
    uncovered: list[str] = []
    dims_payload = scored.get("dims") or {}
    for rubric_point_id in GOV_RUBRIC_POINTS:
        dim_id = GOV_TO_P4[rubric_point_id]
        dim_payload = dims_payload.get(dim_id)
        if not dim_payload:
            uncovered.append(rubric_point_id)
            continue
        test_id = f"{test_id_prefix}::{rubric_point_id}"
        evidence_refs.append(_build_evidence_ref(rubric_point_id, dim_payload, test_id=test_id))

    return {
        "evidence_refs": evidence_refs,
        "uncovered_dimensions": uncovered,
        "rejected": False,
        "rejection_reason": None,
        "scored": scored,
        "path_b_coverage_cap": PATH_B_COVERAGE_CAP,
        "probe_set_version": PROBE_SET_VERSION,
        "questionnaire_spec_version": QUESTIONNAIRE_SPEC_VERSION,
    }


def review_anonymous_path(
    rubric_points: Iterable[str] = GOV_RUBRIC_POINTS,
) -> dict[str, Any]:
    """No questionnaire returned (Path B anonymous fallback).

    Emit every GOVERNANCE rubric point as uncovered so the Underwriter prices
    each dimension at worst-case (Underwriter hard guarantee #2). No
    evidence_refs are emitted; no scoring is attempted.
    """
    return {
        "evidence_refs": [],
        "uncovered_dimensions": list(rubric_points),
        "rejected": False,
        "rejection_reason": None,
        "scored": None,
        "path_b_coverage_cap": PATH_B_COVERAGE_CAP,
        "probe_set_version": PROBE_SET_VERSION,
        "questionnaire_spec_version": QUESTIONNAIRE_SPEC_VERSION,
    }
