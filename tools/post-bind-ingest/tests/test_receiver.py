from __future__ import annotations

import base64
import json
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
import pytest

from post_bind_ingest.invariants import ManifestIdempotencyStore
from post_bind_ingest.normalizer import NormalizerError, normalize_receiver_events
from post_bind_ingest.receiver import (
    ReceiverReplayError,
    ReceiverSignatureError,
    ReceiverSchemaError,
    RedactionConformanceError,
    build_mtls_server_context,
    parse_receiver_payload,
)
from post_bind_ingest.receiver.schema import compute_batch_sha256


PRIVATE_KEY = ed25519.Ed25519PrivateKey.generate()
PUBLIC_KEY_B64 = base64.urlsafe_b64encode(
    PRIVATE_KEY.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
).rstrip(b"=").decode("ascii")


def _jwt_token(payload: dict, *, private_key: ed25519.Ed25519PrivateKey = PRIVATE_KEY) -> str:
    header = {"alg": "ed25519", "typ": "JWT"}
    header_b64 = base64.urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8")).rstrip(b"=").decode(
        "ascii"
    )
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    signing = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = private_key.sign(signing)
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def _receiver_payload() -> dict:
    issued_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        {
            "event_type": "tool_call",
            "event_id": "evt-1",
            "logged_at": "2026-06-16T15:00:05Z",
            "payload": {"tool_name": "calculator", "arg_count": 2},
            "redaction_applied": [],
        }
    ]
    batch_sha256 = f"sha256:{compute_batch_sha256(events)}"
    return {
        "schema_version": "log-event-schema-v1.0",
        "customer_id": "cust-1",
        "agent": "agent-1",
        "batch_id": "batch-1",
        "batch_sha256": batch_sha256,
        "event_count": 1,
        "issued_at": issued_at,
        "events": events,
    }


def _signed_receiver_payload(payload: dict) -> str:
    return _jwt_token(
        {
            "iss": "are-collector",
            "sub": payload["customer_id"],
            "agent": payload["agent"],
            "schema_version": payload["schema_version"],
            "batch_id": payload["batch_id"],
            "batch_sha256": payload["batch_sha256"],
            "event_count": payload["event_count"],
            "issued_at": payload["issued_at"],
            "exp": int(datetime.now(timezone.utc).timestamp()) + 600,
        },
    )


def _signed_payload_with_alias(payload: dict) -> str:
    claim_key = "issued_at" if "issued_at" in payload else "t_emitted"
    claims = {
        "iss": "are-collector",
        "sub": payload["customer_id"],
        "agent": payload["agent"],
        "schema_version": payload["schema_version"],
        "batch_id": payload["batch_id"],
        "batch_sha256": payload["batch_sha256"],
        "event_count": payload["event_count"],
        claim_key: payload[claim_key],
        "exp": int(datetime.now(timezone.utc).timestamp()) + 600,
    }
    return _jwt_token(claims)


def test_parse_receiver_payload_accepts_valid_batch() -> None:
    payload = _receiver_payload()
    token = _signed_receiver_payload(payload)
    payload["jwt"] = token

    env = parse_receiver_payload(payload, jwt_public_key=PUBLIC_KEY_B64)
    assert env.batch_id == "batch-1"
    assert env.batch_sha256 == payload["batch_sha256"]
    assert env.event_count == 1


def test_parse_receiver_payload_rejects_bad_signature() -> None:
    payload = _receiver_payload()
    payload["jwt"] = _signed_receiver_payload(payload)
    payload["jwt"] = payload["jwt"][:-2] + "aa"

    with pytest.raises(ReceiverSignatureError, match="JWT signature mismatch"):
        parse_receiver_payload(payload, jwt_public_key=PUBLIC_KEY_B64)


def test_parse_receiver_payload_skew_rejects_out_of_window() -> None:
    payload = _receiver_payload()
    payload["issued_at"] = "2010-01-01T00:00:00Z"
    payload["jwt"] = _jwt_token(
        {
            "agent": payload["agent"],
            "batch_id": payload["batch_id"],
            "batch_sha256": payload["batch_sha256"],
            "event_count": payload["event_count"],
            "issued_at": payload["issued_at"],
            "schema_version": payload["schema_version"],
            "sub": payload["customer_id"],
        },
    )

    with pytest.raises(ReceiverSignatureError, match="outside allowed clock skew"):
        parse_receiver_payload(payload, jwt_public_key=PUBLIC_KEY_B64, clock_skew_seconds=1)


def test_parse_receiver_payload_t_emitted_alias() -> None:
    payload = _receiver_payload()
    payload.pop("issued_at")
    payload["t_emitted"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["jwt"] = _signed_payload_with_alias(payload)

    env = parse_receiver_payload(payload, jwt_public_key=PUBLIC_KEY_B64)
    assert env.batch_id == "batch-1"


def test_normalize_receiver_events_replay_batch_id_rejected(tmp_path: Path) -> None:
    payload = _receiver_payload()
    store = ManifestIdempotencyStore(path=tmp_path / "receiver-idempotent.state")
    token = _jwt_token(
        {
            "iss": "are-collector",
            "sub": payload["customer_id"],
            "agent": payload["agent"],
            "schema_version": payload["schema_version"],
            "batch_id": payload["batch_id"],
            "batch_sha256": payload["batch_sha256"],
            "event_count": payload["event_count"],
            "issued_at": payload["issued_at"],
        },
    )
    payload["jwt"] = token
    first = normalize_receiver_events(payload, jwt_public_key=PUBLIC_KEY_B64, idempotency_store=store)
    assert first
    with pytest.raises(ReceiverReplayError, match="replay batch_id rejected"):
        normalize_receiver_events(payload, jwt_public_key=PUBLIC_KEY_B64, idempotency_store=store)


def test_normalize_receiver_events_rejects_unredacted_secret_candidate() -> None:
    payload = _receiver_payload()
    payload["events"][0]["payload"] = {
        "tool_name": "payment",
        "arg": "sk-ab12cdef3456",
    }
    payload["events"][0]["redaction_applied"] = []
    payload["batch_sha256"] = f"sha256:{compute_batch_sha256(payload['events'])}"
    payload["jwt"] = _jwt_token(
        {
            "agent": payload["agent"],
            "batch_id": payload["batch_id"],
            "batch_sha256": payload["batch_sha256"],
            "event_count": payload["event_count"],
            "issued_at": payload["issued_at"],
            "schema_version": payload["schema_version"],
            "sub": payload["customer_id"],
        },
    )

    with pytest.raises(RedactionConformanceError, match="unredacted secret-like value"):
        normalize_receiver_events(payload, jwt_public_key=PUBLIC_KEY_B64)


def test_normalize_receiver_events_accepts_schema_verified_redacted_secret() -> None:
    payload = _receiver_payload()
    payload["events"][0]["payload"] = {
        "tool_name": "payment",
        "arg": "[REDACTED:secret_family]",
    }
    payload["events"][0]["redaction_applied"] = ["arg"]
    payload["batch_sha256"] = f"sha256:{compute_batch_sha256(payload['events'])}"
    payload["jwt"] = _jwt_token(
        {
            "agent": payload["agent"],
            "batch_id": payload["batch_id"],
            "batch_sha256": payload["batch_sha256"],
            "event_count": payload["event_count"],
            "issued_at": payload["issued_at"],
            "schema_version": payload["schema_version"],
            "sub": payload["customer_id"],
        },
    )

    events = normalize_receiver_events(payload, jwt_public_key=PUBLIC_KEY_B64)
    assert len(events) == 1
    assert events[0]["event_type"] == "tool_call"
    assert events[0]["redactions"] == ({"path": "arg", "redaction_class": "validated"},)
    assert events[0]["event_ingest"]["manifest_id"] == "batch-1"


def test_normalizer_rejects_invalid_redaction_claim_type() -> None:
    payload = _receiver_payload()
    payload["events"][0]["redaction_applied"] = "oops"
    payload["batch_sha256"] = f"sha256:{compute_batch_sha256(payload['events'])}"
    payload["jwt"] = _jwt_token(
        {
            "agent": payload["agent"],
            "batch_id": payload["batch_id"],
            "batch_sha256": payload["batch_sha256"],
            "event_count": payload["event_count"],
            "issued_at": payload["issued_at"],
            "schema_version": payload["schema_version"],
            "sub": payload["customer_id"],
        },
    )

    with pytest.raises((ReceiverSchemaError, NormalizerError)):
        normalize_receiver_events(payload, jwt_public_key=PUBLIC_KEY_B64)


def test_parse_receiver_payload_rejects_wrong_schema_version() -> None:
    payload = _receiver_payload()
    payload["schema_version"] = "log-event-schema-v0.9"
    payload["jwt"] = _signed_receiver_payload(payload)

    with pytest.raises(ReceiverSchemaError, match="unsupported schema_version"):
        parse_receiver_payload(payload, jwt_public_key=PUBLIC_KEY_B64)


def test_parse_receiver_payload_rejects_wrong_batch_sha() -> None:
    payload = _receiver_payload()
    payload["batch_sha256"] = "sha256:" + ("0" * 64)
    payload["jwt"] = _signed_receiver_payload(payload)

    with pytest.raises(ReceiverSchemaError, match="batch_sha256 does not match"):
        parse_receiver_payload(payload, jwt_public_key=PUBLIC_KEY_B64)


def test_receiver_schema_conformance_n5_is_stable() -> None:
    for idx in range(5):
        payload = _receiver_payload()
        payload["batch_id"] = f"batch-n5-{idx}"
        payload["batch_sha256"] = f"sha256:{compute_batch_sha256(payload['events'])}"
        payload["jwt"] = _signed_receiver_payload(payload)
        envelope = parse_receiver_payload(payload, jwt_public_key=PUBLIC_KEY_B64)
        assert envelope.event_count == 1
        assert envelope.schema_version == "log-event-schema-v1.0"


def test_receiver_replay_defense_n5_rejects_replays(tmp_path: Path) -> None:
    for idx in range(5):
        payload = _receiver_payload()
        payload["batch_id"] = f"batch-replay-{idx}"
        payload["jwt"] = _signed_receiver_payload(payload)
        store = ManifestIdempotencyStore(path=tmp_path / f"replay-{idx}.state")
        normalize_receiver_events(payload, jwt_public_key=PUBLIC_KEY_B64, idempotency_store=store)
        with pytest.raises(ReceiverReplayError):
            normalize_receiver_events(payload, jwt_public_key=PUBLIC_KEY_B64, idempotency_store=store)


def test_mtls_context_requires_tls13_and_client_certificate() -> None:
    context = build_mtls_server_context()
    assert context.minimum_version is ssl.TLSVersion.TLSv1_3
    assert context.maximum_version is ssl.TLSVersion.TLSv1_3
    assert context.verify_mode is ssl.CERT_REQUIRED
