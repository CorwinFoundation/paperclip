from __future__ import annotations

from post_bind_ingest.continuity_gate import (
    classify_continuity_status,
    compute_gap_state,
    evaluate_attach_renew_continuity,
)


def _event(seq: int, logged_at: str, *, action_class: str = "heartbeat") -> dict[str, object]:
    return {
        "event_id": f"evt-{seq}",
        "event_type": "agent_invocation",
        "logged_at": logged_at,
        "schema_version": "log-event-schema-v1.0",
        "payload": {
            "tenant_id": "tenant-1",
            "agent_id": "agent-1",
            "deployment_id": "prod",
            "collector_id": "collector-1",
            "batch_seq": seq,
            "action_class": action_class,
        },
    }


def test_gap_state_detects_missing_sequence_and_blocks_attach() -> None:
    events = [
        _event(1, "2026-07-03T10:00:00Z"),
        _event(3, "2026-07-03T10:05:00Z"),
    ]

    decision = evaluate_attach_renew_continuity(
        events,
        action_class="attach",
        tenant_id="tenant-1",
        agent_id="agent-1",
        deployment_id="prod",
        collector_id="collector-1",
        evaluated_at="2026-07-03T10:10:00Z",
    )

    assert decision.permitted is False
    assert decision.refusal_reason == "telemetry_discontinuity_active: batch_sequence_gap"
    assert decision.gap_state.missing_batch_seq == (2,)
    assert decision.audit_events[0]["payload"]["event"] == "attach_renew_refused"
    assert decision.audit_events[0]["payload"]["refusal_reason"] == decision.refusal_reason
    assert decision.audit_events[0]["payload"]["scanner_contract_ref"] == "are-scanner-api-v1@v1.3.0"


def test_gap_recovery_restores_renew_and_writes_recovery_event() -> None:
    prior = compute_gap_state(
        [_event(1, "2026-07-03T10:00:00Z"), _event(3, "2026-07-03T10:05:00Z")],
        tenant_id="tenant-1",
        agent_id="agent-1",
        deployment_id="prod",
        collector_id="collector-1",
        evaluated_at="2026-07-03T10:10:00Z",
    )
    assert prior.active is True

    decision = evaluate_attach_renew_continuity(
        [
            _event(1, "2026-07-03T10:00:00Z"),
            _event(2, "2026-07-03T10:03:00Z"),
            _event(3, "2026-07-03T10:05:00Z"),
        ],
        action_class="renew",
        tenant_id="tenant-1",
        agent_id="agent-1",
        deployment_id="prod",
        collector_id="collector-1",
        evaluated_at="2026-07-03T10:10:00Z",
        prior_gap_state=prior,
    )

    assert decision.permitted is True
    assert decision.refusal_reason is None
    assert decision.gap_state.active is False
    assert decision.audit_events[0]["payload"]["event"] == "telemetry_gap_recovered"


def test_stale_heartbeat_blocks_renew_until_fresh_heartbeat_arrives() -> None:
    stale = [_event(1, "2026-07-03T01:00:00Z")]
    stale_decision = evaluate_attach_renew_continuity(
        stale,
        action_class="renew",
        tenant_id="tenant-1",
        agent_id="agent-1",
        deployment_id="prod",
        collector_id="collector-1",
        evaluated_at="2026-07-03T10:00:00Z",
        heartbeat_max_age_seconds=3600,
    )

    assert stale_decision.permitted is False
    assert stale_decision.gap_state.reasons == ("heartbeat_stale",)

    fresh_decision = evaluate_attach_renew_continuity(
        stale + [_event(2, "2026-07-03T09:30:00Z")],
        action_class="renew",
        tenant_id="tenant-1",
        agent_id="agent-1",
        deployment_id="prod",
        collector_id="collector-1",
        evaluated_at="2026-07-03T10:00:00Z",
        heartbeat_max_age_seconds=3600,
        prior_gap_state=stale_decision.gap_state,
    )

    assert fresh_decision.permitted is True
    assert fresh_decision.gap_state.active is False
    assert fresh_decision.audit_events[0]["payload"]["event"] == "telemetry_gap_recovered"


def test_non_attach_renew_action_does_not_refuse_but_reports_state() -> None:
    decision = evaluate_attach_renew_continuity(
        [_event(1, "2026-07-03T10:00:00Z"), _event(3, "2026-07-03T10:05:00Z")],
        action_class="claim_review",
        tenant_id="tenant-1",
        agent_id="agent-1",
        deployment_id="prod",
        collector_id="collector-1",
        evaluated_at="2026-07-03T10:10:00Z",
    )

    assert decision.permitted is True
    assert decision.refusal_reason is None
    assert decision.gap_state.active is True
    assert decision.audit_events == ()


def _status(events, *, evaluated_at, heartbeat_max_age_seconds=3600) -> str:
    state = compute_gap_state(
        events,
        tenant_id="tenant-1",
        agent_id="agent-1",
        deployment_id="prod",
        collector_id="collector-1",
        evaluated_at=evaluated_at,
        heartbeat_max_age_seconds=heartbeat_max_age_seconds,
    )
    return classify_continuity_status(state)


def test_continuity_status_healthy() -> None:
    events = [_event(1, "2026-07-03T09:59:00Z"), _event(2, "2026-07-03T10:00:00Z")]
    assert _status(events, evaluated_at="2026-07-03T10:00:00Z") == "healthy"


def test_continuity_status_grace_when_heartbeat_aging_but_not_stale() -> None:
    # age 2400s > 50% of 3600s max, still < max -> eligible but flagged.
    events = [_event(1, "2026-07-03T09:19:00Z"), _event(2, "2026-07-03T09:20:00Z")]
    assert _status(events, evaluated_at="2026-07-03T10:00:00Z") == "grace"


def test_continuity_status_gap_on_sequence_gap_with_fresh_heartbeat() -> None:
    events = [_event(1, "2026-07-03T09:59:00Z"), _event(3, "2026-07-03T10:00:00Z")]
    assert _status(events, evaluated_at="2026-07-03T10:00:00Z") == "gap"


def test_continuity_status_stale_ineligible_on_stale_heartbeat() -> None:
    events = [_event(1, "2026-07-03T01:00:00Z")]
    assert _status(events, evaluated_at="2026-07-03T10:00:00Z") == "stale_ineligible"


def test_continuity_status_stale_ineligible_on_no_telemetry() -> None:
    assert _status([], evaluated_at="2026-07-03T10:00:00Z") == "stale_ineligible"


def test_decision_and_gap_state_expose_continuity_status() -> None:
    decision = evaluate_attach_renew_continuity(
        [_event(1, "2026-07-03T09:59:00Z"), _event(3, "2026-07-03T10:00:00Z")],
        action_class="attach",
        tenant_id="tenant-1",
        agent_id="agent-1",
        deployment_id="prod",
        collector_id="collector-1",
        evaluated_at="2026-07-03T10:00:00Z",
    )
    assert decision.continuity_status == "gap"
    assert decision.to_dict()["continuity_status"] == "gap"
    assert decision.gap_state.to_dict()["continuity_status"] == "gap"
