from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .continuity_gate import ContinuityGateDecision


DECISION_TABLE_REVISION = "BEAAA-18392/v0.1"
DECISION_TABLE_SHA256 = "38f2fcbea2f93c7592029762bd11f2680bf2803b0a2e65a7eaa32865eb6c3c6d"
TRACE_RULESET_VERSION = "passive-monitor-evidence-chain@0.1.0"

_SEVERITY_CONSEQUENCES = {
    "S1": ("unchanged", "none", "post_audit", "PM_PERIL_S1"),
    "S2": ("no_increase", "required", "human_underwriting", "PM_PERIL_S2"),
    "S3": (
        "suspended_affected_action_class",
        "required",
        "human_underwriting",
        "PM_PERIL_S3",
    ),
    "S4": ("suspended", "required", "human_underwriting", "PM_PERIL_S4"),
}


class EvidenceChainTraceError(ValueError):
    """Raised when a deterministic evidence-chain trace cannot be reproduced."""


@dataclass(frozen=True)
class EvidenceChainTrace:
    tested_failure_mode: Mapping[str, Any]
    production_exposure: Mapping[str, Any]
    control_reliance_event: Mapping[str, Any]
    redacted_incident_evidence: Mapping[str, Any]
    consequence: Mapping[str, Any]
    audit_record: Mapping[str, Any]
    ruleset_version: str = TRACE_RULESET_VERSION
    decision_table_revision: str = DECISION_TABLE_REVISION
    decision_table_sha256: str = DECISION_TABLE_SHA256

    def to_dict(self) -> dict[str, Any]:
        return {
            "tested_failure_mode": dict(self.tested_failure_mode),
            "production_exposure": dict(self.production_exposure),
            "control_reliance_event": dict(self.control_reliance_event),
            "redacted_incident_evidence": dict(self.redacted_incident_evidence),
            "consequence": dict(self.consequence),
            "audit_record": dict(self.audit_record),
            "ruleset_version": self.ruleset_version,
            "decision_table_revision": self.decision_table_revision,
            "decision_table_sha256": self.decision_table_sha256,
        }


def build_evidence_chain_trace(
    *,
    tested_failure_mode: Mapping[str, Any],
    production_event: Mapping[str, Any],
    control_reliance_event: Mapping[str, Any],
    claim_candidate: Mapping[str, Any],
    continuity: ContinuityGateDecision,
    decided_at: str,
) -> EvidenceChainTrace:
    """Join one pre-deploy failure mode to a deterministic runtime consequence.

    This is a Passive Monitor trace only. It does not import or mutate Scanner
    scoring, and it deliberately carries hashes/references instead of raw vendor
    content.
    """
    candidate = _mapping(claim_candidate.get("payload"), "claim_candidate.payload")
    source = _mapping(production_event.get("payload"), "production_event.payload")
    reliance = _mapping(control_reliance_event.get("payload"), "control_reliance_event.payload")

    evidence_refs = candidate.get("evidence_refs")
    source_ids = candidate.get("matched_source_event_ids")
    severity = str(candidate.get("severity") or "")
    if not evidence_refs or not source_ids:
        raise EvidenceChainTraceError("decision-sufficient redacted evidence references are required")
    if severity not in _SEVERITY_CONSEQUENCES:
        raise EvidenceChainTraceError(f"unsupported deterministic severity: {severity!r}")

    if continuity.gap_state.active:
        cap_posture, rescan_status, review_lane, reason_code = (
            "suspended",
            "required_after_recovery",
            "human_underwriting",
            _continuity_reason_code(continuity),
        )
        eligibility = "no_warranty_remedy_eligibility_for_affected_window"
    else:
        cap_posture, rescan_status, review_lane, reason_code = _SEVERITY_CONSEQUENCES[severity]
        eligibility = (
            "suspended_for_affected_action_class"
            if severity == "S3"
            else "suspended_for_deployment"
            if severity == "S4"
            else "existing_eligibility_pending_review"
        )

    redacted = {
        "source_event_ids": list(source_ids),
        "evidence_refs": list(evidence_refs),
        "manifest_digest": _required(
            _mapping(claim_candidate.get("event_ingest"), "claim_candidate.event_ingest"),
            "manifest_digest",
        ),
        "detector_id": _required(candidate, "detector_id"),
        "detector_version": _required(candidate, "detector_version"),
        "severity": severity,
        "redactions": list(production_event.get("redactions") or ()),
    }
    exposure = {
        "tenant_id": continuity.gap_state.tenant_id,
        "agent_id": continuity.gap_state.agent_id,
        "deployment_id": continuity.gap_state.deployment_id,
        "action_class": _required(source, "action_class"),
        "tool_name": source.get("tool_name"),
        "event_id": _required(production_event, "event_id"),
    }
    reliance_summary = {
        "event_id": _required(control_reliance_event, "event_id"),
        "control_id": _required(reliance, "control_id"),
        "relied_on": bool(_required(reliance, "relied_on")),
        "authority_result": _required(reliance, "authority_result"),
    }
    consequence = {
        "warranty_eligibility": eligibility,
        "warranty_cap_posture": cap_posture,
        "rescan_status": rescan_status,
        "review_lane": review_lane,
        "reason_code": reason_code,
        "claim_candidate_only": True,
        "numeric_cap": None,
    }
    audit_record = {
        "recorded_at": decided_at,
        "predicate_inputs": {
            "continuity_status": continuity.continuity_status,
            "continuity_reasons": list(continuity.gap_state.reasons),
            "severity": severity,
            "action_class": exposure["action_class"],
            "relied_on": reliance_summary["relied_on"],
        },
        "reason_code": reason_code,
        "ruleset_version": TRACE_RULESET_VERSION,
        "decision_table_revision": DECISION_TABLE_REVISION,
        "evidence_hashes": [redacted["manifest_digest"]],
        "evidence_references": list(evidence_refs),
        "prior_state": "eligible_under_existing_approved_terms",
        "resulting_state": consequence,
    }
    return EvidenceChainTrace(
        tested_failure_mode=dict(tested_failure_mode),
        production_exposure=exposure,
        control_reliance_event=reliance_summary,
        redacted_incident_evidence=redacted,
        consequence=consequence,
        audit_record=audit_record,
    )


def _continuity_reason_code(continuity: ContinuityGateDecision) -> str:
    reasons = set(continuity.gap_state.reasons)
    if "no_sequence_data" in reasons:
        return "PM_NO_LOGS"
    if "heartbeat_stale" in reasons:
        return "PM_HEARTBEAT_STALE"
    if "heartbeat_missing" in reasons:
        return "PM_HEARTBEAT_MISSING"
    if "batch_sequence_gap" in reasons:
        return "PM_SEQUENCE_GAP"
    return "PM_SCHEMA_INVALID"


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvidenceChainTraceError(f"{name} must be an object")
    return value


def _required(value: Mapping[str, Any], field: str) -> Any:
    out = value.get(field)
    if out is None or out == "":
        raise EvidenceChainTraceError(f"{field} is required")
    return out
