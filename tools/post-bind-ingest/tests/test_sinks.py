from __future__ import annotations

from datetime import datetime, UTC

from post_bind_ingest.sinks import (
    POST_BIND_SINKS_VERSION,
    build_claims_queue_item,
    build_per_customer_rescore_feed,
    build_portfolio_calibration_feed,
    canonical_events_from_signal_envelopes,
)


PIN_BLOCK = {
    "scanner_version": "0.2.0",
    "corpus_version": "0.2.0",
    "rubric_version": "0.2.0",
}


def _signal(
    signal_id: str,
    event_type: str,
    *,
    produced_at: str = "2026-06-16T15:00:00Z",
    dp_id: str = "dp-1",
    weight: float = 2.0,
) -> dict[str, object]:
    return {
        "telemetry_api_version": "0.1",
        "event_rule_corpus_version": "0.1",
        "signal_envelope_id": signal_id,
        "dp_id": dp_id,
        "agent_call_id": f"call-{signal_id}",
        "produced_at": produced_at,
        "event_type": event_type,
        "rule_matches": [{"rule_id": "r1", "weight": weight}],
        "redacted_record_ref": f"portal-evidence://connector/redacted-records/{signal_id}",
        "scanner_pins": PIN_BLOCK,
    }


def test_claims_queue_consumes_canonical_post_bind_events() -> None:
    events = canonical_events_from_signal_envelopes([_signal("sig-1", "hallucination")])
    item = build_claims_queue_item(events[0], pin_block=PIN_BLOCK)

    assert events[0]["event_type"] == "claim_candidate_event"
    assert item["sink_version"] == POST_BIND_SINKS_VERSION
    assert item["peril_code"] == "P4"
    assert item["severity_tier"] == "S3"
    assert item["threshold_policy_governed"] is True
    assert item["pin_block"]["scanner_version"] == "0.2.0"


def test_rescore_and_calibration_feeds_share_canonical_events() -> None:
    events = list(
        canonical_events_from_signal_envelopes(
            [
                _signal("sig-1", "hallucination", produced_at="2026-06-16T15:00:00Z", dp_id="dp-1"),
                _signal("sig-2", "prompt_injection", produced_at="2026-06-16T15:01:00Z", dp_id="dp-2"),
            ]
        )
    )
    end_dt = datetime(2026, 6, 16, 16, 0, tzinfo=UTC)

    rescore = build_per_customer_rescore_feed(events, "dp-1", end_dt=end_dt, pin_block=PIN_BLOCK)
    portfolio = build_portfolio_calibration_feed(events, end_dt=end_dt, pin_block=PIN_BLOCK)

    assert [p["peril_code"] for p in rescore["perils"]] == ["P4", "P5"]
    assert {p["peril_code"] for p in portfolio["perils"]} == {"P4", "P5"}
    assert portfolio["contract_output_disposition"] == "outside_are_scanner_api_v1_export_only"
    assert portfolio["auto_rate_update"] is False
