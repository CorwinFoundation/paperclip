"""Manifest envelope schema validation.

Envelope per ADR-0034 §2.1:

  { customer_id, agent_id, day_utc, log_objects: [{sha256, byte_range,
    content_type, source_kind}], signature, signature_alg }

The validator is intentionally hand-coded and stdlib-only — no jsonschema
dependency in the post-bind ingest package. Drift between this validator and
the ADR is caught by the field-by-field conformance test.

Per Schema §Cross-event invariant 5, every manifest declares a single
`schema_version` value that all events in the manifest share. Mixed-version
manifests are rejected here, at the gateway, before the normalizer runs.
"""

from __future__ import annotations

import re
from typing import Any

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
DAY_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

SUPPORTED_SOURCE_KINDS = frozenset(
    {"datadog", "splunk", "cloudwatch", "otel", "stdout", "other"}
)


class ManifestSchemaError(ValueError):
    """Raised on a malformed manifest envelope."""


def validate_manifest(manifest: Any) -> dict[str, Any]:
    """Validate a manifest envelope. Returns the manifest unchanged on success.

    Raises `ManifestSchemaError` on any shape violation. The error message is
    deterministic for the same input so dead-letter records are byte-stable.
    """
    if not isinstance(manifest, dict):
        raise ManifestSchemaError("manifest must be a JSON object")
    required = ("customer_id", "agent_id", "day_utc", "schema_version",
                "log_objects", "signature", "signature_alg")
    for key in required:
        if key not in manifest:
            raise ManifestSchemaError(f"manifest missing required field {key!r}")
    if not UUID_RE.match(str(manifest["customer_id"])):
        raise ManifestSchemaError("customer_id must be a UUIDv4")
    if not UUID_RE.match(str(manifest["agent_id"])):
        raise ManifestSchemaError("agent_id must be a UUIDv4")
    if not DAY_UTC_RE.match(str(manifest["day_utc"])):
        raise ManifestSchemaError("day_utc must be YYYY-MM-DD")
    if manifest["schema_version"] != "log-event-schema-v1.0":
        raise ManifestSchemaError(
            f"schema_version {manifest['schema_version']!r} unsupported; expected 'log-event-schema-v1.0'"
        )
    if not isinstance(manifest["signature"], str) or not manifest["signature"]:
        raise ManifestSchemaError("signature must be a non-empty string")
    if not isinstance(manifest["signature_alg"], str):
        raise ManifestSchemaError("signature_alg must be a string")
    log_objects = manifest["log_objects"]
    if not isinstance(log_objects, list):
        raise ManifestSchemaError("log_objects must be an array")
    if not log_objects:
        raise ManifestSchemaError("log_objects must contain at least one entry")
    for index, obj in enumerate(log_objects):
        _validate_log_object(obj, index)
    return manifest


def _validate_log_object(obj: Any, index: int) -> None:
    where = f"log_objects[{index}]"
    if not isinstance(obj, dict):
        raise ManifestSchemaError(f"{where} must be a JSON object")
    for key in ("sha256", "byte_range", "content_type", "source_kind"):
        if key not in obj:
            raise ManifestSchemaError(f"{where} missing field {key!r}")
    if not SHA256_RE.match(str(obj["sha256"])):
        raise ManifestSchemaError(f"{where}.sha256 must match sha256:<64-hex>")
    byte_range = obj["byte_range"]
    if (
        not isinstance(byte_range, list)
        or len(byte_range) != 2
        or not all(isinstance(n, int) and n >= 0 for n in byte_range)
        or byte_range[0] > byte_range[1]
    ):
        raise ManifestSchemaError(
            f"{where}.byte_range must be [start, end] non-negative ints with start <= end"
        )
    if obj["source_kind"] not in SUPPORTED_SOURCE_KINDS:
        raise ManifestSchemaError(
            f"{where}.source_kind {obj['source_kind']!r} not in supported set"
        )
