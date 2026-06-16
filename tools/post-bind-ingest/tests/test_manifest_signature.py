"""HMAC-SHA256 manifest signature: round-trip + tamper detection + unsupported alg."""

from __future__ import annotations

import pytest

from post_bind_ingest.ingest_gateway.manifest_signature import (
    ManifestSignatureError,
    SUPPORTED_SIGNATURE_ALG,
    sign_manifest_body,
    verify_manifest_signature,
)


KEY = b"fixture-key-do-not-use-in-prod-0123456789ab"


def _body() -> dict:
    return {
        "customer_id": "11111111-1111-4111-8111-111111111111",
        "agent_id": "22222222-2222-4222-8222-222222222222",
        "day_utc": "2026-06-17",
        "schema_version": "log-event-schema-v1.0",
        "log_objects": [
            {"sha256": "sha256:" + "a" * 64, "byte_range": [0, 10],
             "content_type": "application/jsonl", "source_kind": "stdout"}
        ],
    }


def test_round_trip_verifies() -> None:
    body = _body()
    sig = sign_manifest_body(body, KEY)
    verify_manifest_signature(body, sig, SUPPORTED_SIGNATURE_ALG, KEY)


def test_tampered_body_rejected() -> None:
    body = _body()
    sig = sign_manifest_body(body, KEY)
    body["customer_id"] = "33333333-3333-4333-8333-333333333333"
    with pytest.raises(ManifestSignatureError):
        verify_manifest_signature(body, sig, SUPPORTED_SIGNATURE_ALG, KEY)


def test_wrong_key_rejected() -> None:
    body = _body()
    sig = sign_manifest_body(body, KEY)
    with pytest.raises(ManifestSignatureError):
        verify_manifest_signature(body, sig, SUPPORTED_SIGNATURE_ALG, b"other-key" * 5)


def test_unsupported_alg_rejected() -> None:
    body = _body()
    sig = sign_manifest_body(body, KEY)
    with pytest.raises(ManifestSignatureError, match="unsupported"):
        verify_manifest_signature(body, sig, "rsa-sha256", KEY)
