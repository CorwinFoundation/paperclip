"""
Back-compat shim: translate ARE score envelope dict field names to OTel attribute names.

Invoked at span-export time only. The scoring pipeline and Underwriter rating engine
continue to read the existing v1.0.3 field names — this shim is NOT in the hot path.

Semconv pin: opentelemetry-semantic-conventions@1.26.0 GenAI (incubating).
ARE vendor namespace: ifa.are.*
Contract: are-scanner-api-v1@v1.0.4
BEAAA-818
"""

from __future__ import annotations

import json
from typing import Any

# Mapping: v1.0.3 envelope key → OTel attribute name
# Flat top-level fields only. Nested fields handled by specific helpers below.
_ENVELOPE_MAP: dict[str, str] = {
    "are_total": "ifa.are.ari_total",
    "band": "ifa.are.band",
    "confidence": "ifa.are.confidence",
    "scanner_version": "ifa.are.scanner_version",
    "corpus_version": "ifa.are.corpus_version",
    "seed": "ifa.are.seed",
}

_SUBSCORE_MAP: dict[str, str] = {
    "GOVERNANCE": "ifa.are.subscore.governance",
    "ROBUSTNESS": "ifa.are.subscore.robustness",
    "BIAS_FAIRNESS": "ifa.are.subscore.bias_fairness",
    "OPS": "ifa.are.subscore.ops",
}

_EVIDENCE_MAP: dict[str, str] = {
    "rubric_point_id": "ifa.are.rubric_point_id",
    "test_id": "ifa.are.test_id",
    "artifact_uri": "ifa.are.artifact_uri",
    "verdict": "ifa.are.evidence.verdict",
    "weight": "ifa.are.evidence.weight",
    "review_state": "ifa.are.evidence.review_state",
    "tier": "ifa.are.evidence.tier",
    "na_justification": "ifa.are.evidence.na_justification",
}


def envelope_to_span_attrs(envelope: dict[str, Any]) -> dict[str, Any]:
    """Return OTel span attributes for a Layer-1 scan span.

    The returned dict is suitable for passing to span.set_attributes().
    Legacy field names are NOT included — callers that need both call
    envelope_to_span_attrs() and read the original envelope separately.
    """
    attrs: dict[str, Any] = {}

    for old_key, new_key in _ENVELOPE_MAP.items():
        if old_key in envelope:
            attrs[new_key] = envelope[old_key]

    subscores = envelope.get("subscores", {})
    for pillar, attr_name in _SUBSCORE_MAP.items():
        if pillar in subscores:
            attrs[attr_name] = subscores[pillar]

    uncovered = envelope.get("uncovered_dimensions")
    if uncovered is not None:
        attrs["ifa.are.uncovered_dimensions"] = uncovered

    model_versions = envelope.get("model_versions")
    if model_versions:
        attrs["ifa.are.model_versions"] = json.dumps(model_versions, sort_keys=True)
        detectors = model_versions.get("detectors")
        if detectors:
            attrs["ifa.are.model_versions.detectors"] = (
                detectors if isinstance(detectors, list) else [detectors]
            )
        primary_judge = model_versions.get("primary_judge")
        if primary_judge:
            attrs["gen_ai.request.model"] = primary_judge

    verdict_block = envelope.get("verdict", {})
    bind_eligibility = verdict_block.get("bind_eligibility") if verdict_block else None
    if bind_eligibility is not None:
        attrs["ifa.are.verdict.bind_eligibility"] = bind_eligibility

    return attrs


def evidence_ref_to_event_attrs(ref: dict[str, Any]) -> dict[str, Any]:
    """Return OTel span event attributes for a single evidence_refs[] entry.

    Emit as span.add_event("are.evidence", attributes=evidence_ref_to_event_attrs(ref)).
    """
    attrs: dict[str, Any] = {}
    for old_key, new_key in _EVIDENCE_MAP.items():
        if old_key in ref:
            attrs[new_key] = ref[old_key]
    return attrs


def add_evidence_events(span: Any, evidence_refs: list[dict[str, Any]]) -> None:
    """Add all evidence_refs[] entries as span events to the given OTel span."""
    for ref in evidence_refs:
        span.add_event("are.evidence", attributes=evidence_ref_to_event_attrs(ref))
