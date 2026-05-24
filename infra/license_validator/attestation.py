"""Ed25519-signed attestation tokens (BEAAA-569, ADR-0011 §3.4 + §3.5).

Compact token shape, no third-party JWT dependency:

    <base64url(payload_json)>.<base64url(signature)>

The public key is embedded in the binary at build time (BEAAA-567 §3.7
pinning gate). The binary verifies the attestation in-process before the
scoring kernel runs. TTL <=24h per ADR-0011 §3.4; the in-binary verifier
re-checks ``expires_at`` so a re-validated key takes effect inside the
attestation TTL (in-field revocation timer per ADR-0011 §10).
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

MAX_TTL = timedelta(hours=24)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def key_id(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()[:16]


def generate_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()


def load_private_key(path: Path) -> Ed25519PrivateKey:
    data = Path(path).read_bytes()
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("expected an Ed25519 private key in PEM form")
    return key


def load_public_key(path: Path) -> Ed25519PublicKey:
    data = Path(path).read_bytes()
    key = serialization.load_pem_public_key(data)
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError("expected an Ed25519 public key in PEM form")
    return key


def write_private_key(key: Ed25519PrivateKey, path: Path) -> None:
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    Path(path).write_bytes(pem)
    Path(path).chmod(0o600)


def write_public_key(key: Ed25519PublicKey, path: Path) -> None:
    pem = key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    Path(path).write_bytes(pem)


@dataclass(frozen=True)
class Attestation:
    """Decoded attestation payload after signature + TTL checks."""

    partner_id: str
    binary_build_id: str
    scan_id: str
    issued_at: datetime
    expires_at: datetime
    key_id: str
    token: str


def sign_attestation(
    *,
    private_key: Ed25519PrivateKey,
    partner_id: str,
    binary_build_id: str,
    scan_id: str,
    ttl: timedelta = MAX_TTL,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Return ``(token, expires_at)``.

    ``ttl`` is clamped to :data:`MAX_TTL` (24h per ADR-0011 §3.4).
    """
    if ttl > MAX_TTL:
        ttl = MAX_TTL
    if ttl <= timedelta(0):
        raise ValueError("ttl must be positive")
    issued_at = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0)
    expires_at = issued_at + ttl
    payload = {
        "partner_id": partner_id,
        "binary_build_id": binary_build_id,
        "scan_id": scan_id,
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "key_id": key_id(private_key.public_key()),
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(payload_bytes)
    return f"{_b64url(payload_bytes)}.{_b64url(signature)}", expires_at


def verify_attestation(
    *,
    token: str,
    public_key: Ed25519PublicKey,
    expected_partner_id: str,
    expected_binary_build_id: str,
    now: datetime | None = None,
) -> Attestation:
    """Verify signature, key-id match, scope, and TTL.

    Raises :class:`ValueError` (with a message safe to log but not exposed
    to remote callers) on any failure. Callers that need to distinguish
    signature failure from expiry should inspect the message; the binary
    (BEAAA-567) does not distinguish — both are fail-closed.
    """
    try:
        payload_b64, sig_b64 = token.split(".", 1)
    except ValueError as exc:  # malformed token, no dot
        raise ValueError("attestation: malformed token") from exc
    try:
        payload_bytes = _b64url_decode(payload_b64)
        signature = _b64url_decode(sig_b64)
    except Exception as exc:  # noqa: BLE001 — base64 errors
        raise ValueError("attestation: base64 decode failed") from exc
    try:
        public_key.verify(signature, payload_bytes)
    except InvalidSignature as exc:
        raise ValueError("attestation: bad signature") from exc
    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError("attestation: malformed payload") from exc
    required = ("partner_id", "binary_build_id", "scan_id", "issued_at", "expires_at", "key_id")
    if not all(k in payload for k in required):
        raise ValueError("attestation: missing required field")
    if payload["partner_id"] != expected_partner_id:
        raise ValueError("attestation: partner_id mismatch")
    if payload["binary_build_id"] != expected_binary_build_id:
        raise ValueError("attestation: binary_build_id mismatch")
    if payload["key_id"] != key_id(public_key):
        raise ValueError("attestation: key_id mismatch")
    expires_at = _parse_iso(payload["expires_at"])
    issued_at = _parse_iso(payload["issued_at"])
    moment = (now or datetime.now(tz=timezone.utc))
    if moment >= expires_at:
        raise ValueError("attestation: expired")
    if moment < issued_at - timedelta(minutes=5):
        raise ValueError("attestation: issued in the future (clock skew)")
    return Attestation(
        partner_id=payload["partner_id"],
        binary_build_id=payload["binary_build_id"],
        scan_id=payload["scan_id"],
        issued_at=issued_at,
        expires_at=expires_at,
        key_id=payload["key_id"],
        token=token,
    )


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)
