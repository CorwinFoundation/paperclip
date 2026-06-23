from __future__ import annotations

import json

import pytest

from post_bind_ingest.egress import (
    EgressConfig,
    EgressGateError,
    build_monitor_artifact,
    transmit_monitor_artifact,
    validate_monitor_artifact,
)


def _normalized_events() -> tuple[dict, ...]:
    return (
        {
            "event_id": "evt-1",
            "event_type": "tool_call",
            "logged_at": "2026-06-23T17:00:00Z",
            "schema_version": "log-event-schema-v1.0",
            "payload": {
                "tool_name": "payments",
                "recipient_email": "[REDACTED:email]",
                "api_key": "[REDACTED:secret_family]",
            },
            "redactions": (
                {"path": "recipient_email", "redaction_class": "validated"},
                {"path": "api_key", "redaction_class": "validated"},
            ),
        },
    )


def test_build_monitor_artifact_emits_only_contract_allowlist_shapes() -> None:
    artifact = build_monitor_artifact(_normalized_events(), manifest_ref="manifest://batch-1")

    assert artifact["schema_version"] == "monitor-data-contract-v0.1"
    assert artifact["manifest_refs"] == ["manifest://batch-1"]
    assert artifact["aggregates"] == {
        "event_count": 1,
        "event_type_counts": {"tool_call": 1},
        "redaction_count": 2,
    }
    assert {excerpt["path"] for excerpt in artifact["excerpts"]} == {"recipient_email", "api_key"}
    validate_monitor_artifact(artifact)


def test_default_local_only_mode_prepares_but_never_transmits() -> None:
    artifact = build_monitor_artifact(_normalized_events(), manifest_ref="manifest://batch-1")

    def fail_if_called(endpoint: str, payload: bytes) -> None:
        raise AssertionError(f"unexpected transmit to {endpoint}: {payload!r}")

    result = transmit_monitor_artifact(
        artifact,
        config=EgressConfig(),
        transport=fail_if_called,
    )

    assert result.transmitted is False
    assert result.endpoint is None
    assert result.bytes_prepared > 0


def test_explicit_transmit_requires_allowlisted_https_endpoint() -> None:
    artifact = build_monitor_artifact(_normalized_events(), manifest_ref="manifest://batch-1")

    with pytest.raises(EgressGateError, match="https"):
        transmit_monitor_artifact(
            artifact,
            config=EgressConfig(
                transmit_enabled=True,
                endpoint="http://ingest.backbond.ai/monitor",
                allowed_hosts=("ingest.backbond.ai",),
            ),
            transport=lambda endpoint, payload: None,
        )

    with pytest.raises(EgressGateError, match="allowlisted"):
        transmit_monitor_artifact(
            artifact,
            config=EgressConfig(
                transmit_enabled=True,
                endpoint="https://evil.example/monitor",
                allowed_hosts=("ingest.backbond.ai",),
            ),
            transport=lambda endpoint, payload: None,
        )


def test_explicit_transmit_uses_single_gate_payload() -> None:
    artifact = build_monitor_artifact(_normalized_events(), manifest_ref="manifest://batch-1")
    sent: list[tuple[str, bytes]] = []

    result = transmit_monitor_artifact(
        artifact,
        config=EgressConfig(
            transmit_enabled=True,
            endpoint="https://ingest.backbond.ai/monitor",
            allowed_hosts=("ingest.backbond.ai",),
        ),
        transport=lambda endpoint, payload: sent.append((endpoint, payload)),
    )

    assert result.transmitted is True
    assert sent[0][0] == "https://ingest.backbond.ai/monitor"
    wire = json.loads(sent[0][1].decode("utf-8"))
    assert wire["aggregates"]["event_count"] == 1
    assert "payload" not in wire


def test_nothing_raw_escapes_rejects_pii_secret_and_transcript_in_outbound_fields() -> None:
    artifact = build_monitor_artifact(_normalized_events(), manifest_ref="manifest://batch-1")
    artifact["excerpts"].append(
        {
            "event_id": "evt-2",
            "event_type": "llm_completion",
            "path": "transcript",
            "text": "User transcript: jane.customer@example.com paid with sk-ab12cdef3456",
            "length": 66,
            "redaction_conformance": "validated",
        }
    )

    with pytest.raises(EgressGateError, match="redacted excerpt|raw sensitive value"):
        transmit_monitor_artifact(
            artifact,
            config=EgressConfig(
                transmit_enabled=True,
                endpoint="https://ingest.backbond.ai/monitor",
                allowed_hosts=("ingest.backbond.ai",),
            ),
            transport=lambda endpoint, payload: None,
        )


def test_unredacted_event_payload_is_not_selected_for_excerpts() -> None:
    events = (
        {
            "event_id": "evt-1",
            "event_type": "llm_completion",
            "payload": {"transcript": "raw transcript with jane.customer@example.com and sk-ab12cdef3456"},
            "redactions": (),
        },
    )

    artifact = build_monitor_artifact(events, manifest_ref="manifest://batch-1")

    assert artifact["excerpts"] == []
    assert "transcript" not in json.dumps(artifact)
