"""Reference client for the PyInstaller binary (BEAAA-567).

BEAAA-567 §4 specifies:

* Every invocation makes a TLS callback to the IFA license endpoint.
* The binary embeds the public key at build time and verifies the
  attestation in-process.
* The binary fails closed on validation failure, network unreachable,
  endpoint timeout, signature invalid, or attestation expired.

This client honors all of the above. ``fetch_attestation`` returns a
verified :class:`~infra.license_validator.attestation.Attestation` on
success and raises :class:`LicenseValidationError` on every other path.
The binary's wrapper does not distinguish between failure modes — every
non-``valid`` outcome is the same fail-closed exit.

The HTTP transport uses ``httpx`` (already pinned for the project, see
``requirements.txt``); the client takes a pre-built httpx.Client so a
caller can configure ``verify=...`` (TLS root bundle), proxies, etc.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .attestation import Attestation, load_public_key, verify_attestation

DEFAULT_TIMEOUT_SEC = 10.0


class LicenseValidationError(Exception):
    """Raised on any non-``valid`` outcome.

    Wraps a short reason string. The binary fails closed on this
    exception; do not catch it to retry — there is no offline grace
    path per ADR-0011 §3.4.
    """


@dataclass(frozen=True)
class LicenseClientConfig:
    endpoint_url: str
    public_key: Ed25519PublicKey
    partner_id: str
    binary_build_id: str
    license_key: str
    timeout_sec: float = DEFAULT_TIMEOUT_SEC

    @classmethod
    def from_files(
        cls,
        *,
        endpoint_url: str,
        public_key_path: Path,
        partner_id: str,
        binary_build_id: str,
        license_key: str,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> "LicenseClientConfig":
        return cls(
            endpoint_url=endpoint_url,
            public_key=load_public_key(public_key_path),
            partner_id=partner_id,
            binary_build_id=binary_build_id,
            license_key=license_key,
            timeout_sec=timeout_sec,
        )


def fetch_attestation(
    *,
    config: LicenseClientConfig,
    scan_id: str,
    http_client: httpx.Client | None = None,
) -> Attestation:
    """Call the license endpoint and verify the returned attestation.

    Fails closed on:

    * Network error (DNS, TCP, TLS, timeout).
    * Non-2xx response.
    * Outcome != ``valid``.
    * Attestation signature / scope / TTL mismatch.
    """
    payload = {
        "partner_id": config.partner_id,
        "binary_build_id": config.binary_build_id,
        "scan_id": scan_id,
        "timestamp": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "license_key": config.license_key,
    }
    own_client = http_client is None
    client = http_client or httpx.Client(timeout=config.timeout_sec)
    try:
        try:
            # Only pass per-request timeout when we own the client; injected
            # clients (e.g. ``TestClient``) configure their own timeout and
            # warn if it is overridden per-call.
            if own_client:
                resp = client.post(
                    _validate_url(config.endpoint_url),
                    json=payload,
                    timeout=config.timeout_sec,
                )
            else:
                resp = client.post(_validate_url(config.endpoint_url), json=payload)
        except httpx.HTTPError as exc:
            raise LicenseValidationError(f"license endpoint unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code != 200:
            raise LicenseValidationError(f"license endpoint returned {resp.status_code}")
        body = resp.json()
    finally:
        if own_client:
            client.close()

    outcome = body.get("outcome")
    if outcome != "valid":
        raise LicenseValidationError(f"outcome={outcome!r}")
    token = body.get("attestation")
    if not token:
        raise LicenseValidationError("missing attestation on valid outcome")

    try:
        return verify_attestation(
            token=token,
            public_key=config.public_key,
            expected_partner_id=config.partner_id,
            expected_binary_build_id=config.binary_build_id,
        )
    except ValueError as exc:
        raise LicenseValidationError(str(exc)) from exc


def _validate_url(base: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/license/validate"):
        return base
    return f"{base}/license/validate"
