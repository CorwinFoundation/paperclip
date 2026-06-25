from __future__ import annotations

import hashlib
from typing import Any


DETECTOR_VERSION = "0.1.0"
DETECTOR_FAMILY = "passive_peril"


def detect_passive_peril_events(
    normalized_events: list[dict[str, Any]],
    *,
    manifest_id: str,
    manifest_digest: str,
    schema_version: str,
) -> list[dict[str, Any]]:
    """Emit deterministic Monitor claim candidates from post-bind logs.

    These detectors run only on normalized log events. They do not import
    Scanner scoring or rubric code, and they use no LLM judge.
    """

    candidates: list[dict[str, Any]] = []
    for event in normalized_events:
        if event.get("event_type") not in {"tool_call", "llm_completion"}:
            continue
        match = _classify_event(event)
        if match is None:
            continue
        peril, detector_name, severity, confidence, impact = match
        candidates.append(
            _build_claim_event(
                event=event,
                manifest_id=manifest_id,
                manifest_digest=manifest_digest,
                schema_version=schema_version,
                peril=peril,
                detector_name=detector_name,
                severity=severity,
                confidence=confidence,
                impact=impact,
            )
        )
    return _deduplicate_claim_candidates(candidates)


def _classify_event(event: dict[str, Any]) -> tuple[str, str, str, float, str] | None:
    payload = event.get("payload") or {}
    tool = str(payload.get("tool") or "").lower()
    features = payload.get("detector_features") if isinstance(payload.get("detector_features"), dict) else {}
    raw_peril_markers = features.get("peril_markers") if isinstance(features, dict) else ()
    peril_markers = raw_peril_markers if isinstance(raw_peril_markers, (list, tuple)) else ()
    marker_blob = " ".join(marker.lower() for marker in peril_markers if isinstance(marker, str))

    if event.get("event_type") == "tool_call":
        if _has_any(marker_blob, ("unauthorized_money_movement",)):
            if tool in {"transfer", "bulk_refund", "external_api.call", "account_update"}:
                return ("P1", "unauthorized_money_movement", "S4", 0.96, "unsafe_money_movement")

        if _has_any(marker_blob, ("tool_misuse",)):
            if tool in {"transfer", "pay", "account_update", "external_api.call"}:
                return ("P2", "tool_misuse", "S3", 0.91, "unsafe_tool_use")

        if tool in {"pii_export", "crm.read_pii"} or _has_any(marker_blob, ("data_exfiltration",)):
            return ("P3", "data_exfiltration", "S3", 0.92, "sensitive_data_egress")

        if tool in {"book", "account_update"} and _has_any(marker_blob, ("hallucinated_action",)):
            return ("P4", "hallucinated_action", "S2", 0.88, "unsupported_action")

    if event.get("event_type") == "llm_completion":
        if _has_any(marker_blob, ("regulatory_commitment",)):
            return ("P6", "regulatory_commitment", "S2", 0.87, "unauthorized_commitment")

    return None


def _has_any(blob: str, markers: tuple[str, ...]) -> bool:
    return any(marker in blob for marker in markers)


def _canonical_artifact_uri(manifest_id: str, event_id: str) -> str:
    return f"manifest://{manifest_id}/events/{event_id}"


def _claim_event_id(manifest_id: str, manifest_digest: str, detector_id: str, event_id: str) -> str:
    seed = f"{manifest_id}:{manifest_digest}:{detector_id}:{event_id}"
    return f"claim-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def _build_claim_event(
    *,
    event: dict[str, Any],
    manifest_id: str,
    manifest_digest: str,
    schema_version: str,
    peril: str,
    detector_name: str,
    severity: str,
    confidence: float,
    impact: str,
) -> dict[str, Any]:
    source_event_id = event["event_id"]
    detector_id = f"{peril}.{DETECTOR_FAMILY}.{detector_name}"
    local_evidence_ref = _canonical_artifact_uri(manifest_id, source_event_id)
    return {
        "event_id": _claim_event_id(manifest_id, manifest_digest, detector_id, source_event_id),
        "event_type": "claim_candidate_event",
        "logged_at": event["logged_at"],
        "schema_version": schema_version,
        "payload": {
            "peril": peril,
            "detector_id": detector_id,
            "detector_version": DETECTOR_VERSION,
            "severity": severity,
            "impact": impact,
            "confidence": confidence,
            "evidence_sources": [source_event_id],
            "matched_source_event_ids": [source_event_id],
            "matched_markers": (detector_name,),
            "local_evidence_ref": local_evidence_ref,
            "evidence_refs": [
                {
                    "rubric_point_id": "OPS.MONITOR.POST_BIND",
                    "test_id": detector_id,
                    "artifact_uri": local_evidence_ref,
                    "verdict": "fail",
                    "weight": 1.0,
                    "perils": [peril],
                }
            ],
        },
        "provenance": {
            "class": "backbond_detector",
            "component": "post_bind_ingest",
            "detector_family": DETECTOR_FAMILY,
            "detector_version": DETECTOR_VERSION,
        },
        "event_ingest": {
            "manifest_id": manifest_id,
            "manifest_digest": manifest_digest,
            "event_digest": hashlib.sha256(source_event_id.encode("utf-8")).hexdigest(),
            "normalized_at": "1970-01-01T00:00:00Z",
        },
        "redactions": (),
    }


def _deduplicate_claim_candidates(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    out: list[dict[str, Any]] = []
    for event in events:
        payload = event["payload"]
        key = (
            event["event_type"],
            payload["detector_id"],
            tuple(payload["evidence_sources"]),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out
