from __future__ import annotations

from datetime import datetime, UTC

import pytest

from post_bind_ingest.sinks import (
    ClaimsQueueError,
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
    events[0]["redactions"] = ({"path": "payload.evidence_refs", "redaction_class": "hash"},)
    item = build_claims_queue_item(
        events[0],
        pin_block=PIN_BLOCK,
        policy_context={
            "policy_id": "pol-1",
            "agent_id": "dp-1",
            "customer_id": "cust-1",
            "scan_id_at_bind": "00000000-0000-0000-0000-000000000001",
        },
        scanner_runs=[
            {
                "scan_id": "00000000-0000-0000-0000-000000000002",
                "agent_id": "dp-1",
                "run_timestamp": "2026-06-16T14:59:00Z",
            },
            {
                "scan_id": "00000000-0000-0000-0000-000000000003",
                "agent_id": "dp-1",
                "run_timestamp": "2026-06-16T15:01:00Z",
            },
        ],
        claim_id="claim-1",
    )

    assert events[0]["event_type"] == "claim_candidate_event"
    assert item["sink_version"] == POST_BIND_SINKS_VERSION
    assert item["mode"] == "manual"
    assert item["dogfood"] is False
    assert item["auto_pay"] is False
    assert item["bind"] is False
    assert item["indemnity"] is False
    assert item["peril_code"] == "P4"
    assert item["severity_tier"] == "S3"
    assert item["severity_class"] == "S3"
    assert item["claim_kind"] == "candidate"
    assert item["claim_id"] == "claim-1"
    assert item["threshold_policy_governed"] is True
    assert item["pin_block"]["scanner_version"] == "0.2.0"
    assert item["policy_id"] == "pol-1"
    assert item["agent_id"] == "dp-1"
    assert item["customer_id"] == "cust-1"
    assert item["scan_id_at_bind"] == "00000000-0000-0000-0000-000000000001"
    assert item["scan_id_at_loss"] == "00000000-0000-0000-0000-000000000002"
    assert item["redaction_class"] == {"payload.evidence_refs": "hash"}


def test_claims_queue_shadow_mode_is_no_pay_no_bind_for_dogfood() -> None:
    event = canonical_events_from_signal_envelopes([_signal("sig-shadow", "prompt_injection")])[0]
    item = build_claims_queue_item(
        event,
        pin_block=PIN_BLOCK,
        policy_context={
            "policy_id": "shadow-pol-1",
            "agent_id": "dp-1",
            "customer_id": "backbond-dogfood",
            "scan_id_at_bind": "scan-bind",
        },
        scanner_runs=[{"scan_id": "scan-loss", "agent_id": "dp-1", "run_timestamp": "2026-06-16T15:00:00Z"}],
        claim_id="claim-shadow-1",
        mode="shadow",
        dogfood=True,
    )

    assert item["mode"] == "shadow"
    assert item["dogfood"] is True
    assert item["auto_pay"] is False
    assert item["bind"] is False
    assert item["indemnity"] is False


def test_claims_queue_validates_tracker_enums() -> None:
    event = canonical_events_from_signal_envelopes([_signal("sig-1", "hallucination")])[0]
    event["payload"]["peril"] = "P7"

    with pytest.raises(ClaimsQueueError, match="peril must be one of"):
        build_claims_queue_item(
            event,
            pin_block=PIN_BLOCK,
            policy_context={"scan_id_at_bind": "scan-bind", "agent_id": "dp-1"},
            scanner_runs=[{"scan_id": "scan-loss", "agent_id": "dp-1", "run_timestamp": "2026-06-16T15:00:00Z"}],
            claim_id="claim-1",
        )

    event = canonical_events_from_signal_envelopes([_signal("sig-2", "hallucination")])[0]
    event["payload"]["severity"] = "S5"
    with pytest.raises(ClaimsQueueError, match="severity_class must be one of"):
        build_claims_queue_item(
            event,
            pin_block=PIN_BLOCK,
            policy_context={"scan_id_at_bind": "scan-bind", "agent_id": "dp-1"},
            scanner_runs=[{"scan_id": "scan-loss", "agent_id": "dp-1", "run_timestamp": "2026-06-16T15:00:00Z"}],
            claim_id="claim-2",
        )

    event = canonical_events_from_signal_envelopes([_signal("sig-3", "hallucination")])[0]
    with pytest.raises(ClaimsQueueError, match="claim_kind must be one of"):
        build_claims_queue_item(
            event,
            pin_block=PIN_BLOCK,
            policy_context={"scan_id_at_bind": "scan-bind", "agent_id": "dp-1"},
            scanner_runs=[{"scan_id": "scan-loss", "agent_id": "dp-1", "run_timestamp": "2026-06-16T15:00:00Z"}],
            claim_id="claim-3",
            claim_kind="opened",
        )


def test_claims_queue_requires_bracketing_fields() -> None:
    event = canonical_events_from_signal_envelopes([_signal("sig-1", "prompt_injection")])[0]

    with pytest.raises(ClaimsQueueError, match="scan_id_at_bind is required"):
        build_claims_queue_item(
            event,
            pin_block=PIN_BLOCK,
            scanner_runs=[{"scan_id": "scan-loss", "agent_id": "dp-1", "run_timestamp": "2026-06-16T15:00:00Z"}],
            claim_id="claim-1",
        )

    with pytest.raises(ClaimsQueueError, match="scan_id_at_loss is required"):
        build_claims_queue_item(
            event,
            pin_block=PIN_BLOCK,
            policy_context={"scan_id_at_bind": "scan-bind", "agent_id": "dp-1"},
            scanner_runs=[{"scan_id": "scan-future", "agent_id": "dp-1", "run_timestamp": "2026-06-16T15:01:00Z"}],
            claim_id="claim-2",
        )


def test_claims_queue_requires_claim_management_identifier() -> None:
    event = canonical_events_from_signal_envelopes([_signal("sig-1", "hallucination")])[0]

    with pytest.raises(ClaimsQueueError, match="claim_id is required"):
        build_claims_queue_item(
            event,
            pin_block=PIN_BLOCK,
            policy_context={"scan_id_at_bind": "scan-bind", "agent_id": "dp-1"},
            scanner_runs=[{"scan_id": "scan-loss", "agent_id": "dp-1", "run_timestamp": "2026-06-16T15:00:00Z"}],
        )


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
