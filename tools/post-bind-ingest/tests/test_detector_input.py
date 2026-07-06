from __future__ import annotations

import pytest

from post_bind_ingest.detector_input import normalize_tool_name
from post_bind_ingest.passive_detectors import detect_passive_peril_events


def _tool_event(payload: dict, event_id: str = "evt-1") -> dict:
    return {
        "event_id": event_id,
        "event_type": "tool_call",
        "logged_at": "2026-07-05T00:00:00Z",
        "schema_version": "log-event-schema-v1.0",
        "payload": payload,
    }


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"tool": "Transfer"}, "transfer"),
        ({"tool_name": "transfer"}, "transfer"),
        ({"toolName": "transfer"}, "transfer"),
        ({"function_name": "transfer"}, "transfer"),
        ({"name": "transfer"}, "transfer"),
        ({"action": "transfer"}, "transfer"),
        ({"action_name": "transfer"}, "transfer"),
        ({"tool": {"name": "transfer"}}, "transfer"),
        ({"function": {"name": "transfer"}}, "transfer"),
        ({"tool_call": {"function": {"name": "transfer"}}}, "transfer"),
        ({"action": {"name": "transfer"}}, "transfer"),
        ({"unrelated": "x"}, ""),
        ({}, ""),
        ("not-a-mapping", ""),
    ],
)
def test_normalize_tool_name_handles_shape_variants(payload, expected) -> None:
    assert normalize_tool_name(payload) == expected


def test_normalize_tool_name_prefers_explicit_tool_over_generic_name() -> None:
    assert normalize_tool_name({"tool": "transfer", "name": "handler"}) == "transfer"


def test_normalize_tool_name_is_deterministic() -> None:
    payload = {"tool_call": {"function": {"name": "Bulk_Refund"}}}
    assert normalize_tool_name(payload) == normalize_tool_name(payload) == "bulk_refund"


def test_passive_detector_classifies_tool_name_variant() -> None:
    # Vendor emits the tool identity as tool_name (not `tool`) — the historical
    # blind spot. The money-movement peril must still fire.
    event = _tool_event(
        {
            "tool_name": "transfer",
            "detector_features": {"peril_markers": ["unauthorized_money_movement"]},
        }
    )
    candidates = detect_passive_peril_events(
        [event], manifest_id="m1", manifest_digest="d1", schema_version="log-event-schema-v1.0"
    )
    assert len(candidates) == 1
    assert candidates[0]["payload"]["peril"] == "P1"


def test_passive_detector_classifies_openai_function_shape() -> None:
    event = _tool_event(
        {
            "tool_call": {"function": {"name": "pii_export"}},
        }
    )
    candidates = detect_passive_peril_events(
        [event], manifest_id="m1", manifest_digest="d1", schema_version="log-event-schema-v1.0"
    )
    assert len(candidates) == 1
    assert candidates[0]["payload"]["peril"] == "P3"


def test_passive_detector_unknown_tool_shape_yields_no_candidate() -> None:
    event = _tool_event({"unrelated_field": "value"})
    candidates = detect_passive_peril_events(
        [event], manifest_id="m1", manifest_digest="d1", schema_version="log-event-schema-v1.0"
    )
    assert candidates == []
