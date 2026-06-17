from __future__ import annotations

import dataclasses
from datetime import datetime
import hashlib
import re
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
from .receiver import parse_receiver_payload, ReceiverReplayError, RedactionConformanceError


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


def normalize_receiver_events(
    receiver_payload: str | dict[str, Any],
    *,
    jwt_public_key: Any = None,
    idempotency_store: ManifestIdempotencyStore | None = None,
    clock_skew_seconds: int = 300,
) -> tuple[dict[str, Any], ...]:
    envelope = parse_receiver_payload(
        receiver_payload,
        jwt_public_key=jwt_public_key,
        clock_skew_seconds=clock_skew_seconds,
    )
    if idempotency_store is not None and idempotency_store.is_duplicate(
        envelope.batch_id,
        envelope.batch_sha256.removeprefix("sha256:"),
    ):
        raise ReceiverReplayError(f"replay batch_id rejected: {envelope.batch_id}")

    normalized_events: list[dict[str, Any]] = []
    for idx, event in enumerate(envelope.events):
        normalized_events.append(
            normalize_receiver_event(
                event=event,
                idx=idx,
                batch_id=envelope.batch_id,
                schema_version=envelope.schema_version,
                batch_sha256=envelope.batch_sha256.removeprefix("sha256:"),
            )
        )
    normalized_events.extend(
        detect_p5_events(
            normalized_events,
            manifest_id=envelope.batch_id,
            manifest_digest=envelope.batch_sha256.removeprefix("sha256:"),
            schema_version=envelope.schema_version,
        )
    )
    if idempotency_store is not None:
        idempotency_store.record(envelope.batch_id, envelope.batch_sha256.removeprefix("sha256:"))
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


def normalize_receiver_event(
    event: dict[str, Any],
    *,
    idx: int,
    batch_id: str,
    schema_version: str,
    batch_sha256: str,
) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise NormalizerError(f"receiver.events[{idx}] must be an object")
    for field in ("event_type", "payload", "event_id", "logged_at"):
        value = event.get(field)
        if field == "payload":
            if not isinstance(value, dict):
                raise NormalizerError(f"receiver.events[{idx}].{field} must be dict")
            continue
        if not isinstance(value, str):
            raise NormalizerError(f"receiver.events[{idx}].{field} must be a string")
    if event["event_type"] not in ALLOWED_EVENT_TYPES:
        raise NormalizerError(f"unsupported event_type: {event['event_type']!r}")

    redaction_applied = event.get("redaction_applied")
    if not isinstance(redaction_applied, list):
        raise NormalizerError(f"receiver.events[{idx}].redaction_applied must be a list")
    redactions = tuple({"path": str(path), "redaction_class": "validated"} for path in redaction_applied)
    for redacted in redactions:
        if not isinstance(redacted["path"], str) or not redacted["path"]:
            raise NormalizerError(f"receiver.events[{idx}].redaction_applied must contain non-empty strings")

    redaction_paths = {str(path) for path in redaction_applied}
    _verify_redaction_conformance(event["payload"], redaction_paths)

    payload = dict(event["payload"])
    logged_at = _coerce_iso8601(event["logged_at"])
    return {
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "logged_at": logged_at,
        "schema_version": schema_version,
        "payload": payload,
        "provenance": {
            "class": "backbond_receiver",
            "component": "post_bind_ingest.receiver",
            "receiver_version": "0.1.0",
        },
        "event_ingest": {
            "manifest_id": batch_id,
            "manifest_digest": batch_sha256,
            "event_digest": hashlib.sha256(_canonical_payload_bytes(event)).hexdigest(),
            "normalized_at": utc_now_iso(),
        },
        "redactions": redactions,
    }


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SECRET_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9]{12,}\b")
REDACTED_TOKEN_RE = re.compile(r"\[REDACTED(?::[^\]]+)?\]")


def _canonical_payload_bytes(value: Any) -> bytes:
    from .invariants import canonical_dumps

    return canonical_dumps(value)


def _verify_redaction_conformance(payload: dict[str, Any], redacted_paths: set[str]) -> None:
    redacted_paths = set(redacted_paths)
    for full_path, candidate in _walk_text_values(payload):
        if not isinstance(candidate, str):
            continue
        if REDACTED_TOKEN_RE.search(candidate):
            continue
        if EMAIL_RE.search(candidate) and full_path not in redacted_paths:
            raise RedactionConformanceError(
                f"unredacted email-like value at {full_path}; missing redaction_applied proof"
            )
        if SECRET_KEY_RE.search(candidate) and full_path not in redacted_paths:
            raise RedactionConformanceError(
                f"unredacted secret-like value at {full_path}; missing redaction_applied proof"
            )


def _walk_text_values(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            next_path = key if not path else f"{path}.{key}"
            yield from _walk_text_values(child, next_path)
        return
    if isinstance(value, list):
        for idx, child in enumerate(value):
            next_path = f"{path}[{idx}]"
            yield from _walk_text_values(child, next_path)
        return
    if isinstance(value, str) and value:
        yield path, value


def _coerce_iso8601(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat().replace("+00:00", "Z")
    except ValueError as exc:
        raise NormalizerError(f"logged_at must be ISO-8601: {value!r}") from exc
