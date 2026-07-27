from __future__ import annotations

from post_bind_ingest.continuity_gate import evaluate_attach_renew_continuity
from post_bind_ingest.evidence_chain_trace import (
    DECISION_TABLE_REVISION,
    DECISION_TABLE_SHA256,
    build_evidence_chain_trace,
)
from post_bind_ingest.passive_detectors import detect_passive_peril_events


def _event(seq: int, *, marker: str | None = None) -> dict:
    payload = {
        "tenant_id": "vendor-support",
        "agent_id": "support-agent",
        "deployment_id": "release-42",
        "collector_id": "collector-1",
        "batch_seq": seq,
        "action_class": "crm_contact_export",
        "tool_name": "crm.read_pii",
    }
    if marker:
        payload["detector_features"] = {"peril_markers": [marker]}
    return {
        "event_id": f"evt-{seq}",
        "event_type": "tool_call" if marker else "agent_invocation",
        "logged_at": f"2026-07-27T12:0{seq}:00Z",
        "schema_version": "log-event-schema-v1.0",
        "payload": payload,
        "redactions": ["customer_name", "email", "message_body"],
    }


def test_bounded_crm_trace_and_no_logs_fail_closed() -> None:
    production_event = _event(2, marker="data_exfiltration")
    events = [_event(1), production_event]
    continuity = evaluate_attach_renew_continuity(
        events,
        action_class="renew",
        tenant_id="vendor-support",
        agent_id="support-agent",
        deployment_id="release-42",
        collector_id="collector-1",
        evaluated_at="2026-07-27T12:05:00Z",
    )
    candidate = detect_passive_peril_events(
        [production_event],
        manifest_id="manifest-42",
        manifest_digest="sha256:8bc4",
        schema_version="log-event-schema-v1.0",
    )[0]

    trace = build_evidence_chain_trace(
        tested_failure_mode={
            "test_id": "active-scanner:data-exfiltration:crm-export",
            "verdict": "pass",
            "release_id": "release-42",
        },
        production_event=production_event,
        control_reliance_event={
            "event_id": "authority-7",
            "payload": {
                "control_id": "crm-export-approval",
                "relied_on": True,
                "authority_result": "approval_missing_but_tool_executed",
            },
        },
        claim_candidate=candidate,
        continuity=continuity,
        decided_at="2026-07-27T12:05:01Z",
    ).to_dict()

    assert trace["decision_table_revision"] == DECISION_TABLE_REVISION
    assert trace["decision_table_sha256"] == DECISION_TABLE_SHA256
    assert trace["production_exposure"]["action_class"] == "crm_contact_export"
    assert trace["redacted_incident_evidence"]["redactions"] == [
        "customer_name",
        "email",
        "message_body",
    ]
    assert "customer_name" not in str(trace["redacted_incident_evidence"]["evidence_refs"])
    assert trace["consequence"] == {
        "warranty_eligibility": "suspended_for_affected_action_class",
        "warranty_cap_posture": "suspended_affected_action_class",
        "rescan_status": "required",
        "review_lane": "human_underwriting",
        "reason_code": "PM_PERIL_S3",
        "claim_candidate_only": True,
        "numeric_cap": None,
    }
    assert trace["audit_record"]["predicate_inputs"]["severity"] == "S3"
    assert trace["audit_record"]["resulting_state"] == trace["consequence"]

    no_logs = evaluate_attach_renew_continuity(
        [],
        action_class="renew",
        tenant_id="vendor-support",
        agent_id="support-agent",
        deployment_id="release-42",
        collector_id="collector-1",
        evaluated_at="2026-07-27T12:05:00Z",
    )
    fail_closed = build_evidence_chain_trace(
        tested_failure_mode=trace["tested_failure_mode"],
        production_event=production_event,
        control_reliance_event={
            "event_id": "authority-7",
            "payload": {
                "control_id": "crm-export-approval",
                "relied_on": True,
                "authority_result": "approval_missing_but_tool_executed",
            },
        },
        claim_candidate=candidate,
        continuity=no_logs,
        decided_at="2026-07-27T12:06:00Z",
    ).to_dict()
    assert fail_closed["consequence"]["reason_code"] == "PM_NO_LOGS"
    assert (
        fail_closed["consequence"]["warranty_eligibility"]
        == "no_warranty_remedy_eligibility_for_affected_window"
    )
    assert fail_closed["consequence"]["warranty_cap_posture"] == "suspended"
