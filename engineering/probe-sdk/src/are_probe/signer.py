"""Portal CA signature stub — Ed25519 over canonical-JSON envelope bytes.

Mirrors the BEAAA-4900 probe-ledger discipline: same cto-attest-v1 key
identity (sha256 fp 3ec6c4579ec62902b07ca720295da0bdc1727729acb1899c7145e4b167a22e29).
R1 uses this as the portal CA stub; R2 will replace with DP-signed-then-
portal-counter-signed per DISTRIBUTION_READINESS_CHECKLIST.md.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .canonical import canonical_json


@dataclass(frozen=True)
class Signature:
    key_id: str
    algorithm: str
    public_key_b64url: str
    public_key_sha256_fingerprint: str
    signature_b64url: str
    payload_sha256: str


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def load_signer(key_path: str) -> tuple[Ed25519PrivateKey, dict]:
    p = Path(key_path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"signing key not found at {p}")
    payload = json.loads(p.read_text())
    priv_b = base64.b64decode(payload["private_key_b64"])
    sk = Ed25519PrivateKey.from_private_bytes(priv_b)
    return sk, payload


def sign_envelope(envelope: dict, key_path: str) -> Signature:
    sk, key_payload = load_signer(key_path)
    canonical = canonical_json(envelope)
    payload_sha256 = hashlib.sha256(canonical).hexdigest()
    # Sign over the sha256 hex STRING bytes — matches BEAAA-4700 procedure
    # used by the probe ledger and methodology counter-sign, so envelopes
    # verify with the same recipe auditors already have.
    sig_bytes = sk.sign(payload_sha256.encode("ascii"))
    pub_b = base64.b64decode(key_payload["public_key_b64"])
    return Signature(
        key_id=key_payload["key_id"],
        algorithm=key_payload["algorithm"],
        public_key_b64url=_b64url(pub_b),
        public_key_sha256_fingerprint=key_payload["public_key_sha256_fingerprint"],
        signature_b64url=_b64url(sig_bytes),
        payload_sha256=payload_sha256,
    )
