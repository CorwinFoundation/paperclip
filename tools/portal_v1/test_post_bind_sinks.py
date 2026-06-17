"""BEAAA-5867 post-bind sinks + observed-vs-predicted appendix tests.

Rule execution under scanner-implementation-supervision-v1.1:
export-only sinks over telemetry signal envelopes; no are-scanner-api-v1@v1.0
Output mutation.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from datetime import timedelta
from http.server import ThreadingHTTPServer

import pytest

import portal
from portal import PortalHandler, build_service
from post_bind_sinks_v0_1 import (
    DETECTOR_BUNDLE_VERSION,
    LOG_SCHEMA_VERSION,
    OVP_FALLBACK_WINDOW_DAYS,
    OVP_IN_SCOPE_PERILS,
    OVP_PRIMARY_WINDOW_DAYS,
    POST_BIND_SINKS_VERSION,
)
from telemetry_fixtures_v0_1 import FIXTURE_CASES
from test_portal import enroll_ready_dp, submit_all_tasks
from test_telemetry import _record


@pytest.fixture
def service(tmp_path):
    svc = build_service(tmp_path)
    yield svc
    svc.close()


def _produce_score(service):
    signing_key, dp, enrollment = enroll_ready_dp(service)
    scan = service.trigger_scan(dp["dp_id"])
    submit_all_tasks(service, signing_key, scan, enrollment["connector_id"])
    score = service.produce_score(scan["scan_id"])
    return signing_key, dp, enrollment, scan, score


def _emit_case(service, dp, enrollment, case_id: str, suffix: str = ""):
    case = next(c for c in FIXTURE_CASES if c["id"] == case_id)
    return service.submit_redacted_record(
        _record(
            enrollment["connector_id"],
            dp["dp_id"],
            f"post-bind-{case_id}{suffix}",
            case["redacted_output_text"],
        )
    )


def _retime_signal(service, signal_envelope_id: str, produced_at: str) -> None:
    row = service.db.execute(
        "SELECT envelope_json FROM signal_envelopes WHERE id = ?",
        (signal_envelope_id,),
    ).fetchone()
    envelope = json.loads(row["envelope_json"])
    envelope["produced_at"] = produced_at
    service.db.execute(
        "UPDATE signal_envelopes SET produced_at = ?, envelope_json = ? WHERE id = ?",
        (produced_at, json.dumps(envelope, sort_keys=True), signal_envelope_id),
    )
    service.db.commit()


def _iso_days_ago(service, days: int) -> str:
    when = (service._ovp_now() - timedelta(days=days)).replace(microsecond=0)
    return when.isoformat().replace("+00:00", "Z")


def test_claims_queue_sink_routes_s2_plus_events(service):
    _, dp, enrollment, _, _ = _produce_score(service)
    _emit_case(service, dp, enrollment, "hl-01")
    queue = service.list_claims_queue(dp["dp_id"])
    assert queue["sink_version"] == POST_BIND_SINKS_VERSION
    assert queue["consumer_contract"] == "read_only_underwriter_claims_architect"
    assert queue["manual_only"] is True
    assert queue["policy_scope"] == {
        "threshold_policy_ref": "BEAAA-5817_v0.3",
        "threshold_policy_governed_perils": ["P4", "P6"],
        "non_governed_items_posture": "telemetry_manual_review_only",
        "queue_wide_conformance_claim": False,
    }
    assert queue["count"] >= 1
    item = queue["items"][0]
    assert item["claim_id"].startswith("synthetic-claim:")
    assert item["peril_code"] == "P4"
    assert item["severity_tier"] in {"S2", "S3", "S4"}
    assert item["threshold_policy_ref"] == "BEAAA-5817_v0.3"
    assert item["threshold_policy_governed"] is True
    assert item["telemetry_manual_review_only"] is False
    assert "BEAAA-5817-governed" in item["policy_scope_note"]
    assert item["pin_block"]["log_schema_version"] == LOG_SCHEMA_VERSION
    assert item["pin_block"]["detector_bundle_version"] == DETECTOR_BUNDLE_VERSION


def test_claims_queue_scope_marks_non_beaaa5817_perils_as_telemetry_only(service):
    _, dp, enrollment, _, _ = _produce_score(service)
    _emit_case(service, dp, enrollment, "pi-01")
    queue = service.list_claims_queue(dp["dp_id"])
    item = queue["items"][0]
    assert item["peril_code"] == "P5"
    assert item["threshold_policy_ref"] is None
    assert item["threshold_policy_governed"] is False
    assert item["telemetry_manual_review_only"] is True
    assert "outside BEAAA-5817 v0.3" in item["policy_scope_note"]
    assert item["manual_only"] is True


def test_claims_queue_scope_marks_p6_as_beaaa5817_governed(service):
    _, dp, enrollment, _, _ = _produce_score(service)
    _emit_case(service, dp, enrollment, "rr-01")
    queue = service.list_claims_queue(dp["dp_id"])
    item = queue["items"][0]
    assert item["peril_code"] == "P6"
    assert item["threshold_policy_ref"] == "BEAAA-5817_v0.3"
    assert item["threshold_policy_governed"] is True
    assert item["telemetry_manual_review_only"] is False
    assert item["manual_only"] is True


def test_per_customer_rescore_feed_is_manual_only_and_p4_p5_scoped(service):
    _, dp, enrollment, _, _ = _produce_score(service)
    _emit_case(service, dp, enrollment, "hl-02")
    _emit_case(service, dp, enrollment, "pi-01")
    feed = service.get_per_customer_rescore_feed(dp["dp_id"])
    assert feed["routing"] == "manual_underwriting_review_only"
    assert feed["auto_reprice"] is False
    assert [p["peril_code"] for p in feed["perils"]] == list(OVP_IN_SCOPE_PERILS)
    by_code = {p["peril_code"]: p for p in feed["perils"]}
    assert by_code["P4"]["observed_event_count"] == 1
    assert by_code["P5"]["observed_event_count"] == 1
    assert by_code["P4"]["shadow_score_delta_points"] == 0
    assert feed["pin_block"]["scanner_version"] == portal.SCANNER_VERSION


def test_per_customer_rescore_feed_uses_30d_window_and_excludes_stale_events(service):
    _, dp, enrollment, _, _ = _produce_score(service)
    stale = _emit_case(service, dp, enrollment, "hl-01", "-stale")
    fresh = _emit_case(service, dp, enrollment, "hl-02", "-fresh")
    _retime_signal(service, stale["signal_envelope_ids"][0], _iso_days_ago(service, OVP_PRIMARY_WINDOW_DAYS + 5))
    _retime_signal(service, fresh["signal_envelope_ids"][0], _iso_days_ago(service, 2))
    feed = service.get_per_customer_rescore_feed(dp["dp_id"])
    p4 = {p["peril_code"]: p for p in feed["perils"]}["P4"]
    assert p4["observed_event_count"] == 1
    assert p4["eligible_actions"] == 10000
    assert stale["signal_envelope_ids"][0] not in p4["signal_envelope_ids"]
    assert fresh["signal_envelope_ids"][0] in p4["signal_envelope_ids"]
    assert p4["review_window"]["mode"] == "rolling_30d"
    assert p4["review_window"]["fallback_window_used"] is False


def test_observed_vs_predicted_uses_90d_fallback_when_30d_is_not_credible(service):
    _, dp, enrollment, scan, _score = _produce_score(service)
    ids = []
    for idx, case_id in enumerate(("hl-01", "hl-02", "hl-03")):
        emitted = _emit_case(service, dp, enrollment, case_id, f"-fallback-{idx}")
        ids.append(emitted["signal_envelope_ids"][0])
    for idx, signal_id in enumerate(ids):
        _retime_signal(service, signal_id, _iso_days_ago(service, OVP_PRIMARY_WINDOW_DAYS + 10 + idx))
    feed = service.get_per_customer_rescore_feed(dp["dp_id"])
    p4 = {p["peril_code"]: p for p in feed["perils"]}["P4"]
    assert p4["observed_event_count"] == 3
    assert p4["eligible_actions"] == 30000
    assert p4["review_window"]["mode"] == f"rolling_{OVP_FALLBACK_WINDOW_DAYS}d"
    assert p4["review_window"]["fallback_window_used"] is True
    assert feed["window"]["fallback_window_used"] is True
    ovp = service.get_underwriting_rollup(scan["scan_id"])["observed_vs_predicted"]
    assert ovp["window"]["mode"] == f"rolling_{OVP_FALLBACK_WINDOW_DAYS}d"
    assert ovp["window"]["fallback_window_used"] is True


def test_portfolio_calibration_feed_aggregates_across_customers(service):
    _, dp_a, enrollment_a, _, _ = _produce_score(service)
    _, dp_b, enrollment_b, _, _ = _produce_score(service)
    _emit_case(service, dp_a, enrollment_a, "hl-03", "-a")
    _emit_case(service, dp_b, enrollment_b, "pi-02", "-b")
    feed = service.get_portfolio_calibration_feed()
    assert feed["contract_output_disposition"] == "outside_are_scanner_api_v1_export_only"
    assert feed["auto_rate_update"] is False
    by_code = {p["peril_code"]: p for p in feed["perils"]}
    assert by_code["P4"]["customer_count"] == 1
    assert by_code["P5"]["customer_count"] == 1
    assert by_code["P4"]["observed_event_count"] == 1
    assert by_code["P5"]["observed_event_count"] == 1


def test_observed_vs_predicted_sparse_block_on_rollup_without_telemetry(service):
    _, _dp, _enrollment, scan, score = _produce_score(service)
    score_snapshot = json.dumps(score, sort_keys=True)
    rollup = service.get_underwriting_rollup(scan["scan_id"])
    assert "observed_vs_predicted" in rollup
    ovp = rollup["observed_vs_predicted"]
    assert ovp["block_disposition"] == "export_only_no_mutation"
    assert [p["peril_code"] for p in ovp["perils"]] == ["P4", "P5"]
    assert all(p["observed_event_count"] == 0 for p in ovp["perils"])
    assert all(p["credibility_status"] == "insufficient_data" for p in ovp["perils"])
    assert ovp["guardrails"] == {
        "upstream_rollup_fields_unchanged": True,
        "upstream_score_fields_unchanged": True,
        "contract_output_unchanged": True,
        "pricing_book_unchanged": True,
    }
    persisted = json.loads(
        service.db.execute("SELECT score_json FROM scans WHERE id = ?", (scan["scan_id"],)).fetchone()["score_json"]
    )
    assert json.dumps(persisted, sort_keys=True) == score_snapshot


def test_observed_vs_predicted_guard_rejects_score_field_mutation(service):
    _, dp, _enrollment, scan, score = _produce_score(service)
    rollup = portal._build_peril_rollup(score)
    original = service.get_per_customer_rescore_feed

    def mutating_feed(dp_id):
        assert dp_id == dp["dp_id"]
        score["are_total"] = score["are_total"] + 1
        return original(dp_id)

    service.get_per_customer_rescore_feed = mutating_feed
    with pytest.raises(portal.PortalError) as err:
        service.build_observed_vs_predicted(scan["scan_id"], rollup, score)
    assert err.value.code == "observed_vs_predicted_invariant_violated"


def test_internal_reports_render_ovp_but_dp_report_does_not(service):
    _, dp, enrollment, scan, _score = _produce_score(service)
    _emit_case(service, dp, enrollment, "pi-03")
    internal_path = service.render_report(scan["scan_id"], "internal_uw")
    appendix_path = service.render_report(scan["scan_id"], "evidence_appendix")
    dp_path = service.render_report(scan["scan_id"], "dp")
    internal = open(internal_path, encoding="utf-8").read()
    appendix = open(appendix_path, encoding="utf-8").read()
    dp_html = open(dp_path, encoding="utf-8").read()
    assert "Observed vs predicted drift (post-bind, export-only)" in internal
    assert "Observed vs predicted drift (post-bind, export-only)" in appendix
    assert "manual underwriting review only" in internal
    assert "Observed vs predicted drift" not in dp_html


def test_post_bind_sink_http_routes(service):
    _, dp, enrollment, _scan, _score = _produce_score(service)
    _emit_case(service, dp, enrollment, "pi-01")
    PortalHandler.service = service
    server = ThreadingHTTPServer(("127.0.0.1", 0), PortalHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(f"{base}/api/v1/post-bind/claims-queue?dp_id={dp['dp_id']}") as resp:
            claims = json.loads(resp.read().decode("utf-8"))
        with urllib.request.urlopen(f"{base}/api/v1/post-bind/customers/{dp['dp_id']}") as resp:
            rescore = json.loads(resp.read().decode("utf-8"))
        with urllib.request.urlopen(f"{base}/api/v1/post-bind/portfolio-calibration-feed") as resp:
            portfolio_feed = json.loads(resp.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert claims["count"] == 1
    assert rescore["dp_id"] == dp["dp_id"]
    assert portfolio_feed["perils"]
