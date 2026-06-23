from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from .schema import (
    REQUIRED_BATCH_KEYS,
    ReceiverError,
    ReceiverSchemaError,
    RedactionConformanceError,
    _get_issued_at_field,
    validate_batch_schema,
    compute_batch_sha256,
)


class ReceiverSignatureError(ReceiverError):
    """Raised when receiver JWT verification fails."""


class ReceiverReplayError(ReceiverError):
    """Raised when a receiver batch is a replay."""


_JWT_PART_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _b64url_decode(value: str) -> bytes:
    payload = value.rstrip("=")
    if not _JWT_PART_RE.fullmatch(payload):
        raise ReceiverSignatureError("JWT segment must be URL-safe base64")
    padding_len = (-len(payload)) % 4
    return base64.urlsafe_b64decode(payload + ("=" * padding_len))


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _coerce_jwt_payload(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise ReceiverSignatureError("JWT payload must be a JSON object")
    return raw


def _load_ed25519_public_key(
    public_key: ed25519.Ed25519PublicKey | bytes | str | None,
) -> ed25519.Ed25519PublicKey:
    if public_key is None:
        raise ReceiverSignatureError("jwt keyring entry is required to verify receiver token")
    if isinstance(public_key, ed25519.Ed25519PublicKey):
        return public_key
    key_bytes = public_key if isinstance(public_key, bytes) else str(public_key).encode("utf-8")
    try:
        if b"BEGIN PUBLIC KEY" in key_bytes:
            loaded = serialization.load_pem_public_key(key_bytes)
            if not isinstance(loaded, ed25519.Ed25519PublicKey):
                raise ReceiverSignatureError("jwt keyring entry must be ed25519")
            return loaded
        return ed25519.Ed25519PublicKey.from_public_bytes(_b64url_decode(key_bytes.decode("ascii").strip()))
    except ReceiverSignatureError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ReceiverSignatureError("jwt keyring entry is not a valid ed25519 public key") from exc


def verify_jwt_signature(
    token: str,
    public_keyring: Mapping[str, ed25519.Ed25519PublicKey | bytes | str] | None,
) -> Mapping[str, Any]:
    if not token:
        raise ReceiverSignatureError("JWT token is required for receiver mode")

    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError as exc:
        raise ReceiverSignatureError("JWT must be in header.payload.signature format") from exc

    try:
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        payload_raw = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ReceiverSignatureError("JWT header/payload are not valid base64 JSON") from exc

    if not isinstance(header, Mapping) or not isinstance(payload_raw, Mapping):
        raise ReceiverSignatureError("JWT header and payload must be objects")
    algorithm = header.get("alg")
    if algorithm != "EdDSA":
        raise ReceiverSignatureError(f"unsupported JWT algorithm: {algorithm!r}")
    key_id = header.get("kid")
    if not isinstance(key_id, str) or not key_id:
        raise ReceiverSignatureError("JWT kid header is required")
    if public_keyring is None:
        raise ReceiverSignatureError("jwt_keyring is required to verify receiver token")
    public_key = public_keyring.get(key_id)
    if public_key is None:
        raise ReceiverSignatureError(f"unknown JWT kid: {key_id!r}")

    verifier = _load_ed25519_public_key(public_key)
    try:
        verifier.verify(
            _b64url_decode(signature_b64),
            f"{header_b64}.{payload_b64}".encode("ascii"),
        )
    except InvalidSignature as exc:
        raise ReceiverSignatureError("JWT signature mismatch")

    return payload_raw


@dataclass(frozen=True)
class ReceiverEnvelope:
    payload: Mapping[str, Any]
    events: list[dict[str, Any]]
    customer_id: str
    agent_id: str
    schema_version: str
    batch_id: str
    batch_sha256: str
    event_count: int
    issued_at: str
    signed: bool = True


def _parse_issued_at(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReceiverSchemaError("issued_at must be ISO-8601 datetime") from exc


def _parse_expiration(payload: Mapping[str, Any]) -> None:
    exp = payload.get("exp")
    if exp is None:
        return
    if not isinstance(exp, (int, float)):
        raise ReceiverSchemaError("exp must be a unix timestamp")
    if datetime.now(timezone.utc).timestamp() > float(exp):
        raise ReceiverSignatureError("jwt is expired")


def parse_receiver_payload(
    payload: str | Mapping[str, Any],
    *,
    jwt_keyring: Mapping[str, ed25519.Ed25519PublicKey | bytes | str] | None = None,
    clock_skew_seconds: int = 300,
    allow_unsigned: bool = False,
) -> ReceiverEnvelope:
    try:
        raw_payload = json.loads(payload) if isinstance(payload, str) else payload
    except json.JSONDecodeError as exc:
        raise ReceiverSchemaError("receiver payload is not valid JSON") from exc

    if not isinstance(raw_payload, Mapping):
        raise ReceiverSchemaError("receiver payload must be a JSON object")

    signed = False
    envelope_claims: Mapping[str, Any] | None = None
    if "jwt" in raw_payload or "authorization" in raw_payload:
        token = str(raw_payload.get("jwt") or raw_payload.get("authorization", ""))
        token = token.removeprefix("Bearer ").strip()
        envelope_payload = verify_jwt_signature(token, jwt_keyring)
        signed = True
        envelope_claims = envelope_payload
    elif not allow_unsigned:
        raise ReceiverSignatureError("receiver payload missing JWT token")
    else:
        envelope_claims = raw_payload

    merged = dict(raw_payload)
    for key in REQUIRED_BATCH_KEYS:
        if key not in merged and key in envelope_claims:
            merged[key] = envelope_claims[key]

    if "issued_at" not in merged and "t_emitted" not in merged and envelope_claims:
        if "issued_at" in envelope_claims:
            merged["issued_at"] = envelope_claims["issued_at"]
        elif "t_emitted" in envelope_claims:
            merged["issued_at"] = envelope_claims["t_emitted"]

    validate_batch_schema(merged)

    try:
        _parse_expiration(envelope_claims)
    except (TypeError, ValueError) as exc:
        raise ReceiverSchemaError("invalid jwt exp claim") from exc

    claims_event_count = merged["event_count"]
    if claims_event_count != len(merged["events"]):
        raise ReceiverSchemaError("event_count must equal len(events)")

    # Ensure signed claims bind the same identity and batch as the request body.
    claim_customer_id = envelope_claims.get("customer_id", envelope_claims.get("sub")) if envelope_claims else None
    claim_agent = envelope_claims.get("agent") if envelope_claims else None
    claim_fields = {
        "customer_id": claim_customer_id,
        "agent": claim_agent,
        "schema_version": envelope_claims.get("schema_version") if envelope_claims else None,
        "batch_id": envelope_claims.get("batch_id") if envelope_claims else None,
        "batch_sha256": envelope_claims.get("batch_sha256") if envelope_claims else None,
        "event_count": envelope_claims.get("event_count") if envelope_claims else None,
    }
    for field, claim_value in claim_fields.items():
        if claim_value is not None and claim_value != merged.get(field):
            raise ReceiverSchemaError(f"receiver payload claim mismatch for {field}")

    now = datetime.now(timezone.utc)
    issued_at = _parse_issued_at(_get_issued_at_field(merged))
    delta = abs((now - issued_at).total_seconds())
    if delta > clock_skew_seconds:
        raise ReceiverSignatureError("jwt issued_at is outside allowed clock skew")

    calculated = f"sha256:{compute_batch_sha256(list(merged['events']))}"
    if not str(merged["batch_sha256"]).startswith("sha256:"):
        raise ReceiverSchemaError("batch_sha256 must use sha256 prefix format")
    if merged["batch_sha256"] != calculated:
        raise ReceiverSchemaError("batch_sha256 does not match event payload")

    if envelope_claims is None:
        raise ReceiverSignatureError("receiver envelope claims unavailable")
    required_from_claims = (
        ("customer_id", ["customer_id", "sub"]),
        ("agent", ["agent"]),
        ("schema_version", ["schema_version"]),
        ("batch_id", ["batch_id"]),
        ("batch_sha256", ["batch_sha256"]),
        ("event_count", ["event_count"]),
    )
    for display_name, aliases in required_from_claims:
        if not any(alias in envelope_claims for alias in aliases):
            raise ReceiverSchemaError(f"receiver payload missing required claim: {display_name}")

    return ReceiverEnvelope(
        payload=merged,
        events=list(merged["events"]),
        customer_id=str(merged["customer_id"]),
        agent_id=str(merged["agent"]),
        schema_version=str(merged["schema_version"]),
        batch_id=str(merged["batch_id"]),
        batch_sha256=str(merged["batch_sha256"]),
        event_count=claims_event_count,
        issued_at=_get_issued_at_field(merged),
        signed=signed,
    )
