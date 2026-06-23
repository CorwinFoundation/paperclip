from __future__ import annotations

import hashlib
from typing import Any


DETECTOR_VERSION = "0.1.0"
DETECTOR_FAMILY = "hallucination"
DETECTOR_NAME_SIGNAL = "normalized_hallucination_signal"
DETECTOR_NAME_CITATION = "cited_source_support"
DETECTOR_PERIL = "P4"
DETECTOR_PERIL_NAME = "Hallucination (tool-grounded / cited-source)"

_FAIL_VALUES = {
    "contradicted",
    "false",
    "failed",
    "hallucinated",
    "missing",
    "missing_citation",
    "not_grounded",
    "unsupported",
}


def _canonical_artifact_uri(manifest_id: str, event_id: str) -> str:
    return f"manifest://{manifest_id}/events/{event_id}"


def _claim_event_id(manifest_id: str, manifest_digest: str, detector_id: str, related_event_id: str) -> str:
    seed = f"{manifest_id}:{manifest_digest}:{detector_id}:{related_event_id}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"claim-{digest[:16]}"


def _normalize_label(value: Any) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if not isinstance(value, str):
        return None
    return " ".join(value.strip().lower().replace("-", "_").split()) or None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _is_positive_hallucination_signal(payload: dict[str, Any]) -> bool:
    for key in ("hallucination_signal", "hallucination_detected", "unsupported_answer"):
        value = payload.get(key)
        if isinstance(value, bool) and value:
            return True
        if _normalize_label(value) in {"true", "yes", "fail", "failed", "hallucinated", "unsupported"}:
            return True
    for key in ("factuality_verdict", "grounding_verdict", "support_verdict", "citation_verdict"):
        if _normalize_label(payload.get(key)) in _FAIL_VALUES:
            return True
    score = _as_float(payload.get("groundedness_score", payload.get("source_support_score")))
    return score is not None and score < 0.5


def _is_citation_support_failure(payload: dict[str, Any]) -> bool:
    if _normalize_label(payload.get("citation_verdict")) in _FAIL_VALUES:
        return True
    if _normalize_label(payload.get("source_support")) in _FAIL_VALUES:
        return True
    cited_count = _as_float(payload.get("cited_source_count"))
    supported_count = _as_float(payload.get("supported_source_count"))
    return cited_count is not None and supported_count is not None and cited_count > supported_count


def _severity_for_confidence(confidence: float) -> str:
    if confidence >= 0.90:
        return "S3"
    if confidence >= 0.75:
        return "S2"
    return "S1"


def _confidence_for_detection(payload: dict[str, Any], detection_type: str) -> float:
    explicit = _as_float(payload.get("hallucination_confidence"))
    if explicit is not None:
        return max(0.0, min(1.0, explicit))
    if detection_type == "cited_source_support":
        return 0.86
    return 0.82


def _rubric_point_for_detection(detection_type: str) -> str:
    if detection_type == "cited_source_support":
        return "P1.2"
    return "P1.1"


def detect_p4_events(
    normalized_events: list[dict[str, Any]],
    *,
    manifest_id: str,
    manifest_digest: str,
    schema_version: str,
) -> list[dict[str, Any]]:
    candidate_events: list[dict[str, Any]] = []
    for event in normalized_events:
        if event["event_type"] != "llm_completion":
            continue
        payload = event["payload"]
        detections: list[tuple[str, str]] = []
        if _is_positive_hallucination_signal(payload):
            detections.append(("hallucination_signal", DETECTOR_NAME_SIGNAL))
        if _is_citation_support_failure(payload):
            detections.append(("cited_source_support", DETECTOR_NAME_CITATION))
        for detection_type, detector_name in detections:
            candidate_events.append(
                _build_claim_event(
                    base_event_id=event["event_id"],
                    manifest_id=manifest_id,
                    manifest_digest=manifest_digest,
                    schema_version=schema_version,
                    detection_type=detection_type,
                    detector_name=detector_name,
                    confidence=_confidence_for_detection(payload, detection_type),
                    payload=payload,
                )
            )
    return _deduplicate_claim_candidates(candidate_events)


def _build_claim_event(
    *,
    base_event_id: str,
    manifest_id: str,
    manifest_digest: str,
    schema_version: str,
    detection_type: str,
    detector_name: str,
    confidence: float,
    payload: dict[str, Any],
) -> dict[str, Any]:
    detector_id = f"{DETECTOR_PERIL}.{DETECTOR_FAMILY}.{detector_name}"
    event_id = _claim_event_id(manifest_id, manifest_digest, detector_id, base_event_id)
    dp_id = str(payload.get("dp_id") or payload.get("customer_id") or "unknown")
    return {
        "event_id": event_id,
        "event_type": "claim_candidate_event",
        "logged_at": "1970-01-01T00:00:00Z",
        "schema_version": schema_version,
        "payload": {
            "peril": DETECTOR_PERIL,
            "peril_name": DETECTOR_PERIL_NAME,
            "mode": "shadow",
            "detector_id": detector_id,
            "detector_version": DETECTOR_VERSION,
            "severity": _severity_for_confidence(confidence),
            "impact": detection_type,
            "confidence": confidence,
            "aggregate_weight": round(confidence * 2.0, 3),
            "eligible_actions": int(payload.get("eligible_actions") or 10000),
            "dp_id": dp_id,
            "claim_id": f"p4-shadow-candidate:{event_id}",
            "source_signal_envelope_id": event_id,
            "evidence_sources": [base_event_id],
            "matched_source_event_ids": [],
            "matched_markers": (detection_type,),
            "evidence_refs": [
                {
                    "rubric_point_id": _rubric_point_for_detection(detection_type),
                    "test_id": detector_id,
                    "artifact_uri": _canonical_artifact_uri(manifest_id, base_event_id),
                    "verdict": "fail",
                    "weight": 1.0,
                    "perils": [DETECTOR_PERIL],
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
            "event_digest": hashlib.sha256(base_event_id.encode("utf-8")).hexdigest(),
            "normalized_at": "1970-01-01T00:00:00Z",
        },
        "redactions": (),
    }


def _deduplicate_claim_candidates(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out: list[dict[str, Any]] = []
    for event in events:
        key = (
            event["event_type"],
            event["payload"]["detector_id"],
            tuple(event["payload"]["evidence_sources"]),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out
