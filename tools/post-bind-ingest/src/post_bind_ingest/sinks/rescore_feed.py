from __future__ import annotations

from datetime import datetime
from typing import Any

from .common import (
    DETECTOR_BUNDLE_VERSION,
    EVENT_TYPE_TO_PERIL,
    LOG_SCHEMA_VERSION,
    OVP_FALLBACK_WINDOW_DAYS,
    OVP_IN_SCOPE_PERILS,
    OVP_PRIMARY_WINDOW_DAYS,
    OVP_VERSION,
    POST_BIND_SINKS_VERSION,
    aggregate_peril_events,
    iso_window,
)


OVP_THRESHOLDS = {
    "P4": {
        "min_eligible_actions_30d": 10000,
        "min_eligible_actions_90d": 25000,
        "min_observed_events": 3,
        "ratio_trigger": 2.0,
        "absolute_excess_trigger_per_10k": 5.0,
        "ratio_severe_review": 3.0,
        "absolute_excess_severe_review_per_10k": 10.0,
        "prior_mass_actions": 2500,
    },
    "P5": {
        "min_eligible_actions_30d": 10000,
        "min_eligible_actions_90d": 25000,
        "min_observed_events": 3,
        "ratio_trigger": 1.75,
        "absolute_excess_trigger_per_10k": 3.0,
        "ratio_severe_review": 2.5,
        "absolute_excess_severe_review_per_10k": 6.0,
        "prior_mass_actions": 2500,
    },
}

PREDICTED_FREQ_PER_10K = {"P4": 4.0, "P5": 2.0}


def ovp_metrics_for(
    peril_code: str,
    eligible_actions: int,
    observed_events: int,
    *,
    window_days: int = OVP_PRIMARY_WINDOW_DAYS,
) -> dict[str, Any]:
    thresholds = OVP_THRESHOLDS[peril_code]
    predicted = PREDICTED_FREQ_PER_10K[peril_code]
    if eligible_actions <= 0:
        observed = 0.0
        credibility_weighted = predicted
    else:
        observed = (observed_events / eligible_actions) * 10000
        prior_mass = thresholds["prior_mass_actions"]
        alpha = prior_mass * (predicted / 10000)
        credibility_weighted = ((observed_events + alpha) / (eligible_actions + prior_mass)) * 10000
    pred_eff = max(predicted, 0.2)
    ratio = credibility_weighted / pred_eff if pred_eff else 0.0
    excess = max(0.0, credibility_weighted - predicted)
    min_actions_key = "min_eligible_actions_90d" if window_days == OVP_FALLBACK_WINDOW_DAYS else "min_eligible_actions_30d"
    credibility_gate = eligible_actions >= thresholds[min_actions_key] and observed_events >= thresholds["min_observed_events"]
    if not credibility_gate:
        status = "insufficient_data"
        recommendation = "none"
        delta = 0
    else:
        severe = (
            ratio >= thresholds["ratio_severe_review"]
            or excess >= thresholds["absolute_excess_severe_review_per_10k"]
        )
        formal = (
            ratio >= thresholds["ratio_trigger"]
            and excess >= thresholds["absolute_excess_trigger_per_10k"]
        )
        watch = (
            ratio >= thresholds["ratio_trigger"]
            or excess >= thresholds["absolute_excess_trigger_per_10k"]
        )
        status = "credible" if formal or severe else "watch"
        if severe:
            recommendation = "severe_review"
            delta = -2
        elif formal:
            recommendation = "formal_rescore_review"
            delta = -1
        elif watch:
            recommendation = "watch"
            delta = 0
        else:
            recommendation = "none"
            delta = 0
    return {
        "eligible_actions": eligible_actions,
        "observed_event_count": observed_events,
        "predicted_frequency_per_10k": round(predicted, 4),
        "observed_frequency_per_10k": round(observed, 4),
        "credibility_weighted_observed_frequency_per_10k": round(credibility_weighted, 4),
        "divergence_ratio": round(ratio, 4),
        "absolute_excess_per_10k": round(excess, 4),
        "credibility_status": status,
        "thresholds_applied": thresholds,
        "window_days": window_days,
        "shadow_score_delta_points": delta,
        "rescore_recommendation": recommendation,
    }


def build_peril_window_metrics(
    events: list[dict[str, Any]],
    dp_id: str,
    peril_code: str,
    peril_name: str,
    *,
    end_dt: datetime,
) -> dict[str, Any]:
    primary_window = iso_window(OVP_PRIMARY_WINDOW_DAYS, end_dt)
    fallback_window = iso_window(OVP_FALLBACK_WINDOW_DAYS, end_dt)
    primary = aggregate_peril_events(events, dp_id=dp_id, window=primary_window).get((dp_id, peril_code), {})
    primary_metrics = ovp_metrics_for(
        peril_code,
        int(primary.get("eligible_actions") or 0),
        int(primary.get("observed_event_count") or 0),
        window_days=OVP_PRIMARY_WINDOW_DAYS,
    )
    selected = primary
    selected_metrics = primary_metrics
    selected_window = primary_window
    fallback_used = False
    if primary_metrics["credibility_status"] == "insufficient_data":
        fallback = aggregate_peril_events(events, dp_id=dp_id, window=fallback_window).get((dp_id, peril_code), {})
        fallback_metrics = ovp_metrics_for(
            peril_code,
            int(fallback.get("eligible_actions") or 0),
            int(fallback.get("observed_event_count") or 0),
            window_days=OVP_FALLBACK_WINDOW_DAYS,
        )
        if fallback_metrics["credibility_status"] != "insufficient_data":
            selected = fallback
            selected_metrics = fallback_metrics
            selected_window = fallback_window
            fallback_used = True
    return {
        "peril_code": peril_code,
        "peril_name": peril_name,
        **selected_metrics,
        "signal_envelope_ids": selected.get("signal_envelope_ids", []),
        "review_window": {
            "mode": selected_window["mode"],
            "start_at": selected_window["start_at"],
            "end_at": selected_window["end_at"],
            "fallback_window_used": fallback_used,
        },
    }


def build_per_customer_rescore_feed(
    events: list[dict[str, Any]],
    dp_id: str,
    *,
    end_dt: datetime,
    pin_block: dict[str, Any],
) -> dict[str, Any]:
    perils = []
    for peril_code in OVP_IN_SCOPE_PERILS:
        peril_name = EVENT_TYPE_TO_PERIL["hallucination" if peril_code == "P4" else "prompt_injection"][1]
        perils.append(build_peril_window_metrics(events, dp_id, peril_code, peril_name, end_dt=end_dt))
    fallback_used = any(p["review_window"]["fallback_window_used"] for p in perils)
    selected_days = OVP_FALLBACK_WINDOW_DAYS if fallback_used else OVP_PRIMARY_WINDOW_DAYS
    window = iso_window(selected_days, end_dt)
    return {
        "sink_version": POST_BIND_SINKS_VERSION,
        "rescore_feed_version": OVP_VERSION,
        "dp_id": dp_id,
        "routing": "manual_underwriting_review_only",
        "auto_reprice": False,
        "methodology_ticket_ref": "BEAAA-5818",
        "window": {
            "mode": window["mode"],
            "fallback_mode": f"rolling_{OVP_FALLBACK_WINDOW_DAYS}d",
            "start_at": window["start_at"],
            "end_at": window["end_at"],
            "fallback_window_used": fallback_used,
        },
        "perils": perils,
        "shadow_total_delta_points": max(-6, sum(p["shadow_score_delta_points"] for p in perils)),
        "pin_block": {
            **pin_block,
            "log_schema_version": LOG_SCHEMA_VERSION,
            "detector_bundle_version": DETECTOR_BUNDLE_VERSION,
        },
    }
