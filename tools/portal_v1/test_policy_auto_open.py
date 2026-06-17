"""BEAAA-6165 policy-system claim auto-open adapter tests."""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from portal import PortalHandler, build_service, now_iso
from test_portal import enroll_ready_dp


@pytest.fixture
def service(tmp_path):
    svc = build_service(tmp_path)
    yield svc
    svc.close()


def _policy_dp(service):
    _signing_key, dp, _enrollment = enroll_ready_dp(service)
    return dp, f"synthetic-policy:{dp['dp_id']}"


def _claims_queue_item(dp, policy_id, source_id="signal-1"):
    return {
        "claims_queue_item_id": source_id,
        "signal_envelope_id": source_id,
        "policy_id": policy_id,
        "agent_id": dp["dp_id"],
        "event_t": "2026-06-17T10:00:00Z",
        "severity_tier": "S3",
        "peril_code": "P4",
        "threshold_policy_ref": "BEAAA-5817_v0.3",
        "threshold_policy_governed": True,
        "telemetry_manual_review_only": False,
        "evidence_refs": ["portal-evidence://dp/signal-envelopes/signal-1"],
    }


def _claims_trigger_envelope(dp, source_id="claims-env-1"):
    return {
        "claims_trigger_envelope_id": source_id,
        "dp_id": dp["dp_id"],
        "produced_at": "2026-06-17T10:00:00Z",
        "trigger_window_end": "2026-06-17T10:00:00Z",
        "signal_envelope_ids": ["signal-2"],
        "event_type_counts": {"hallucination": 5},
        "claim_eligibility_class": "interim_payout_eligible",
        "redacted_evidence_refs": ["portal-evidence://dp/signal-envelopes/signal-2"],
        "portal_signature": "test-signature",
    }


def _customer_payload(dp, signal_id="loss-1", summary="Tool-grounded false output caused a customer demand."):
    return {
        "customer_loss_signal_id": signal_id,
        "reported_at": now_iso(),
        "reported_event_at": "2026-06-17T10:00:00Z",
        "affected_agent_id": dp["dp_id"],
        "reporter_email": "owner@example.com",
        "loss_type": "financial_loss",
        "summary": summary,
        "evidence_refs": ["customer://ticket-123"],
        "customer_reference_ids": ["ticket-123"],
    }


def _claim_rows(service):
    return service.db.execute("SELECT * FROM claim_records ORDER BY opened_at ASC").fetchall()


AUTO_OPEN_EVENT_KEYS = {
    "source_type",
    "source_id",
    "policy_id",
    "insured_agent_id",
    "event_occurred_at",
    "severity_tier",
    "peril_code",
    "threshold_policy_ref",
    "threshold_policy_governed",
    "customer_notice_present",
    "evidence_refs",
    "wording_section_candidates",
    "fingerprint",
}


def test_normalizers_feed_exact_auto_open_event_field_set(service):
    dp, policy_id = _policy_dp(service)

    queue_event = service.normalize_claims_queue_item(_claims_queue_item(dp, policy_id))
    envelope_event = service.normalize_claims_trigger_envelope(_claims_trigger_envelope(dp))

    assert set(queue_event) == AUTO_OPEN_EVENT_KEYS
    assert set(envelope_event) == AUTO_OPEN_EVENT_KEYS


def test_customer_loss_signal_feeds_exact_auto_open_event_field_set(service):
    dp, policy_id = _policy_dp(service)
    captured = {}
    original = service.open_claim_record

    def spy(auto_open_event):
        captured.update(auto_open_event)
        return original(auto_open_event)

    service.open_claim_record = spy
    result = service.handle_customer_loss_signal(policy_id, _customer_payload(dp, "loss-field-set"))

    assert result["status"] == "opened"
    assert set(captured) == AUTO_OPEN_EVENT_KEYS


def test_scenario_1_telemetry_threshold_opens_record(service):
    dp, policy_id = _policy_dp(service)
    result = service.open_claim_from_claims_queue_item(_claims_queue_item(dp, policy_id))
    record = service.get_claim_record(result["claim_record_id"])

    assert result["status"] == "opened"
    assert len(_claim_rows(service)) == 1
    assert record["status"] == "intake_open"
    assert record["coverage_state"] == "unreviewed"
    assert record["reserve_state"] == "not_set"
    assert record["payment_state"] == "not_approved"
    assert record["trigger_source"] == "telemetry_threshold"
    assert record["intake_class"] == "claim_notice"
    assert record["threshold_policy_governed"] is True
    assert record["threshold_policy_ref"] == "BEAAA-5817_v0.3"
    assert record["wording_section_candidates"]


def test_claims_trigger_envelope_normalizes_to_same_write_path(service):
    dp, _policy_id = _policy_dp(service)
    result = service.open_claim_from_claims_trigger_envelope(_claims_trigger_envelope(dp))
    record = service.get_claim_record(result["claim_record_id"])

    assert result["status"] == "opened"
    assert record["trigger_source"] == "telemetry_threshold"
    assert record["severity_tier"] == "S4"
    assert record["coverage_state"] == "unreviewed"
    assert record["reserve_state"] == "not_set"
    assert record["payment_state"] == "not_approved"


def test_scenario_2_customer_loss_signal_endpoint_opens_record(service):
    dp, policy_id = _policy_dp(service)
    PortalHandler.service = service
    server = ThreadingHTTPServer(("127.0.0.1", 0), PortalHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/v1/policies/{policy_id}/claim-intake/customer-loss-signal",
            data=json.dumps(_customer_payload(dp)).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=2)

    record = service.get_claim_record(payload["claim_record_id"])
    assert payload["status"] == "opened"
    assert len(_claim_rows(service)) == 1
    assert record["trigger_source"] == "customer_loss_signal"
    assert record["intake_class"] == "claim_notice"
    assert record["coverage_state"] == "unreviewed"
    assert record["reserve_state"] == "not_set"
    assert record["payment_state"] == "not_approved"


def test_customer_loss_signal_informational_only_opens_incident_watch(service):
    dp, policy_id = _policy_dp(service)
    result = service.handle_customer_loss_signal(
        policy_id,
        _customer_payload(
            dp,
            signal_id="loss-info",
            summary="Informational only. No loss, no demand, no request for policy response.",
        ),
    )
    record = service.get_claim_record(result["claim_record_id"])
    assert result["status"] == "opened"
    assert record["intake_class"] == "incident_watch"


def test_scenario_3_duplicate_within_24h_suppresses_telemetry_and_customer(service):
    dp, policy_id = _policy_dp(service)

    first_telemetry = service.open_claim_from_claims_queue_item(_claims_queue_item(dp, policy_id, "signal-dupe"))
    second_telemetry = service.open_claim_from_claims_queue_item(_claims_queue_item(dp, policy_id, "signal-dupe"))
    assert second_telemetry["status"] == "duplicate_suppressed"
    assert second_telemetry["claim_record_id"] == first_telemetry["claim_record_id"]

    first_customer = service.handle_customer_loss_signal(policy_id, _customer_payload(dp, "loss-dupe"))
    second_customer = service.handle_customer_loss_signal(policy_id, _customer_payload(dp, "loss-dupe"))
    assert second_customer["status"] == "duplicate_suppressed"
    assert second_customer["claim_record_id"] == first_customer["claim_record_id"]

    assert len(_claim_rows(service)) == 2
    assert len(service.list_claim_record_activity(first_telemetry["claim_record_id"])) == 1
    assert len(service.list_claim_record_activity(first_customer["claim_record_id"])) == 1


def test_customer_loss_rejection_writes_intake_audit_event(service):
    _dp, policy_id = _policy_dp(service)
    result = service.handle_customer_loss_signal(policy_id, {"reporter_email": "owner@example.com"})
    audit_rows = service.db.execute("SELECT * FROM intake_audit_events").fetchall()

    assert result["status"] == "rejected"
    assert "missing_required_fields" in result["rejection_reason"]
    assert len(audit_rows) == 1
    assert audit_rows[0]["policy_id"] == policy_id


def test_claim_record_guards_and_source_hygiene(service):
    dp, policy_id = _policy_dp(service)
    result = service.handle_customer_loss_signal(policy_id, _customer_payload(dp, "loss-hygiene"))
    record = service.get_claim_record(result["claim_record_id"])

    assert record["coverage_state"] == "unreviewed"
    assert record["reserve_state"] == "not_set"
    assert record["payment_state"] == "not_approved"

    row_text = json.dumps(record, sort_keys=True)
    assert "reporter_email" not in row_text
    assert "reporter_role" not in row_text
    assert "Tool-grounded false output" in row_text


def test_adapter_source_does_not_hard_code_wording_section_numbers():
    source = Path(__file__).with_name("policy_auto_open_v0_1.py").read_text(encoding="utf-8").lower()
    assert "section 1" not in source
    assert "section 2" not in source
    assert "section 3" not in source
    assert "section 4" not in source
    assert "section 5" not in source
    assert "section 6" not in source
    assert "section 7" not in source
    assert "section 8" not in source
    assert "section 9" not in source
