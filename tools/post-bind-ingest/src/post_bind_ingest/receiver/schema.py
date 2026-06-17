from __future__ import annotations

from collections.abc import Mapping
import hashlib
from datetime import datetime
from typing import Any

from ..invariants import canonical_dumps

ALLOWED_SCHEMA_VERSIONS = {"log-event-schema-v1.0"}

ALLOWED_EVENT_TYPES = frozenset(
    {
        "agent_invocation",
        "tool_call",
        "tool_source_reference",
        "llm_completion",
        "escalation",
        "error",
        "security_event",
        "pii_emission",
        "claim_candidate_event",
    }
)

REQUIRED_BATCH_FIELDS = {
    "issued_at": str,
    "schema_version": str,
    "batch_id": str,
    "batch_sha256": str,
    "event_count": int,
    "events": list,
    "customer_id": str,
    "agent": str,
}

REQUIRED_BATCH_KEYS = frozenset(("customer_id", "agent", "schema_version", "batch_id", "batch_sha256", "event_count"))
ALTERNATE_ISSUED_AT_KEYS = ("issued_at", "t_emitted")

REQUIRED_EVENT_FIELDS = {
    "event_id": str,
    "event_type": str,
    "logged_at": str,
    "payload": dict,
    "redaction_applied": list,
}


class ReceiverError(ValueError):
    """Raised when a receiver envelope or event fails structural validation."""


class ReceiverSchemaError(ReceiverError):
    """Raised when receiver payload fails schema expectations."""


class RedactionConformanceError(ReceiverError):
    """Raised when pre-redacted claims do not satisfy receiver checks."""


def validate_iso8601(value: str, field: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReceiverSchemaError(f"{field} must be ISO-8601 datetime") from exc


def validate_event_schema(event: Mapping[str, Any], idx: int | None = None) -> None:
    field_prefix = f"event[{idx}]." if idx is not None else "event."
    for field, expected_type in REQUIRED_EVENT_FIELDS.items():
        value = event.get(field)
        if value is None:
            raise ReceiverSchemaError(f"{field_prefix}{field} is required")
        if not isinstance(value, expected_type):
            raise ReceiverSchemaError(f"{field_prefix}{field} must be {expected_type.__name__}")
    event_type = event["event_type"]
    if not isinstance(event_type, str) or event_type not in ALLOWED_EVENT_TYPES:
        raise ReceiverSchemaError(f"{field_prefix}event_type must be one of {sorted(ALLOWED_EVENT_TYPES)}")
    validate_iso8601(event["logged_at"], f"{field_prefix}logged_at")


def validate_batch_schema(payload: Mapping[str, Any]) -> None:
    for field in REQUIRED_BATCH_KEYS:
        value = payload.get(field)
        if value is None:
            raise ReceiverSchemaError(f"receiver payload missing required field: {field}")
        if field in REQUIRED_BATCH_FIELDS and not isinstance(value, REQUIRED_BATCH_FIELDS[field]):
            raise ReceiverSchemaError(f"receiver payload field {field} must be {REQUIRED_BATCH_FIELDS[field].__name__}")

    issued_at = _get_issued_at_field(payload)
    validate_iso8601(issued_at, "issued_at")

    schema_version = payload.get("schema_version")
    if schema_version not in ALLOWED_SCHEMA_VERSIONS:
        raise ReceiverSchemaError(f"unsupported schema_version: {schema_version!r}")

    if payload["event_count"] <= 0:
        raise ReceiverSchemaError("event_count must be > 0")
    events = payload["events"]
    if not isinstance(events, list) or not events:
        raise ReceiverSchemaError("events must be a non-empty list")
    if len(events) != payload["event_count"]:
        raise ReceiverSchemaError("event_count must equal len(events)")

    for idx, event in enumerate(events):
        if not isinstance(event, Mapping):
            raise ReceiverSchemaError(f"events[{idx}] must be an object")
        validate_event_schema(event, idx=idx)

    batch_sha256 = payload["batch_sha256"]
    if not isinstance(batch_sha256, str) or not batch_sha256:
        raise ReceiverSchemaError("batch_sha256 must be a non-empty string")


def _get_issued_at_field(payload: Mapping[str, Any]) -> str:
    for field in ALTERNATE_ISSUED_AT_KEYS:
        value = payload.get(field)
        if value is not None:
            if not isinstance(value, str):
                break
            if value:
                return value
    raise ReceiverSchemaError("receiver payload missing required issued-at field")


def batch_event_digests(events: list[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(hashlib.sha256(canonical_dumps(event)).hexdigest() for event in events)


def compute_batch_sha256(events: list[Mapping[str, Any]]) -> str:
    return hashlib.sha256(canonical_dumps(events)).hexdigest()
