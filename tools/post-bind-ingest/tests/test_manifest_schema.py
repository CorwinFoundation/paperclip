"""Manifest envelope schema validation + Schema invariant 5 (schema_version stability)."""

from __future__ import annotations

import copy

import pytest

from post_bind_ingest.ingest_gateway.manifest_schema import (
    ManifestSchemaError,
    validate_manifest,
)


VALID = {
    "customer_id": "11111111-1111-4111-8111-111111111111",
    "agent_id": "22222222-2222-4222-8222-222222222222",
    "day_utc": "2026-06-17",
    "schema_version": "log-event-schema-v1.0",
    "log_objects": [
        {"sha256": "sha256:" + "a" * 64, "byte_range": [0, 100],
         "content_type": "application/jsonl", "source_kind": "stdout"}
    ],
    "signature": "deadbeef",
    "signature_alg": "hmac-sha256",
}


def test_valid_manifest_accepted() -> None:
    assert validate_manifest(copy.deepcopy(VALID)) == VALID


@pytest.mark.parametrize("missing_key", list(VALID.keys()))
def test_missing_required_field_rejected(missing_key: str) -> None:
    bad = copy.deepcopy(VALID)
    bad.pop(missing_key)
    with pytest.raises(ManifestSchemaError, match="missing required field"):
        validate_manifest(bad)


def test_bad_uuid_rejected() -> None:
    bad = copy.deepcopy(VALID)
    bad["customer_id"] = "not-a-uuid"
    with pytest.raises(ManifestSchemaError, match="UUIDv4"):
        validate_manifest(bad)


def test_unsupported_schema_version_rejected() -> None:
    """Schema invariant 5 — only log-event-schema-v1.0 accepted at gateway."""
    bad = copy.deepcopy(VALID)
    bad["schema_version"] = "log-event-schema-v0.9"
    with pytest.raises(ManifestSchemaError, match="unsupported"):
        validate_manifest(bad)


def test_bad_sha256_rejected() -> None:
    bad = copy.deepcopy(VALID)
    bad["log_objects"][0]["sha256"] = "deadbeef"
    with pytest.raises(ManifestSchemaError, match="sha256:"):
        validate_manifest(bad)


def test_empty_log_objects_rejected() -> None:
    bad = copy.deepcopy(VALID)
    bad["log_objects"] = []
    with pytest.raises(ManifestSchemaError, match="at least one entry"):
        validate_manifest(bad)


def test_bad_byte_range_rejected() -> None:
    bad = copy.deepcopy(VALID)
    bad["log_objects"][0]["byte_range"] = [10, 5]
    with pytest.raises(ManifestSchemaError, match="byte_range"):
        validate_manifest(bad)


def test_bad_source_kind_rejected() -> None:
    bad = copy.deepcopy(VALID)
    bad["log_objects"][0]["source_kind"] = "vendor-x"
    with pytest.raises(ManifestSchemaError, match="source_kind"):
        validate_manifest(bad)
