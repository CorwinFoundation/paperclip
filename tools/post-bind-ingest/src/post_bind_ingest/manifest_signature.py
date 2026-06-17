from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .invariants import canonical_dumps


ALLOWED_SCHEMA_VERSIONS = {"log-event-schema-v1.0"}


class ManifestSchemaError(ValueError):
    """Raised when a manifest payload fails structural validation."""


class ManifestSignatureError(ValueError):
    """Raised when a manifest signature is absent, malformed, or invalid."""


@dataclass(frozen=True)
class ManifestSignature:
    algorithm: str
    value: str
    key_id: str | None = None

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "ManifestSignature":
        if not isinstance(value, Mapping):
            raise ManifestSchemaError("manifest.signature must be an object")
        algorithm = value.get("algorithm")
        signature = value.get("value")
        if not isinstance(algorithm, str) or not algorithm:
            raise ManifestSchemaError("manifest.signature.algorithm must be a non-empty string")
        if not isinstance(signature, str) or not signature:
            raise ManifestSchemaError("manifest.signature.value must be a non-empty string")
        key_id = value.get("key_id")
        if key_id is not None and not isinstance(key_id, str):
            raise ManifestSchemaError("manifest.signature.key_id must be a string when present")
        return cls(algorithm=algorithm, value=signature, key_id=key_id)


def validate_manifest_schema(manifest: Mapping[str, Any]) -> None:
    """Validate structural invariants for the push-mode S3 manifest envelope."""
    required = {
        "manifest_version": str,
        "manifest_id": str,
        "customer_id": str,
        "schema_version": str,
        "signature": Mapping,
        "issued_at": str,
        "events": list,
    }
    for field, expected_type in required.items():
        value = manifest.get(field)
        if value is None:
            raise ManifestSchemaError(f"missing required field: {field}")
        if not isinstance(value, expected_type):
            raise ManifestSchemaError(f"field {field} must be {expected_type.__name__}")

    if manifest["manifest_version"] not in {"1", "v1"}:
        raise ManifestSchemaError(
            "manifest.manifest_version must be '1' or 'v1'"
        )
    schema_version = manifest["schema_version"]
    if schema_version not in ALLOWED_SCHEMA_VERSIONS:
        raise ManifestSchemaError(f"unsupported schema_version: {schema_version!r}")

    if not manifest["manifest_id"].strip():
        raise ManifestSchemaError("manifest.manifest_id must not be empty")
    if not manifest["customer_id"].strip():
        raise ManifestSchemaError("manifest.customer_id must not be empty")

    issued_at = manifest["issued_at"]
    try:
        datetime.fromisoformat(issued_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestSchemaError("manifest.issued_at must be ISO-8601 datetime") from exc

    events = manifest["events"]
    if not events:
        raise ManifestSchemaError("manifest.events must contain at least one event")
    for idx, event in enumerate(events):
        if not isinstance(event, Mapping):
            raise ManifestSchemaError(f"manifest.events[{idx}] must be an object")
        _validate_event(event, idx)

    _ = ManifestSignature.parse(manifest["signature"])


def parse_manifest(raw: str | bytes) -> dict[str, Any]:
    try:
        manifest: dict[str, Any] = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ManifestSchemaError("manifest payload is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ManifestSchemaError("manifest root must be a JSON object")
    validate_manifest_schema(manifest)
    return manifest


def verify_manifest_signature(manifest: Mapping[str, Any], secret: str | bytes | None = None) -> None:
    """Verify manifest signature with supported algorithms."""
    signature = ManifestSignature.parse(manifest["signature"])
    payload = _manifest_payload_without_signature(manifest)
    expected = _hash_payload(payload, signature.algorithm, secret=secret)
    if expected is None:
        raise ManifestSignatureError(f"unsupported signature algorithm: {signature.algorithm}")
    if not hmac.compare_digest(signature.value, expected):
        raise ManifestSignatureError("manifest signature mismatch")


def _manifest_payload_without_signature(manifest: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = dict(manifest)
    sanitized = {key: value for key, value in sanitized.items() if key != "signature"}
    return sanitized


def _hash_payload(payload: Mapping[str, Any], algorithm: str, secret: str | bytes | None = None) -> str | None:
    canonical = canonical_dumps(payload)
    if algorithm in {"hmac-sha256", "sha256-hmac"}:
        if secret is None:
            raise ManifestSignatureError("secret required for hmac-sha256 manifest signatures")
        key = secret if isinstance(secret, (bytes, bytearray)) else str(secret).encode("utf-8")
        return hmac.new(key, canonical, hashlib.sha256).hexdigest()
    if algorithm == "sha256":
        return hashlib.sha256(canonical).hexdigest()
    if algorithm == "sha256-noprefix":
        return hashlib.sha256(canonical).hexdigest()
    return None


def normalize_and_digest_events(manifest: Mapping[str, Any]) -> tuple[tuple[str, ...], str]:
    """
    Return ordered event ids and deterministic manifest event hash.
    """
    event_ids = tuple(_event_id(event, idx) for idx, event in enumerate(manifest["events"]))
    event_digests = tuple(hashlib.sha256(canonical_dumps(event)).hexdigest() for event in manifest["events"])
    manifest_digest_input = b"\n".join(digest.encode("utf-8") for digest in event_digests)
    manifest_digest = hashlib.sha256(manifest_digest_input).hexdigest()
    return event_ids, manifest_digest


def _validate_event(event: Mapping[str, Any], idx: int) -> None:
    required = {"event_id": str, "event_type": str, "logged_at": str, "payload": Mapping}
    for field, expected_type in required.items():
        value = event.get(field)
        if value is None:
            raise ManifestSchemaError(f"manifest.events[{idx}].{field} is required")
        if not isinstance(value, expected_type):
            raise ManifestSchemaError(f"manifest.events[{idx}].{field} must be {expected_type.__name__}")
    _validate_event_iso(event["logged_at"], f"manifest.events[{idx}].logged_at")


def _validate_event_iso(value: str, field: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestSchemaError(f"{field} must be ISO-8601 datetime") from exc


def _event_id(event: Mapping[str, Any], index: int) -> str:
    return str(event.get("event_id") or f"{event.get('event_type', 'event')}::{index}")
