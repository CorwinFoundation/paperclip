from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


POST_BIND_SINKS_VERSION = "0.1"
LOG_SCHEMA_VERSION = "log-event-schema-v1.0"
LOG_SCHEMA_PATCH_VERSION = "v1.0.1"
DETECTOR_BUNDLE_VERSION = "post-bind-detectors-v0.1"
OVP_VERSION = "0.1"
OVP_METHODOLOGY_VERSION = "0.1"
OVP_CALIBRATION_DATE = "2026-06-16"
OVP_IN_SCOPE_PERILS = ("P4", "P5")
OVP_PRIMARY_WINDOW_DAYS = 30
OVP_FALLBACK_WINDOW_DAYS = 90
CLAIMS_THRESHOLD_POLICY_REF = "BEAAA-5817_v0.3"
CLAIMS_THRESHOLD_POLICY_GOVERNED_PERILS = {"P4", "P6"}

EVENT_TYPE_TO_PERIL = {
    "wrong_recipient": ("P1", "Wrong-recipient / misdirected action"),
    "unauthorized_commitment": ("P2", "Unauthorized commitment / runaway action"),
    "data_leak": ("P3", "Data leak (egress / PII / secret exfiltration)"),
    "hallucination": ("P4", "Hallucination (tool-grounded / cited-source)"),
    "prompt_injection": ("P5", "Prompt injection / adversarial input"),
    "regulatory_response": ("P6", "Regulatory response"),
}

DEFAULT_ELIGIBLE_ACTIONS_PER_EVENT = 10000


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def iso_window(days: int, end_dt: datetime) -> dict[str, Any]:
    end = end_dt.astimezone(UTC).replace(microsecond=0)
    start = end - timedelta(days=days)
    return {
        "mode": f"rolling_{days}d",
        "start_at": start.isoformat().replace("+00:00", "Z"),
        "end_at": end.isoformat().replace("+00:00", "Z"),
        "days": days,
    }


def severity_tier(aggregate_weight: float) -> str:
    if aggregate_weight >= 4.0:
        return "S4"
    if aggregate_weight >= 2.0:
        return "S3"
    if aggregate_weight >= 1.0:
        return "S2"
    if aggregate_weight > 0:
        return "S1"
    return "S0"


def canonical_events_from_signal_envelopes(signals: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    events: list[dict[str, Any]] = []
    for signal in signals:
        mapped = EVENT_TYPE_TO_PERIL.get(signal.get("event_type"))
        if not mapped:
            continue
        peril_code, peril_name = mapped
        aggregate_weight = sum(float(m.get("weight") or 0) for m in signal.get("rule_matches") or [])
        events.append(
            {
                "event_id": signal["signal_envelope_id"],
                "event_type": "claim_candidate_event",
                "logged_at": signal["produced_at"],
                "schema_version": LOG_SCHEMA_VERSION,
                "payload": {
                    "peril": peril_code,
                    "peril_name": peril_name,
                    "severity": severity_tier(aggregate_weight),
                    "aggregate_weight": aggregate_weight,
                    "eligible_actions": int(signal.get("eligible_actions") or DEFAULT_ELIGIBLE_ACTIONS_PER_EVENT),
                    "dp_id": signal["dp_id"],
                    "claim_id": signal.get("claim_id"),
                    "source_signal_envelope_id": signal["signal_envelope_id"],
                    "evidence_refs": [signal["redacted_record_ref"]],
                    "rule_matches": signal.get("rule_matches") or [],
                    "scanner_pins": signal.get("scanner_pins") or {},
                    "telemetry_api_version": signal.get("telemetry_api_version"),
                    "event_rule_corpus_version": signal.get("event_rule_corpus_version"),
                },
                "provenance": {
                    "class": "backbond_sink_adapter",
                    "component": "post_bind_ingest",
                    "sink_adapter_version": POST_BIND_SINKS_VERSION,
                },
                "event_ingest": {
                    "manifest_id": f"portal-signal:{signal['signal_envelope_id']}",
                    "normalized_at": signal["produced_at"],
                },
            }
        )
    return tuple(events)


def filter_events_for_window(
    events: list[dict[str, Any]],
    *,
    dp_id: str | None = None,
    window: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filtered = [
        event
        for event in events
        if event.get("event_type") == "claim_candidate_event"
        and (dp_id is None or event.get("payload", {}).get("dp_id") == dp_id)
    ]
    if not window:
        return filtered
    start = parse_iso(window["start_at"])
    end = parse_iso(window["end_at"])
    return [event for event in filtered if start <= parse_iso(event["logged_at"]) <= end]


def aggregate_peril_events(
    events: list[dict[str, Any]],
    *,
    dp_id: str | None = None,
    window: dict[str, Any] | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for event in filter_events_for_window(events, dp_id=dp_id, window=window):
        payload = event["payload"]
        peril_code = payload.get("peril")
        peril_name = payload.get("peril_name") or peril_code
        event_dp_id = payload["dp_id"]
        if not peril_code:
            continue
        key = (event_dp_id, peril_code)
        entry = out.setdefault(
            key,
            {
                "dp_id": event_dp_id,
                "peril_code": peril_code,
                "peril_name": peril_name,
                "eligible_actions": 0,
                "observed_event_count": 0,
                "signal_envelope_ids": [],
                "first_observed_at": event["logged_at"],
                "last_observed_at": event["logged_at"],
            },
        )
        entry["eligible_actions"] += int(payload.get("eligible_actions") or DEFAULT_ELIGIBLE_ACTIONS_PER_EVENT)
        entry["observed_event_count"] += 1
        entry["signal_envelope_ids"].append(payload["source_signal_envelope_id"])
        entry["last_observed_at"] = event["logged_at"]
    return out
