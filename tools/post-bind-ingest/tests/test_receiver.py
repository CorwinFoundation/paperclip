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
from post_bind_ingest.evidence_pipeline import ingest_evidence_tier
from post_bind_ingest.evidence_tier import VerifiedSource
from post_bind_ingest.receiver import (
    ReceiverReplayError,
    ReceiverSignatureError,
    ReceiverSchemaError,
    RedactionConformanceError,
    build_mtls_server_context,
    parse_receiver_payload,
)
from post_bind_ingest.receiver.schema import compute_batch_sha256
from post_bind_ingest.receiver.validator import (
    BatchChainVerifier,
    GENESIS_BATCH_HASH,
    ReceiverChainError,
    WIRE_VERSION_V2,
    compute_chained_batch_hash,
)


PRIVATE_KEY = ed25519.Ed25519PrivateKey.generate()
KEY_ID = "bind-key-1"
PUBLIC_KEY_B64 = base64.urlsafe_b64encode(
    PRIVATE_KEY.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
).rstrip(b"=").decode("ascii")
KEYRING = {KEY_ID: PUBLIC_KEY_B64}


def _jwt_token(
    payload: dict,
    *,
    private_key: ed25519.Ed25519PrivateKey = PRIVATE_KEY,
    key_id: str | None = KEY_ID,
    algorithm: str = "EdDSA",
) -> str:
    header = {"alg": algorithm, "typ": "JWT"}
    if key_id is not None:
        header["kid"] = key_id
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


def _v2_payload(*, batch_id: str, prev_batch_hash: str) -> dict:
    payload = _receiver_payload()
    payload["batch_id"] = batch_id
    payload.update({"wire_version": WIRE_VERSION_V2, "prev_batch_hash": prev_batch_hash})
    payload["batch_hash"] = compute_chained_batch_hash(payload)
    claims = {
        "iss": "are-collector", "sub": payload["customer_id"], "agent": payload["agent"],
        **{key: payload[key] for key in (
            "schema_version", "batch_id", "batch_sha256", "event_count", "issued_at",
            "wire_version", "prev_batch_hash", "batch_hash",
        )},
        "exp": int(datetime.now(timezone.utc).timestamp()) + 600,
    }
    payload["jwt"] = _jwt_token(claims)
    return payload


def test_v1_cannot_detect_batch_deletion_or_reordering() -> None:
    first_payload = _receiver_payload()
    first_payload["jwt"] = _signed_receiver_payload(first_payload)
    second_payload = _receiver_payload()
    second_payload["batch_id"] = "batch-2"
    second_payload["jwt"] = _signed_receiver_payload(second_payload)
    first = parse_receiver_payload(first_payload, jwt_keyring=KEYRING)
    second = parse_receiver_payload(second_payload, jwt_keyring=KEYRING)
    assert first.prev_batch_hash is None and second.prev_batch_hash is None


def test_v2_chain_accepts_genesis_and_ordered_continuation() -> None:
    verifier = BatchChainVerifier()
    first = parse_receiver_payload(_v2_payload(batch_id="batch-1", prev_batch_hash=GENESIS_BATCH_HASH), jwt_keyring=KEYRING)
    verifier.accept(first)
    second = parse_receiver_payload(_v2_payload(batch_id="batch-2", prev_batch_hash=first.batch_hash), jwt_keyring=KEYRING)
    verifier.accept(second)
    assert verifier.head == second.batch_hash


@pytest.mark.parametrize("case", ["deletion", "reordering", "fork"])
def test_v2_chain_rejects_discontinuity(case: str) -> None:
    verifier = BatchChainVerifier()
    first = parse_receiver_payload(_v2_payload(batch_id="batch-1", prev_batch_hash=GENESIS_BATCH_HASH), jwt_keyring=KEYRING)
    second = parse_receiver_payload(_v2_payload(batch_id="batch-2", prev_batch_hash=first.batch_hash), jwt_keyring=KEYRING)
    third = parse_receiver_payload(_v2_payload(batch_id="batch-3", prev_batch_hash=second.batch_hash), jwt_keyring=KEYRING)
    verifier.accept(first)
    candidate = third if case != "fork" else parse_receiver_payload(
        _v2_payload(batch_id="fork", prev_batch_hash=GENESIS_BATCH_HASH), jwt_keyring=KEYRING
    )
    with pytest.raises(ReceiverChainError):
        verifier.accept(candidate)


def test_v2_chain_rejects_replay_and_signed_field_tamper() -> None:
    verifier = BatchChainVerifier()
    payload = _v2_payload(batch_id="batch-1", prev_batch_hash=GENESIS_BATCH_HASH)
    envelope = parse_receiver_payload(payload, jwt_keyring=KEYRING)
    verifier.accept(envelope)
    with pytest.raises(ReceiverReplayError):
        verifier.accept(envelope)
    payload["prev_batch_hash"] = "sha256:" + ("1" * 64)
    with pytest.raises(ReceiverSchemaError, match="claim mismatch"):
        parse_receiver_payload(payload, jwt_keyring=KEYRING)


def test_parse_receiver_payload_accepts_valid_batch() -> None:
    payload = _receiver_payload()
    token = _signed_receiver_payload(payload)
    payload["jwt"] = token

    env = parse_receiver_payload(payload, jwt_keyring=KEYRING)
    assert env.batch_id == "batch-1"
    assert env.batch_sha256 == payload["batch_sha256"]
    assert env.event_count == 1


def test_receiver_to_log_tracker_and_hub_evidence_tier_boundary() -> None:
    import sqlite3
    import sys
    from pathlib import Path
    log_tracker_src = Path(__file__).resolve().parents[2] / "log-tracker" / "src"
    sys.path.insert(0, str(log_tracker_src))
    from log_tracker.evidence_tier import append_assignment, list_assignments

    payload = _receiver_payload()
    payload["customer_id"] = "tenant-a"
    payload["agent"] = "agent-a"
    payload["jwt"] = _signed_receiver_payload(payload)
    pins = {"driver_version":"1", "collector_version":"1", "mapping_version":"1",
            "evidence_profile_version":"1", "signing_identity_ref":"spiffe://tenant-a/collector"}
    source = VerifiedSource(
        tenant_id="tenant-a", source_instance_id="source-a", source_class="in_code_otel",
        supported_surfaces=("coding",), source_pins=pins, identity_verified=True,
        signature_valid=True, hash_chain_valid=True, redaction_fail_closed=True,
        replay_idempotency_valid=True, continuity_verified=True, silence_distinguished_from_zero=True,
        coverage_bounded=True, class_specific_verified=True, verification_snapshot_hash="sha256:snapshot",
        verification_window_start="2026-08-01T00:00:00Z", verification_window_end="2026-08-02T00:00:00Z",
        registered_agent_id="agent-a",
    )
    conn = sqlite3.connect(":memory:")
    migration = Path(__file__).resolve().parents[2] / "log-tracker" / "migrations" / "0002_evidence_tier_assignment_v1.sql"
    conn.executescript(migration.read_text())
    boundary = ingest_evidence_tier(
        receiver_payload=payload, jwt_keyring=KEYRING, conn=conn, tenant_id="tenant-a", agent_id="agent-a",
        term_id="term-a", required_surfaces=("coding",), verified_sources=(source,), registered_pins=pins,
        as_of="2026-08-01T12:00:00Z", append_assignment=append_assignment, bound_current_term_multiplier=1.25,
    )
    assert boundary["accepted_event_count"] == 1
    assert boundary["hub"]["agent_tier"] == "T1"
    assert boundary["hub"]["freshness_state"] == "current"
    assert boundary["rate"]["current_term_multiplier"] == 1.25
    assert len(list_assignments(conn, tenant_id="tenant-a", agent_id="agent-a")) == 1
    assert list_assignments(conn, tenant_id="tenant-b", agent_id="agent-a") == []


@pytest.mark.parametrize("field,value,error", [
    ("customer_id", "tenant-b", "receiver_tenant_identity_mismatch"),
    ("agent", "agent-b", "receiver_agent_identity_mismatch"),
])
def test_receiver_signed_identity_mismatch_rejects_before_log_tracker(field, value, error) -> None:
    import sqlite3
    import sys
    log_tracker_src = Path(__file__).resolve().parents[2] / "log-tracker" / "src"
    sys.path.insert(0, str(log_tracker_src))
    from log_tracker.evidence_tier import append_assignment

    payload = _receiver_payload()
    payload["customer_id"] = "tenant-a"
    payload["agent"] = "agent-a"
    payload[field] = value
    payload["jwt"] = _signed_receiver_payload(payload)
    pins = {"driver_version":"1", "collector_version":"1", "mapping_version":"1",
            "evidence_profile_version":"1", "signing_identity_ref":"spiffe://tenant-a/collector"}
    source = VerifiedSource(
        tenant_id="tenant-a", source_instance_id="source-a", source_class="in_code_otel",
        supported_surfaces=("coding",), source_pins=pins, identity_verified=True, signature_valid=True,
        hash_chain_valid=True, redaction_fail_closed=True, replay_idempotency_valid=True,
        continuity_verified=True, silence_distinguished_from_zero=True, coverage_bounded=True,
        class_specific_verified=True, verification_snapshot_hash="sha256:snapshot",
        verification_window_start="2026-08-01T00:00:00Z", verification_window_end="2026-08-02T00:00:00Z",
        registered_agent_id="agent-a",
    )
    conn = sqlite3.connect(":memory:")
    migration = Path(__file__).resolve().parents[2] / "log-tracker" / "migrations" / "0002_evidence_tier_assignment_v1.sql"
    conn.executescript(migration.read_text())
    with pytest.raises(ValueError, match=f"^{error}$"):
        ingest_evidence_tier(
            receiver_payload=payload, jwt_keyring=KEYRING, conn=conn, tenant_id="tenant-a", agent_id="agent-a",
            term_id="term-a", required_surfaces=("coding",), verified_sources=(source,), registered_pins=pins,
            as_of="2026-08-01T12:00:00Z", append_assignment=append_assignment,
            bound_current_term_multiplier=1.25,
        )
    assert conn.execute("SELECT COUNT(*) FROM evidence_tier_events").fetchone()[0] == 0


def test_parse_receiver_payload_rejects_bad_signature() -> None:
    payload = _receiver_payload()
    payload["jwt"] = _signed_receiver_payload(payload)
    payload["jwt"] = payload["jwt"][:-2] + "aa"

    with pytest.raises(ReceiverSignatureError, match="JWT signature mismatch"):
        parse_receiver_payload(payload, jwt_keyring=KEYRING)


def test_parse_receiver_payload_rejects_missing_kid() -> None:
    payload = _receiver_payload()
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
        key_id=None,
    )

    with pytest.raises(ReceiverSignatureError, match="kid header is required"):
        parse_receiver_payload(payload, jwt_keyring=KEYRING)


def test_parse_receiver_payload_rejects_unknown_kid() -> None:
    payload = _receiver_payload()
    payload["jwt"] = _signed_receiver_payload(payload)

    with pytest.raises(ReceiverSignatureError, match="unknown JWT kid"):
        parse_receiver_payload(payload, jwt_keyring={"other-key": PUBLIC_KEY_B64})


def test_parse_receiver_payload_rejects_hs256_downgrade() -> None:
    payload = _receiver_payload()
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
        algorithm="HS256",
    )

    with pytest.raises(ReceiverSignatureError, match="unsupported JWT algorithm"):
        parse_receiver_payload(payload, jwt_keyring=KEYRING)


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
        parse_receiver_payload(payload, jwt_keyring=KEYRING, clock_skew_seconds=1)


def test_parse_receiver_payload_t_emitted_alias() -> None:
    payload = _receiver_payload()
    payload.pop("issued_at")
    payload["t_emitted"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["jwt"] = _signed_payload_with_alias(payload)

    env = parse_receiver_payload(payload, jwt_keyring=KEYRING)
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
    first = normalize_receiver_events(payload, jwt_keyring=KEYRING, idempotency_store=store)
    assert first
    with pytest.raises(ReceiverReplayError, match="replay batch_id rejected"):
        normalize_receiver_events(payload, jwt_keyring=KEYRING, idempotency_store=store)


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
        normalize_receiver_events(payload, jwt_keyring=KEYRING)


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

    events = normalize_receiver_events(payload, jwt_keyring=KEYRING)
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
        normalize_receiver_events(payload, jwt_keyring=KEYRING)


def test_parse_receiver_payload_rejects_wrong_schema_version() -> None:
    payload = _receiver_payload()
    payload["schema_version"] = "log-event-schema-v0.9"
    payload["jwt"] = _signed_receiver_payload(payload)

    with pytest.raises(ReceiverSchemaError, match="unsupported schema_version"):
        parse_receiver_payload(payload, jwt_keyring=KEYRING)


def test_parse_receiver_payload_rejects_wrong_batch_sha() -> None:
    payload = _receiver_payload()
    payload["batch_sha256"] = "sha256:" + ("0" * 64)
    payload["jwt"] = _signed_receiver_payload(payload)

    with pytest.raises(ReceiverSchemaError, match="batch_sha256 does not match"):
        parse_receiver_payload(payload, jwt_keyring=KEYRING)


def test_receiver_schema_conformance_n5_is_stable() -> None:
    for idx in range(5):
        payload = _receiver_payload()
        payload["batch_id"] = f"batch-n5-{idx}"
        payload["batch_sha256"] = f"sha256:{compute_batch_sha256(payload['events'])}"
        payload["jwt"] = _signed_receiver_payload(payload)
        envelope = parse_receiver_payload(payload, jwt_keyring=KEYRING)
        assert envelope.event_count == 1
        assert envelope.schema_version == "log-event-schema-v1.0"


def test_receiver_replay_defense_n5_rejects_replays(tmp_path: Path) -> None:
    for idx in range(5):
        payload = _receiver_payload()
        payload["batch_id"] = f"batch-replay-{idx}"
        payload["jwt"] = _signed_receiver_payload(payload)
        store = ManifestIdempotencyStore(path=tmp_path / f"replay-{idx}.state")
        normalize_receiver_events(payload, jwt_keyring=KEYRING, idempotency_store=store)
        with pytest.raises(ReceiverReplayError):
            normalize_receiver_events(payload, jwt_keyring=KEYRING, idempotency_store=store)


def test_mtls_context_requires_tls13_and_client_certificate() -> None:
    context = build_mtls_server_context()
    assert context.minimum_version is ssl.TLSVersion.TLSv1_3
    assert context.maximum_version is ssl.TLSVersion.TLSv1_3
    assert context.verify_mode is ssl.CERT_REQUIRED


def _sign_events_payload(payload: dict) -> None:
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


def test_redaction_fails_closed_when_declared_path_still_leaks_secret() -> None:
    # Fail-closed core: vendor DECLARES `arg` redacted but the value is still a
    # raw secret. The declaration must not buy a pass.
    payload = _receiver_payload()
    payload["events"][0]["payload"] = {"tool_name": "payment", "arg": "sk-ab12cdef3456"}
    payload["events"][0]["redaction_applied"] = ["arg"]
    _sign_events_payload(payload)

    with pytest.raises(RedactionConformanceError, match="not trusted \\(fail-closed\\)"):
        normalize_receiver_events(payload, jwt_keyring=KEYRING)


def test_redaction_fails_closed_when_declared_path_still_leaks_email() -> None:
    payload = _receiver_payload()
    payload["events"][0]["payload"] = {"tool_name": "email", "arg": "alice@example.com"}
    payload["events"][0]["redaction_applied"] = ["arg"]
    _sign_events_payload(payload)

    with pytest.raises(RedactionConformanceError, match="not trusted \\(fail-closed\\)"):
        normalize_receiver_events(payload, jwt_keyring=KEYRING)


def test_redaction_fails_closed_when_embedded_sentinel_still_leaks_email() -> None:
    payload = _receiver_payload()
    payload["events"][0]["payload"] = {"tool_name": "email", "arg": "[REDACTED] alice@example.com"}
    payload["events"][0]["redaction_applied"] = ["arg"]
    _sign_events_payload(payload)

    with pytest.raises(RedactionConformanceError, match="email-like value"):
        normalize_receiver_events(payload, jwt_keyring=KEYRING)


def test_redaction_fails_closed_when_embedded_sentinel_still_leaks_secret() -> None:
    payload = _receiver_payload()
    payload["events"][0]["payload"] = {"tool_name": "payment", "arg": "[REDACTED] sk-ab12cdef3456"}
    payload["events"][0]["redaction_applied"] = ["arg"]
    _sign_events_payload(payload)

    with pytest.raises(RedactionConformanceError, match="secret-like value"):
        normalize_receiver_events(payload, jwt_keyring=KEYRING)


def test_redaction_fails_closed_when_declared_path_not_proven_redacted() -> None:
    # Vendor declares `arg` redacted but leaves an ordinary, non-sentinel value.
    # We cannot confirm redaction actually happened -> reject.
    payload = _receiver_payload()
    payload["events"][0]["payload"] = {"tool_name": "note", "arg": "some plaintext value"}
    payload["events"][0]["redaction_applied"] = ["arg"]
    _sign_events_payload(payload)

    with pytest.raises(RedactionConformanceError, match="not proven redacted"):
        normalize_receiver_events(payload, jwt_keyring=KEYRING)


@pytest.mark.parametrize(
    "value",
    [
        123456789,
        ["[REDACTED]"],
        {"nested": "[REDACTED]"},
    ],
)
def test_redaction_fails_closed_for_unsupported_declared_value_shapes(value: object) -> None:
    payload = _receiver_payload()
    payload["events"][0]["payload"] = {"tool_name": "note", "arg": value}
    payload["events"][0]["redaction_applied"] = ["arg"]
    _sign_events_payload(payload)

    with pytest.raises(RedactionConformanceError, match="not proven redacted"):
        normalize_receiver_events(payload, jwt_keyring=KEYRING)


def test_redaction_accepts_declared_path_with_hash_sentinel() -> None:
    payload = _receiver_payload()
    payload["events"][0]["payload"] = {"tool_name": "payment", "arg": "sha256:" + ("a" * 64)}
    payload["events"][0]["redaction_applied"] = ["arg"]
    _sign_events_payload(payload)

    events = normalize_receiver_events(payload, jwt_keyring=KEYRING)
    assert events[0]["redactions"] == ({"path": "arg", "redaction_class": "validated"},)


def test_redaction_accepts_declared_path_cleared_to_null() -> None:
    payload = _receiver_payload()
    payload["events"][0]["payload"] = {"tool_name": "payment", "arg": None}
    payload["events"][0]["redaction_applied"] = ["arg"]
    _sign_events_payload(payload)

    events = normalize_receiver_events(payload, jwt_keyring=KEYRING)
    assert events[0]["event_type"] == "tool_call"
