from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any

from .invariants import ManifestIdempotencyStore, utc_now_iso
from .manifest_signature import ALLOWED_SCHEMA_VERSIONS, validate_manifest_schema
from .manifest_signature import normalize_and_digest_events, parse_manifest
from .p5_detector import detect_p5_events
from .redaction import (
    RedactionError,
    RedactedField,
    normalize_payload_redaction_plan,
    apply_redaction,
)


class NormalizerError(ValueError):
    """Raised when a manifest cannot be normalized to Schema v1 events."""


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


def normalize_manifest(
    raw_manifest: str | dict[str, Any],
    *,
    redaction_policy: dict[str, str] | None = None,
    idempotency_store: ManifestIdempotencyStore | None = None,
) -> tuple[dict[str, Any], ...]:
    manifest = parse_manifest(raw_manifest) if isinstance(raw_manifest, str) else raw_manifest
    if not isinstance(manifest, dict):
        raise NormalizerError("manifest payload must be a JSON object")

    try:
        validate_manifest_schema(manifest)
    except ValueError as exc:
        raise NormalizerError(str(exc)) from exc

    manifest_version = manifest.get("schema_version")
    if manifest_version not in ALLOWED_SCHEMA_VERSIONS:
        raise NormalizerError(f"unsupported schema version: {manifest_version!r}")

    global_policy = normalize_payload_redaction_plan(redaction_policy)

    event_hashes, manifest_digest = normalize_and_digest_events(manifest)
    if not event_hashes:
        raise NormalizerError("manifest contains no events")
    if idempotency_store is not None and idempotency_store.is_duplicate(manifest["manifest_id"], manifest_digest):
        return ()

    normalized_events: list[dict[str, Any]] = []
    for idx, event in enumerate(manifest["events"]):
        normalized_events.append(
            normalize_event(
                event=event,
                idx=idx,
                manifest_id=manifest["manifest_id"],
                schema_version=manifest["schema_version"],
                manifest_digest=manifest_digest,
                event_digest=event_hashes[idx],
                event_policy=global_policy,
            )
        )
    normalized_events.extend(
        detect_p5_events(
            normalized_events,
            manifest_id=manifest["manifest_id"],
            manifest_digest=manifest_digest,
            schema_version=manifest["schema_version"],
        )
    )
    if idempotency_store is not None:
        idempotency_store.record(manifest["manifest_id"], manifest_digest)
    return tuple(normalized_events)


def normalize_event(
    event: dict[str, Any],
    *,
    idx: int,
    manifest_id: str,
    schema_version: str,
    manifest_digest: str,
    event_digest: str,
    event_policy: dict[str, str],
) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise NormalizerError(f"manifest.events[{idx}] must be an object")
    for field, expected in {"event_type": str, "payload": dict, "event_id": str, "logged_at": str}.items():
        value = event.get(field)
        if not isinstance(value, expected):
            raise NormalizerError(f"manifest.events[{idx}].{field} must be {expected.__name__}")
    if event["event_type"] not in ALLOWED_EVENT_TYPES:
        raise NormalizerError(f"unsupported event_type: {event['event_type']!r}")
    event_policy = dict(event_policy)
    event_payload = dict(event["payload"])

    per_event_policy = normalize_payload_redaction_plan(event.get("redaction", {}))
    for key, redaction in per_event_policy.items():
        event_policy[key] = redaction

    redacted_payload, redacted_fields = apply_redaction(event_payload, event_policy)
    redactions = tuple(dataclasses.asdict(field) for field in redacted_fields)

    logged_at = _coerce_iso8601(event["logged_at"])
    return {
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "logged_at": logged_at,
        "schema_version": schema_version,
        "payload": redacted_payload,
        "provenance": {
            "class": "backbond_normalizer",
            "component": "post_bind_ingest",
            "normalizer_version": "0.1.0",
        },
        "event_ingest": {
            "manifest_id": manifest_id,
            "manifest_digest": manifest_digest,
            "event_digest": event_digest,
            "normalized_at": utc_now_iso(),
        },
        "redactions": redactions,
    }


def _coerce_iso8601(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat().replace("+00:00", "Z")
    except ValueError as exc:
        raise NormalizerError(f"logged_at must be ISO-8601: {value!r}") from exc
