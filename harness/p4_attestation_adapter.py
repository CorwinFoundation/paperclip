"""BEAAA-200 / BEAAA-5210 GOV attestation integration.

Bridge `score_p4_attestation.score_form` plus the PII handling evidence-pack
grader into the measurement format consumed by
`harness.pipeline.merge_measurements_with_priors`.

When a customer P4 attestation form is supplied, this adapter replaces the prior
raw=~2.0 stub for P4.1–P4.6 with form-derived raws (capped per BEAAA-213/214
signature-mode + form-variant + review-state cascade). When a PII handling
evidence pack is supplied, it also emits the paired
GOVERNANCE.PII.HANDLING_ATTESTATION raw from the deterministic ADR-0025
evidence ladder.

Output rows:
    {"dim_id": "P4.<n>", "raw": <0..4>, "benchmark": "p4_attestation_form_v0_1_locked"}
    {"dim_id": "GOVERNANCE.PII.HANDLING_ATTESTATION", "raw": <0..4>, "benchmark": "pii_attestation_evidence_pack_v0_1"}

The adapter does not silently downgrade on signature rejection — callers receive
the structured rejection so the pilot run can surface it rather than emitting
inflated stub raws.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.detectors.llmguard import ATTESTATION_DIM
from harness.detectors.llmguard.envelope import attach_pii_divergence_diagnostic
from harness.detectors.llmguard.scoring import EvidencePack, grade_attestation
from scripts import score_p4_attestation as p4

BENCHMARK_LABEL = "p4_attestation_form_v0_1_locked"
PII_ATTESTATION_BENCHMARK_LABEL = "pii_attestation_evidence_pack_v0_1"
PILLAR_DIM_IDS = ("P4.1", "P4.2", "P4.3", "P4.4", "P4.5", "P4.6")
GOVERNANCE_ATTESTATION_DIM_IDS = (*PILLAR_DIM_IDS, ATTESTATION_DIM)


class P4AttestationAdapterResult(dict):
    """Dict with two extra attributes for caller ergonomics: `.measurements`
    (list to feed into the merger) and `.scored` (full score_form payload)."""

    @property
    def measurements(self) -> list[dict[str, Any]]:
        return self["measurements"]

    @property
    def scored(self) -> dict[str, Any]:
        return self["scored"]


def attestation_to_measurements(
    form: dict[str, Any],
    *,
    ledger: dict[str, Any] | None = None,
    ledger_replay_divergence: dict[str, float] | None = None,
    pii_evidence_pack: dict[str, Any] | EvidencePack | None = None,
    pii_substrate_raw: float | None = None,
    evidence_root: Path | None = None,
    grader=None,
    now=None,
    review_state_overlay: dict[str, dict[str, Any]] | None = None,
) -> P4AttestationAdapterResult:
    """Score a P4 attestation form and emit a measurements list for the pipeline.

    If the P4 form is rejected (Q2 signature_mode insufficient for claimed
    tier), `measurements` is an empty list and `scored["rejected"]` is True.
    Callers must check `scored.get("rejected")` before treating the absence
    of measurements as a free pass — that path is what keeps a tamper-attempt
    from silently falling through to priors.

    `pii_evidence_pack` is optional so existing P4-only callers remain byte-
    compatible. When present, `pii_substrate_raw` must be present because the
    0.5 no-PII special case and divergence diagnostic depend on the paired
    ROBUSTNESS substrate dim.
    """
    scored = p4.score_form(
        form,
        ledger=ledger,
        ledger_replay_divergence=ledger_replay_divergence,
        evidence_root=evidence_root,
        grader=grader,
        now=now,
        review_state_overlay=review_state_overlay,
        raise_on_signature_insufficient=False,
    )
    measurements: list[dict[str, Any]] = []
    if not scored.get("rejected"):
        for dim_id in PILLAR_DIM_IDS:
            dim = scored["dims"].get(dim_id)
            if dim is None:
                continue
            measurements.append(
                {
                    "dim_id": dim_id,
                    "raw": float(dim["raw"]),
                    "benchmark": BENCHMARK_LABEL,
                }
            )
    if pii_evidence_pack is not None:
        pii_measurement, pii_scored = pii_attestation_to_measurement(
            pii_evidence_pack,
            substrate_raw=pii_substrate_raw,
        )
        measurements.append(pii_measurement)
        scored.setdefault("dims", {})[ATTESTATION_DIM] = pii_scored
        scored.setdefault("diagnostics", []).extend(pii_scored["diagnostics"])
    return P4AttestationAdapterResult(measurements=measurements, scored=scored)


def _evidence_pack_from_mapping(pack: dict[str, Any] | EvidencePack) -> EvidencePack:
    if isinstance(pack, EvidencePack):
        return pack
    return EvidencePack(
        dpa_signed=bool(pack.get("dpa_signed") or pack.get("signed_dpa")),
        dpia_filed=bool(pack.get("dpia_filed") or pack.get("dpia")),
        dsar_runbook_uri=str(pack.get("dsar_runbook_uri") or pack.get("dsar_runbook") or ""),
        dsar_sla_days=int(pack.get("dsar_sla_days") or pack.get("dsar_sla_days_max") or 9999),
        breach_notification_sla_h=int(
            pack.get("breach_notification_sla_h")
            or pack.get("breach_sla_72h")
            or 9999
        ),
        third_party_audit_kind=str(pack.get("third_party_audit_kind") or pack.get("third_party_audit") or "NONE"),
        policy_text_uri=str(pack.get("policy_text_uri") or pack.get("policy_text") or ""),
        no_pii_processing_attest=bool(pack.get("no_pii_processing_attest") or pack.get("no_pii_attestation")),
    )


def pii_attestation_to_measurement(
    evidence_pack: dict[str, Any] | EvidencePack,
    *,
    substrate_raw: float | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Emit the measured row for GOVERNANCE.PII.HANDLING_ATTESTATION.

    Deterministic rule execution against ADR-0025's existing rubric point:
    no judge model, no scoring math change, no output-shape change.
    """
    if substrate_raw is None:
        raise ValueError("pii_substrate_raw is required for PII attestation grading")

    result = grade_attestation(
        _evidence_pack_from_mapping(evidence_pack),
        substrate_raw=float(substrate_raw),
    )
    diagnostics: list[dict[str, Any]] = []
    attach_pii_divergence_diagnostic(
        diagnostics,
        substrate_raw=float(substrate_raw),
        attestation_raw=result.raw,
    )
    scored = {
        "tier": result.raw,
        "raw": result.raw,
        "gate_triggered": result.matched_cut,
        "cap_applied": None,
        "evidence_refs": [
            {"kind": key, "present": present}
            for key, present in sorted(result.evidence_inventory.items())
        ],
        "evidence_inventory": result.evidence_inventory,
        "diagnostics": diagnostics,
        "notes": [
            "deterministic_pii_evidence_pack_grade",
            "attestation_substrate_divergence_metadata_only",
        ],
    }
    measurement = {
        "dim_id": ATTESTATION_DIM,
        "raw": float(result.raw),
        "benchmark": PII_ATTESTATION_BENCHMARK_LABEL,
    }
    return measurement, scored
