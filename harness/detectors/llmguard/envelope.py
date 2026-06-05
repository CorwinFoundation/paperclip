"""evidence_refs[] + diagnostics integration for the PII activation.

Three thin helpers that the scoring pipeline calls when populating the
contract envelope for a scan containing the two PII rubric_point_ids:

* ``substrate_artifact_uri(scan_id, probe_id)``  →  canonical per-probe
  detection trace URI (ROBUSTNESS.PII.SCAN_TIME_DETECTION).
* ``attestation_artifact_uri(scan_id, attestation_id)``  →  canonical
  attestation-pack URI (GOVERNANCE.PII.HANDLING_ATTESTATION).
* ``attach_pii_divergence_diagnostic(diagnostics, substrate_raw,
  attestation_raw)``  →  if the divergence condition fires, append the
  ``attestation_substrate_divergence`` flag entry to the diagnostics
  block. Metadata-only per ADR-0022 §composition rule — never enters
  scoring math.

These are wrap-only thin glue — no rubric, scoring math, or contract
schema decisions live here. The cuts that drive divergence emission are
declared canonically in ``rubric/probe_mappings/pii_attestation.yaml::
composition_diagnostic`` and ``harness/detectors/llmguard/__init__.py``.
"""
from __future__ import annotations

from typing import Any, Dict, List

from harness.detectors.llmguard import (
    DIVERGENCE_FLAG_NAME,
    SUBSTRATE_DIM,
    ATTESTATION_DIM,
    divergence_flag,
)


SUBSTRATE_ARTIFACT_TEMPLATE = "artifacts/{scan_id}/probes/pii_substrate/{probe_id}.json"
ATTESTATION_ARTIFACT_TEMPLATE = (
    "artifacts/{scan_id}/attestations/pii_handling/{attestation_id}.json"
)


def substrate_artifact_uri(scan_id: str, probe_id: str) -> str:
    """Canonical artifact_uri for one per-probe substrate detection
    trace (ROBUSTNESS.PII.SCAN_TIME_DETECTION).

    Pinned in ADR-0022 §rubric_point_id spec text — every measured row
    MUST point at a reachable URI per CTO Hard Guarantee #2.
    """
    if not scan_id or not probe_id:
        raise ValueError("substrate_artifact_uri requires non-empty scan_id and probe_id")
    return SUBSTRATE_ARTIFACT_TEMPLATE.format(scan_id=scan_id, probe_id=probe_id)


def attestation_artifact_uri(scan_id: str, attestation_id: str) -> str:
    """Canonical artifact_uri for one attestation pack
    (GOVERNANCE.PII.HANDLING_ATTESTATION).
    """
    if not scan_id or not attestation_id:
        raise ValueError(
            "attestation_artifact_uri requires non-empty scan_id and attestation_id"
        )
    return ATTESTATION_ARTIFACT_TEMPLATE.format(
        scan_id=scan_id, attestation_id=attestation_id
    )


def attach_pii_divergence_diagnostic(
    diagnostics: List[Dict[str, Any]],
    *,
    substrate_raw: float,
    attestation_raw: float,
) -> List[Dict[str, Any]]:
    """Append the ``attestation_substrate_divergence`` diagnostic entry
    when the divergence condition fires. No-op otherwise.

    Mutates and returns the ``diagnostics`` list for ergonomic chaining
    with other diagnostic appenders (e.g. ``coverage_caveat``). The
    diagnostic entry shape mirrors ``coverage_caveat`` for auditor
    consistency.

    Per ADR-0022 §composition rule the flag is metadata-only — UW pricing
    engine consumes it (Option C lever, BEAAA-225 precedent); the scoring
    engine MUST NOT route this value into pillar means or ARE total.
    """
    if not divergence_flag(substrate_raw, attestation_raw):
        return diagnostics
    diagnostics.append({
        "flag": DIVERGENCE_FLAG_NAME,
        "paired_dims": [SUBSTRATE_DIM, ATTESTATION_DIM],
        "substrate_raw": round(float(substrate_raw), 4),
        "attestation_raw": round(float(attestation_raw), 4),
        "scoring_math_impact": "none (metadata-only; Option C lever)",
        "spec_authority": "ADR-0022 §composition rule",
        "activation_authority": "ADR-0025 §wiring",
    })
    return diagnostics
