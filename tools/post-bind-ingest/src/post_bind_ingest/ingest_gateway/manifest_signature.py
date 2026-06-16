"""HMAC-SHA256 manifest envelope signing.

MVP per CTO brief §4: per-customer fixture-only HMAC key under
`tests/fixtures/signing_keys/{customer_id}.key`. Production KMS / IAM is
Phase 2.0.1. The signature is computed over the *canonical-JSON* body of
the manifest (`policy_body`-style, sorted keys, no whitespace) so the wire
form is independent of producer whitespace habits.

Failure mode (Schema §Ingest gateway): manifest signature failure ->
gateway rejects, records to dead-letter, does NOT enqueue objects for
normalization. Per ADR-0034 §2.1 the customer is alerted out-of-band.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from ..normalizer.transcript_sha import canonical_json


SUPPORTED_SIGNATURE_ALG = "hmac-sha256"


class ManifestSignatureError(ValueError):
    """Raised on signature mismatch or unsupported signature_alg."""


def _signing_bytes(body: dict[str, Any]) -> bytes:
    return canonical_json(body).encode("utf-8")


def sign_manifest_body(body: dict[str, Any], key_bytes: bytes) -> str:
    """Produce a hex-encoded HMAC-SHA256 over the canonical-JSON body.

    Test-side helper. Production signing is performed inside the customer
    environment by the connector; the gateway only verifies.
    """
    mac = hmac.new(key_bytes, _signing_bytes(body), hashlib.sha256)
    return mac.hexdigest()


def verify_manifest_signature(
    body: dict[str, Any],
    signature_hex: str,
    signature_alg: str,
    key_bytes: bytes,
) -> None:
    """Verify the signature on a manifest body. Raises on mismatch."""
    if signature_alg != SUPPORTED_SIGNATURE_ALG:
        raise ManifestSignatureError(
            f"unsupported signature_alg {signature_alg!r}; expected {SUPPORTED_SIGNATURE_ALG!r}"
        )
    expected = sign_manifest_body(body, key_bytes)
    if not hmac.compare_digest(expected, signature_hex):
        raise ManifestSignatureError("manifest signature mismatch")
