"""BEAAA-5944 post-bind sink placement and OVP appendix tests."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

PORTAL_ROOT = Path(__file__).resolve().parent
POST_BIND_SRC = PORTAL_ROOT.parents[0] / "post-bind-ingest" / "src"
for entry in (PORTAL_ROOT, POST_BIND_SRC):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import portal
from post_bind_ingest.sinks import (
    OVP_FALLBACK_WINDOW_DAYS,
    OVP_IN_SCOPE_PERILS,
    OVP_PRIMARY_WINDOW_DAYS,
)


def _iso_days_ago(days: int) -> str:
    when = (datetime(2026, 6, 16, 16, 0, tzinfo=UTC) - timedelta(days=days)).replace(microsecond=0)
    return when.isoformat().replace("+00:00", "Z")


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
        "scanner_pins": {
            "scanner_version": portal.SCANNER_VERSION,
            "corpus_version": portal.CORPUS_VERSION,
            "rubric_version": portal.RUBRIC_VERSION,
        },
    }


def test_claims_queue_routes_governed_p4_events() -> None:
    service = portal.build_service()
    service.add_signal(_signal("sig-1", "hallucination"))

    queue = service.list_claims_queue("dp-1")
    item = queue["items"][0]

    assert queue["manual_only"] is True
    assert item["peril_code"] == "P4"
    assert item["threshold_policy_ref"] == "BEAAA-5817_v0.3"
    assert item["threshold_policy_governed"] is True
    assert item["pin_block"]["scanner_version"] == portal.SCANNER_VERSION


def test_claims_queue_marks_p5_as_telemetry_only() -> None:
    service = portal.build_service()
    service.add_signal(_signal("sig-1", "prompt_injection"))

    item = service.list_claims_queue("dp-1")["items"][0]

    assert item["peril_code"] == "P5"
    assert item["threshold_policy_ref"] is None
    assert item["threshold_policy_governed"] is False
    assert item["telemetry_manual_review_only"] is True


def test_rescore_feed_is_manual_p4_p5_and_uses_30d_window() -> None:
    service = portal.build_service()
    service.add_signal(_signal("stale", "hallucination", produced_at=_iso_days_ago(OVP_PRIMARY_WINDOW_DAYS + 5)))
    service.add_signal(_signal("fresh", "hallucination", produced_at=_iso_days_ago(2)))
    service.add_signal(_signal("fresh-p5", "prompt_injection", produced_at=_iso_days_ago(2)))

    feed = service.get_per_customer_rescore_feed("dp-1")
    by_code = {p["peril_code"]: p for p in feed["perils"]}

    assert feed["routing"] == "manual_underwriting_review_only"
    assert feed["auto_reprice"] is False
    assert [p["peril_code"] for p in feed["perils"]] == list(OVP_IN_SCOPE_PERILS)
    assert by_code["P4"]["observed_event_count"] == 1
    assert by_code["P4"]["review_window"]["mode"] == "rolling_30d"
    assert by_code["P4"]["review_window"]["fallback_window_used"] is False


def test_observed_vs_predicted_uses_90d_fallback_when_30d_is_not_credible() -> None:
    service = portal.build_service()
    for idx in range(3):
        service.add_signal(
            _signal(
                f"fallback-{idx}",
                "hallucination",
                produced_at=_iso_days_ago(OVP_PRIMARY_WINDOW_DAYS + 10 + idx),
            )
        )

    feed = service.get_per_customer_rescore_feed("dp-1")
    p4 = {p["peril_code"]: p for p in feed["perils"]}["P4"]
    ovp = service.get_underwriting_rollup("scan-1")["observed_vs_predicted"]

    assert p4["observed_event_count"] == 3
    assert p4["eligible_actions"] == 30000
    assert p4["review_window"]["mode"] == f"rolling_{OVP_FALLBACK_WINDOW_DAYS}d"
    assert ovp["window"]["mode"] == f"rolling_{OVP_FALLBACK_WINDOW_DAYS}d"
    assert ovp["window"]["fallback_window_used"] is True


def test_portfolio_calibration_feed_aggregates_across_customers() -> None:
    service = portal.build_service()
    service.add_signal(_signal("p4-a", "hallucination", dp_id="dp-a"))
    service.add_signal(_signal("p5-b", "prompt_injection", dp_id="dp-b"))

    feed = service.get_portfolio_calibration_feed()
    by_code = {p["peril_code"]: p for p in feed["perils"]}

    assert feed["contract_output_disposition"] == "outside_are_scanner_api_v1_export_only"
    assert feed["auto_rate_update"] is False
    assert by_code["P4"]["customer_count"] == 1
    assert by_code["P5"]["customer_count"] == 1


def test_observed_vs_predicted_guard_rejects_score_mutation() -> None:
    service = portal.build_service()
    original = service.get_per_customer_rescore_feed

    def mutating_feed(dp_id: str) -> dict[str, object]:
        service.score["are_total"] += 1
        return original(dp_id)

    service.get_per_customer_rescore_feed = mutating_feed  # type: ignore[method-assign]

    with pytest.raises(portal.PortalError) as err:
        service.get_underwriting_rollup("scan-1")
    assert err.value.code == "observed_vs_predicted_invariant_violated"


def test_internal_reports_render_ovp_but_dp_report_does_not() -> None:
    service = portal.build_service()
    service.add_signal(_signal("sig-1", "prompt_injection"))

    internal = service._render_internal_report("Internal UW")
    dp_html = service._render_dp_report("DP")

    assert "Observed vs predicted drift (post-bind, export-only)" in internal
    assert "manual underwriting review only" in internal
    assert "Observed vs predicted drift" not in dp_html
