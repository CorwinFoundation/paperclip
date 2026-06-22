from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from post_bind_ingest.invariants import normalize_redaction_class

from .common import (
    CLAIMS_THRESHOLD_POLICY_GOVERNED_PERILS,
    CLAIMS_THRESHOLD_POLICY_REF,
    DETECTOR_BUNDLE_VERSION,
    LOG_SCHEMA_PATCH_VERSION,
    LOG_SCHEMA_VERSION,
    POST_BIND_SINKS_VERSION,
)

ALLOWED_PERILS = frozenset({"P1", "P2", "P3", "P4", "P5", "P6"})
ALLOWED_SEVERITY_CLASSES = frozenset({"S0", "S1", "S2", "S3", "S4"})
ALLOWED_CLAIM_KINDS = frozenset({"candidate", "submitted", "paid", "denied"})


class ClaimsQueueError(ValueError):
    """Raised when a claim candidate cannot be emitted to the tracker hook."""


def build_claims_queue_item(
    event: dict[str, Any],
    *,
    pin_block: dict[str, Any],
    policy_context: dict[str, Any] | None = None,
    scanner_runs: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    claim_id: str | None = None,
    claim_kind: str = "candidate",
    mode: str = "manual",
    dogfood: bool = False,
) -> dict[str, Any]:
    if event.get("event_type") != "claim_candidate_event":
        raise ClaimsQueueError("claims queue only accepts claim_candidate_event")
    payload = event["payload"]
    peril_code = _validate_enum(payload.get("peril"), ALLOWED_PERILS, "peril")
    severity_class = _validate_enum(
        payload.get("severity_class") or payload.get("severity"),
        ALLOWED_SEVERITY_CLASSES,
        "severity_class",
    )
    claim_kind = _validate_enum(payload.get("claim_kind") or claim_kind, ALLOWED_CLAIM_KINDS, "claim_kind")
    source_id = _claim_source_id(event, payload)
    event_t = event["logged_at"]
    bracket = _bracket_scanner_runs(
        event_t=event_t,
        payload=payload,
        policy_context=policy_context,
        scanner_runs=tuple(scanner_runs),
    )
    redaction_class = _redaction_class_map(event)
    policy_governed = peril_code in CLAIMS_THRESHOLD_POLICY_GOVERNED_PERILS
    if mode not in {"manual", "shadow"}:
        raise ClaimsQueueError("mode must be 'manual' or 'shadow'")
    return {
        "sink_version": POST_BIND_SINKS_VERSION,
        "mode": mode,
        "dogfood": bool(dogfood),
        "auto_pay": False,
        "bind": False,
        "indemnity": False,
        "claims_queue_item_id": source_id,
        "claim_event_id": source_id,
        "claim_id": _required_claim_id(claim_id or payload.get("claim_id")),
        "claim_kind": claim_kind,
        "signal_envelope_id": payload.get("source_signal_envelope_id"),
        "dp_id": payload.get("dp_id"),
        "policy_id": bracket.get("policy_id"),
        "agent_id": bracket.get("agent_id"),
        "customer_id": bracket.get("customer_id"),
        "peril_code": peril_code,
        "peril": peril_code,
        "peril_name": payload.get("peril_name") or peril_code,
        "severity_tier": severity_class,
        "severity_class": severity_class,
        "event_t": event_t,
        "scan_id_at_bind": bracket["scan_id_at_bind"],
        "scan_id_at_loss": bracket["scan_id_at_loss"],
        "redaction_class": redaction_class,
        "routing": "underwriter_and_claims_architect_review",
        "manual_only": True,
        "threshold_policy_ref": CLAIMS_THRESHOLD_POLICY_REF if policy_governed else None,
        "threshold_policy_governed": policy_governed,
        "telemetry_manual_review_only": not policy_governed,
        "policy_scope_note": (
            "BEAAA-5817-governed P4/P6 handling lane; manual-only review, no automatic coverage or repricing."
            if policy_governed
            else "Telemetry-only manual review outside BEAAA-5817 v0.3 P4/P6 threshold scope."
        ),
        "produced_at": event["logged_at"],
        "evidence_refs": payload["evidence_refs"],
        "evidence_refs_uri": payload.get("evidence_refs_uri"),
        "metadata": {
            "detector_id": payload.get("detector_id"),
            "detector_version": payload.get("detector_version"),
            "impact": payload.get("impact"),
            "confidence": payload.get("confidence"),
            "evidence_sources": payload.get("evidence_sources"),
            "matched_source_event_ids": payload.get("matched_source_event_ids"),
            "matched_markers": payload.get("matched_markers"),
            "rule_matches": payload.get("rule_matches"),
            "source_signal_envelope_id": payload.get("source_signal_envelope_id"),
            "scanner_pins": payload.get("scanner_pins") or {},
        },
        "pin_block": {
            **pin_block,
            "log_schema_version": LOG_SCHEMA_VERSION,
            "log_schema_patch_version": LOG_SCHEMA_PATCH_VERSION,
            "detector_bundle_version": DETECTOR_BUNDLE_VERSION,
        },
    }


def _claim_source_id(event: dict[str, Any], payload: dict[str, Any]) -> str:
    value = payload.get("source_signal_envelope_id") or event.get("event_id")
    if not isinstance(value, str) or not value:
        raise ClaimsQueueError("claim_candidate_event requires source_signal_envelope_id or event_id")
    return value


def _validate_enum(value: Any, allowed: frozenset[str], field_name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ClaimsQueueError(f"{field_name} must be one of {sorted(allowed)}")
    return value


def _required_claim_id(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ClaimsQueueError("claim_id is required and must be the claim-management-system identifier")
    return value


def _redaction_class_map(event: dict[str, Any]) -> dict[str, str]:
    payload = event.get("payload") or {}
    raw_map = event.get("redaction_class_map") or payload.get("redaction_class_map") or {}
    if not isinstance(raw_map, dict):
        raise ClaimsQueueError("redaction_class_map must be an object when present")
    redaction_class = {str(path): normalize_redaction_class(value) for path, value in raw_map.items()}
    for redaction in event.get("redactions") or ():
        if not isinstance(redaction, dict):
            raise ClaimsQueueError("redactions entries must be objects")
        path = redaction.get("path")
        redaction_value = redaction.get("redaction_class")
        if not isinstance(path, str) or not path:
            raise ClaimsQueueError("redactions entries require non-empty path")
        redaction_class[path] = normalize_redaction_class(redaction_value)
    return redaction_class


def _bracket_scanner_runs(
    *,
    event_t: str,
    payload: dict[str, Any],
    policy_context: dict[str, Any] | None,
    scanner_runs: tuple[dict[str, Any], ...],
) -> dict[str, str | None]:
    context = dict(policy_context or {})
    scan_id_at_bind = (
        context.get("scan_id_at_bind")
        or context.get("scan_id_priced")
        or context.get("scanner_run_id")
        or payload.get("scan_id_at_bind")
    )
    agent_id = context.get("agent_id") or payload.get("agent_id") or payload.get("dp_id")
    scan_id_at_loss = payload.get("scan_id_at_loss") or _latest_scan_id_at_loss(
        scanner_runs=scanner_runs,
        agent_id=agent_id,
        event_t=event_t,
    )
    if not isinstance(scan_id_at_bind, str) or not scan_id_at_bind:
        raise ClaimsQueueError("scan_id_at_bind is required for claim bracketing")
    if not isinstance(scan_id_at_loss, str) or not scan_id_at_loss:
        raise ClaimsQueueError("scan_id_at_loss is required for claim bracketing")
    return {
        "policy_id": _optional_str(context.get("policy_id") or payload.get("policy_id")),
        "agent_id": _optional_str(agent_id),
        "customer_id": _optional_str(context.get("customer_id") or payload.get("customer_id")),
        "scan_id_at_bind": scan_id_at_bind,
        "scan_id_at_loss": scan_id_at_loss,
    }


def _latest_scan_id_at_loss(
    *,
    scanner_runs: tuple[dict[str, Any], ...],
    agent_id: Any,
    event_t: str,
) -> str | None:
    if not isinstance(agent_id, str) or not agent_id:
        return None
    event_dt = _parse_iso(event_t)
    candidates = []
    for run in scanner_runs:
        if run.get("agent_id") != agent_id:
            continue
        run_timestamp = run.get("run_timestamp")
        scan_id = run.get("scan_id")
        if not isinstance(run_timestamp, str) or not isinstance(scan_id, str):
            continue
        run_dt = _parse_iso(run_timestamp)
        if run_dt <= event_dt:
            candidates.append((run_dt, scan_id))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _parse_iso(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except Exception as exc:
        raise ClaimsQueueError(f"invalid ISO-8601 timestamp: {value!r}") from exc


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
