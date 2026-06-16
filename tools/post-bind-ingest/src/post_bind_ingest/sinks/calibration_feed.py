from __future__ import annotations

from datetime import datetime
from typing import Any

from .common import (
    DETECTOR_BUNDLE_VERSION,
    LOG_SCHEMA_VERSION,
    OVP_PRIMARY_WINDOW_DAYS,
    POST_BIND_SINKS_VERSION,
    aggregate_peril_events,
    iso_window,
)


def build_portfolio_calibration_feed(
    events: list[dict[str, Any]],
    *,
    end_dt: datetime,
    pin_block: dict[str, Any],
) -> dict[str, Any]:
    window = iso_window(OVP_PRIMARY_WINDOW_DAYS, end_dt)
    aggregates = aggregate_peril_events(events, window=window)
    by_peril: dict[str, dict[str, Any]] = {}
    for (_dp_id, peril_code), row in aggregates.items():
        entry = by_peril.setdefault(
            peril_code,
            {
                "peril_code": peril_code,
                "peril_name": row["peril_name"],
                "customer_count": 0,
                "eligible_actions": 0,
                "observed_event_count": 0,
            },
        )
        entry["customer_count"] += 1
        entry["eligible_actions"] += row["eligible_actions"]
        entry["observed_event_count"] += row["observed_event_count"]
    for entry in by_peril.values():
        eligible = entry["eligible_actions"]
        entry["observed_frequency_per_10k"] = round(
            (entry["observed_event_count"] / eligible) * 10000, 4
        ) if eligible else 0.0
    return {
        "sink_version": POST_BIND_SINKS_VERSION,
        "consumer_contract": "actuary_quarterly_rates_yaml_input",
        "contract_output_disposition": "outside_are_scanner_api_v1_export_only",
        "auto_rate_update": False,
        "window": {
            "mode": window["mode"],
            "start_at": window["start_at"],
            "end_at": window["end_at"],
            "fallback_window_used": False,
        },
        "perils": [by_peril[k] for k in sorted(by_peril)],
        "pin_block": {
            **pin_block,
            "log_schema_version": LOG_SCHEMA_VERSION,
            "detector_bundle_version": DETECTOR_BUNDLE_VERSION,
        },
    }
