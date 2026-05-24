"""Transport-agnostic license-validation service (BEAAA-569 §3 + §4).

This module is the pure validation kernel. :mod:`app` wraps it as a FastAPI
HTTP surface; tests target this module directly so the validation logic is
exercised without a test-client round trip.

Outcomes (exhaustive per ADR-0011 §3.4 + BEAAA-569 description §4):

* ``valid``                — key OK, partner matches, build not blacklisted,
                             not expired, not revoked. Returns an attestation
                             with TTL <=24h.
* ``revoked``              — key explicitly revoked via :mod:`registry`.
* ``expired``              — key past its ``expires_at``.
* ``partner_mismatch``     — request ``partner_id`` differs from the partner
                             the key was issued to, OR the request
                             ``binary_build_id`` differs from the build the
                             key was issued for.
* ``binary_blacklisted``   — request ``binary_build_id`` is on the
                             blacklist (orthogonal to the key).
* ``unknown_key``          — license_key not in the registry. Indistinguishable
                             from ``revoked`` in the wire response so an
                             attacker cannot enumerate keys; logged separately
                             for audit.

The binary fails closed on any non-``valid`` outcome (BEAAA-567 §4).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .attestation import MAX_TTL, sign_attestation
from .audit import AuditEntry, AuditLog
from .registry import Registry

# Outcomes returned to the binary. ``unknown_key`` is collapsed to
# ``revoked`` on the wire to avoid key enumeration; it remains a first-class
# label inside the audit log.
_WIRE_ALIAS: Final[dict[str, str]] = {"unknown_key": "revoked"}


class ValidationOutcome(str, Enum):
    VALID = "valid"
    REVOKED = "revoked"
    EXPIRED = "expired"
    PARTNER_MISMATCH = "partner_mismatch"
    BINARY_BLACKLISTED = "binary_blacklisted"
    UNKNOWN_KEY = "unknown_key"  # audit-only label, collapsed on the wire


@dataclass(frozen=True)
class LicenseValidationRequest:
    partner_id: str
    binary_build_id: str
    scan_id: str
    timestamp: str  # ISO-8601 from the caller; opaque to the service
    license_key: str


@dataclass(frozen=True)
class LicenseValidationResponse:
    outcome: str  # "valid" | "revoked" | "expired" | "partner_mismatch" | "binary_blacklisted"
    attestation: str | None
    expires_at: str | None  # ISO-8601 if attestation is non-null


def _utc_now(now: datetime | None) -> datetime:
    return (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate(
    request: LicenseValidationRequest,
    *,
    registry: Registry,
    private_key: Ed25519PrivateKey,
    audit_log: AuditLog,
    source_ip: str,
    now: datetime | None = None,
    ttl: timedelta = MAX_TTL,
) -> LicenseValidationResponse:
    """Run the validation pipeline and emit an audit entry.

    Side effects: appends one line to ``audit_log``. Does not mutate
    ``registry``.
    """
    moment = _utc_now(now)
    record = registry.get(request.license_key)

    if record is None:
        outcome = ValidationOutcome.UNKNOWN_KEY
    elif registry.is_build_blacklisted(request.binary_build_id):
        outcome = ValidationOutcome.BINARY_BLACKLISTED
    elif record.status == "revoked":
        outcome = ValidationOutcome.REVOKED
    elif (
        record.partner_id != request.partner_id
        or record.binary_build_id != request.binary_build_id
    ):
        outcome = ValidationOutcome.PARTNER_MISMATCH
    elif moment >= record.expires_at:
        outcome = ValidationOutcome.EXPIRED
    else:
        outcome = ValidationOutcome.VALID

    key_fp = _key_fingerprint(private_key)
    audit_log.record(
        AuditEntry(
            timestamp=moment,
            partner_id=request.partner_id,
            binary_build_id=request.binary_build_id,
            scan_id=request.scan_id,
            source_ip=source_ip,
            outcome=outcome.value,
            key_id=key_fp,
        )
    )

    if outcome != ValidationOutcome.VALID:
        wire_outcome = _WIRE_ALIAS.get(outcome.value, outcome.value)
        return LicenseValidationResponse(outcome=wire_outcome, attestation=None, expires_at=None)

    token, expires_at = sign_attestation(
        private_key=private_key,
        partner_id=request.partner_id,
        binary_build_id=request.binary_build_id,
        scan_id=request.scan_id,
        ttl=ttl,
        now=moment,
    )
    return LicenseValidationResponse(
        outcome=ValidationOutcome.VALID.value,
        attestation=token,
        expires_at=_iso(expires_at),
    )


def _key_fingerprint(private_key: Ed25519PrivateKey) -> str:
    from .attestation import key_id

    return key_id(private_key.public_key())
