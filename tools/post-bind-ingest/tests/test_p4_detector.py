from __future__ import annotations

from post_bind_ingest.normalizer import normalize_manifest
from post_bind_ingest.p4_detector import detect_p4_events
from post_bind_ingest.sinks import build_claims_queue_item


PIN_BLOCK = {
    "scanner_version": "0.4.1",
    "corpus_version": "unchanged",
    "rubric_version": "are-scanner-api-v1@v1.0",
}


def test_detect_p4_events_uses_structured_hallucination_features() -> None:
    normalized_events = [
        {
            "event_id": "llm-1",
            "event_type": "llm_completion",
            "payload": {
                "dp_id": "dp-1",
                "hallucination_signal": True,
                "grounding_verdict": "unsupported",
                "citation_verdict": "missing_citation",
                "redacted_record_ref": "manifest://m-1/events/llm-1",
            },
        }
    ]
    findings = detect_p4_events(
        normalized_events,
        manifest_id="m-1",
        manifest_digest="digest-1",
        schema_version="log-event-schema-v1.0",
    )

    assert len(findings) == 2
    assert {finding["payload"]["peril"] for finding in findings} == {"P4"}
    assert {finding["payload"]["mode"] for finding in findings} == {"shadow"}
    assert all(
        finding["payload"]["evidence_refs"][0]["artifact_uri"] == "manifest://m-1/events/llm-1"
        for finding in findings
    )


def test_normalize_manifest_appends_p4_claim_events() -> None:
    manifest = {
        "manifest_version": "1",
        "manifest_id": "m-p4",
        "customer_id": "cust-p4",
        "schema_version": "log-event-schema-v1.0",
        "issued_at": "2026-06-16T15:00:00Z",
        "events": [
            {
                "event_id": "llm-1",
                "event_type": "llm_completion",
                "logged_at": "2026-06-16T15:00:03Z",
                "payload": {
                    "dp_id": "dp-1",
                    "hallucination_signal": True,
                    "groundedness_score": 0.12,
                },
            },
        ],
        "signature": {"algorithm": "hmac-sha256", "value": "unused"},
    }

    events = normalize_manifest(manifest)
    claim_events = [event for event in events if event["event_type"] == "claim_candidate_event"]
    assert len(claim_events) == 1
    assert claim_events[0]["payload"]["detector_id"] == "P4.hallucination.normalized_hallucination_signal"


def _manifest_for_p4_event(*, event_id: str, positive: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "dp_id": "own-fleet",
        "response_received": True,
        "redacted_record_ref": f"manifest://retell-shadow/events/{event_id}",
    }
    if positive:
        payload.update(
            {
                "hallucination_signal": True,
                "grounding_verdict": "unsupported",
                "citation_verdict": "missing_citation",
                "hallucination_confidence": 0.93,
            }
        )
    else:
        payload.update(
            {
                "hallucination_signal": False,
                "grounding_verdict": "supported",
                "citation_verdict": "supported",
                "groundedness_score": 0.98,
            }
        )
    return {
        "manifest_version": "1",
        "manifest_id": f"retell-shadow-{event_id}",
        "customer_id": "retell-shadow",
        "schema_version": "log-event-schema-v1.0",
        "issued_at": "2026-06-16T15:00:00Z",
        "events": [
            {
                "event_type": "llm_completion",
                "event_id": event_id,
                "logged_at": "2026-06-16T15:00:05Z",
                "payload": payload,
            }
        ],
        "signature": {"algorithm": "sha256", "value": "unused"},
    }


def test_manifest_routes_known_positive_p4_to_shadow_claims_queue() -> None:
    normalized = normalize_manifest(_manifest_for_p4_event(event_id="llm-positive", positive=True))
    p4_events = [
        event
        for event in normalized
        if event["event_type"] == "claim_candidate_event" and event["payload"]["peril"] == "P4"
    ]
    assert len(p4_events) >= 1
    assert {event["payload"]["mode"] for event in p4_events} == {"shadow"}
    assert all(
        ref["artifact_uri"].startswith("manifest://retell-shadow-llm-positive/events/")
        for event in p4_events
        for ref in event["payload"]["evidence_refs"]
    )

    queue_item = build_claims_queue_item(p4_events[0], pin_block=PIN_BLOCK)
    assert queue_item["peril_code"] == "P4"
    assert queue_item["manual_only"] is True
    assert queue_item["threshold_policy_governed"] is True


def test_manifest_clean_own_fleet_replay_has_zero_p4_candidates() -> None:
    normalized = normalize_manifest(_manifest_for_p4_event(event_id="llm-clean", positive=False))
    p4_events = [
        event
        for event in normalized
        if event["event_type"] == "claim_candidate_event" and event["payload"]["peril"] == "P4"
    ]
    assert p4_events == []
