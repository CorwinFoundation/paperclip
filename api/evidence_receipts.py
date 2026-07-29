"""BackBond-controlled ``evidence_receipt_v1`` issuance.

BEAAA-18819. This module is the receipt-service boundary, not scanner-side
signing code. The signer is deliberately an injected KMS/HSM facade: callers
can request a signature but cannot obtain private-key bytes.

Contract: contracts/evidence-receipt-v1.md@v1 (ratified 2026-07-29).
"""
from __future__ import annotations

import base64
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from api.jcs import canonicalize

VERSION = "evidence_receipt_v1"
SIGNATURE_ALG = "ed25519"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_OPAQUE = re.compile(r"[A-Za-z0-9_-]{16,256}\Z")
_SCAN_ID = re.compile(r"[A-Za-z0-9._:-]{1,256}\Z")
_KEY_ID = re.compile(r"[A-Za-z0-9._:/-]{1,256}\Z")
_DIGEST_FIELDS = (
    "evidence_bundle_sha256",
    "probe_manifest_sha256",
    "score_snapshot_sha256",
    "scanner_build_digest",
)


class ReceiptError(Exception):
    """Base class for stable receipt-service failures."""

    code = "receipt_error"
    status = 500


class InvalidSubmission(ReceiptError):
    code = "invalid_receipt_submission"
    status = 400


class UnauthorizedScanner(ReceiptError):
    code = "unauthorized_scanner_identity"
    status = 401


class ReceiptDigestConflict(ReceiptError):
    code = "receipt_digest_conflict"
    status = 409


class CrossTenantReceipt(ReceiptError):
    code = "cross_tenant_receipt_rejected"
    status = 409


class SigningUnavailable(ReceiptError):
    code = "receipt_signing_unavailable"
    status = 503


class RetentionWriteDenied(ReceiptError):
    code = "receipt_retention_write_denied"
    status = 503


@dataclass(frozen=True)
class ScannerIdentity:
    subject: str
    allowed_tenant_refs: frozenset[str]


@dataclass(frozen=True)
class KeyRecord:
    signing_key_id: str
    status: str
    public_key: bytes
    not_before: datetime
    not_after: datetime


class ReceiptSigner(Protocol):
    """Non-exportable KMS/HSM signing facade."""

    @property
    def active_key_id(self) -> str: ...

    def sign(self, message: bytes) -> bytes: ...


class ReceiptStore(Protocol):
    def get_by_scan_id(self, scan_id: str) -> bytes | None: ...

    def create_locked(
        self, *, scan_id: str, receipt_bytes: bytes, retain_until: datetime
    ) -> None: ...


class AuditSink(Protocol):
    def emit(self, event: Mapping[str, str]) -> None: ...


@dataclass(frozen=True)
class ReceiptSubmission:
    version: str
    scan_id: str
    tenant_ref: str
    evidence_bundle_sha256: str
    probe_manifest_sha256: str
    score_snapshot_sha256: str
    scanner_build_digest: str
    idempotency_nonce: str

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "ReceiptSubmission":
        expected = {
            "version",
            "scan_id",
            "tenant_ref",
            *_DIGEST_FIELDS,
            "idempotency_nonce",
        }
        if set(value) != expected:
            raise InvalidSubmission("submission fields do not match evidence_receipt_v1")
        if value.get("version") != VERSION:
            raise InvalidSubmission("unsupported receipt version")
        for name in expected:
            if not isinstance(value.get(name), str):
                raise InvalidSubmission(f"{name} must be a string")
        if not _SCAN_ID.fullmatch(value["scan_id"]):
            raise InvalidSubmission("scan_id is not an opaque reference")
        if not _OPAQUE.fullmatch(value["tenant_ref"]):
            raise InvalidSubmission("tenant_ref is not an opaque non-reversible reference")
        if not _OPAQUE.fullmatch(value["idempotency_nonce"]):
            raise InvalidSubmission("idempotency_nonce is invalid")
        for name in _DIGEST_FIELDS:
            if not _DIGEST.fullmatch(value[name]):
                raise InvalidSubmission(f"{name} must be sha256:<64 lowercase hex>")
        return cls(**{name: value[name] for name in cls.__dataclass_fields__})


def _add_ten_years(value: datetime) -> datetime:
    """Calendar-year retention: preserve month/day/time, clamp leap day."""
    try:
        return value.replace(year=value.year + 10)
    except ValueError:
        return value.replace(year=value.year + 10, day=28)


def signing_body(receipt: Mapping[str, Any]) -> bytes:
    expected = {
        "version",
        "receipt_id",
        "scan_id",
        "tenant_ref",
        *_DIGEST_FIELDS,
        "received_at",
        "signing_key_id",
        "signature_alg",
    }
    body = {key: value for key, value in receipt.items() if key != "signature"}
    if set(body) != expected:
        raise InvalidSubmission("receipt fields do not match evidence_receipt_v1")
    return canonicalize(body)


class ReceiptService:
    def __init__(
        self,
        *,
        signer: ReceiptSigner,
        store: ReceiptStore,
        audit: AuditSink,
        clock: Callable[[], datetime] | None = None,
        new_receipt_id: Callable[[], str] | None = None,
    ) -> None:
        self._signer = signer
        self._store = store
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._new_receipt_id = new_receipt_id or (lambda: str(uuid.uuid4()))

    def issue(
        self, raw: Mapping[str, Any], *, identity: ScannerIdentity
    ) -> bytes:
        submission = ReceiptSubmission.parse(raw)
        if submission.tenant_ref not in identity.allowed_tenant_refs:
            self._event("cross_tenant_rejection", submission, identity)
            raise UnauthorizedScanner("scanner identity is not authorized for tenant_ref")

        existing = self._store.get_by_scan_id(submission.scan_id)
        if existing is not None:
            import json

            original = json.loads(existing)
            if original["tenant_ref"] != submission.tenant_ref:
                self._event("cross_tenant_rejection", submission, identity)
                raise CrossTenantReceipt("scan_id is already bound to another tenant_ref")
            if any(original[name] != getattr(submission, name) for name in _DIGEST_FIELDS):
                self._event("digest_conflict", submission, identity)
                raise ReceiptDigestConflict("scan_id is already bound to different digests")
            self._event("identical_retry", submission, identity)
            return existing

        received_at = self._clock().astimezone(timezone.utc)
        key_id = self._signer.active_key_id
        if not _KEY_ID.fullmatch(key_id):
            self._event("signature_key_lookup_failure", submission, identity)
            raise SigningUnavailable("active signing key id is unavailable")
        receipt: dict[str, str] = {
            "version": VERSION,
            "receipt_id": self._new_receipt_id(),
            "scan_id": submission.scan_id,
            "tenant_ref": submission.tenant_ref,
            **{name: getattr(submission, name) for name in _DIGEST_FIELDS},
            "received_at": received_at.isoformat().replace("+00:00", "Z"),
            "signing_key_id": key_id,
            "signature_alg": SIGNATURE_ALG,
        }
        try:
            receipt["signature"] = base64.b64encode(
                self._signer.sign(signing_body(receipt))
            ).decode("ascii")
        except Exception as exc:
            self._event("signature_key_lookup_failure", submission, identity)
            raise SigningUnavailable("receipt signing failed") from exc
        receipt_bytes = canonicalize(receipt)
        try:
            self._store.create_locked(
                scan_id=submission.scan_id,
                receipt_bytes=receipt_bytes,
                retain_until=_add_ten_years(received_at),
            )
        except Exception as exc:
            # Another worker may have won the conditional create. Re-read the
            # immutable winner so a concurrent identical retry remains
            # byte-for-byte idempotent instead of leaking the losing signature.
            winner = self._store.get_by_scan_id(submission.scan_id)
            if winner is not None:
                import json

                original = json.loads(winner)
                if (
                    original.get("tenant_ref") == submission.tenant_ref
                    and all(
                        original.get(name) == getattr(submission, name)
                        for name in _DIGEST_FIELDS
                    )
                ):
                    self._event("identical_retry", submission, identity)
                    return winner
            self._event("denied_overwrite", submission, identity)
            raise RetentionWriteDenied("retention-locked receipt write failed") from exc
        self._event("create", submission, identity)
        return receipt_bytes

    def _event(
        self, kind: str, submission: ReceiptSubmission, identity: ScannerIdentity
    ) -> None:
        # Deliberately excludes digests, tenant identity, customer names and raw evidence.
        self._audit.emit(
            {
                "event": kind,
                "scan_id": submission.scan_id,
                "scanner_subject": identity.subject,
                "status": "recorded",
            }
        )


def verify_receipt(
    receipt_bytes: bytes,
    *,
    key_directory: Mapping[str, KeyRecord],
    now: datetime,
) -> bool:
    """Verify a receipt. Retired keys verify; revoked/unknown keys fail closed."""
    import json

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        receipt = json.loads(receipt_bytes)
        if canonicalize(receipt) != receipt_bytes:
            return False
        key = key_directory[receipt["signing_key_id"]]
        if key.status not in {"active", "retired"}:
            return False
        if not (key.not_before <= now <= key.not_after):
            return False
        signature = base64.b64decode(receipt["signature"], validate=True)
        Ed25519PublicKey.from_public_bytes(key.public_key).verify(
            signature, signing_body(receipt)
        )
        return True
    except Exception:
        return False


class S3ObjectLockReceiptStore:
    """S3-compatible object-lock adapter for the receipt-writer IAM identity.

    Bucket versioning and Object Lock must be enabled at provisioning time.
    ``IfNoneMatch='*'`` rejects a second write to the logical receipt key;
    COMPLIANCE retention is applied in the same PutObject request.
    """

    def __init__(self, client: Any, bucket: str, prefix: str = "receipts/v1") -> None:
        self._client = client
        self._bucket = bucket
        self._prefix = prefix.rstrip("/")

    def _key(self, scan_id: str) -> str:
        return f"{self._prefix}/{scan_id}.json"

    def get_by_scan_id(self, scan_id: str) -> bytes | None:
        try:
            return self._client.get_object(
                Bucket=self._bucket, Key=self._key(scan_id)
            )["Body"].read()
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise

    def create_locked(
        self, *, scan_id: str, receipt_bytes: bytes, retain_until: datetime
    ) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=self._key(scan_id),
            Body=receipt_bytes,
            ContentType="application/json",
            IfNoneMatch="*",
            ObjectLockMode="COMPLIANCE",
            ObjectLockRetainUntilDate=retain_until,
        )

    def retention_metadata(self, scan_id: str) -> Mapping[str, Any]:
        """Return provider-native retention metadata for acceptance evidence."""
        return self._client.get_object_retention(
            Bucket=self._bucket, Key=self._key(scan_id)
        )


class ReceiptControlAudit:
    """PII-free control-plane audit events outside the issuance hot path."""

    _KEY_EVENTS = frozenset({"key_rotation", "key_retirement", "key_revocation"})

    def __init__(self, audit: AuditSink) -> None:
        self._audit = audit

    def key_lifecycle(self, event: str, signing_key_id: str) -> None:
        if event not in self._KEY_EVENTS or not _KEY_ID.fullmatch(signing_key_id):
            raise ValueError("invalid key lifecycle audit event")
        self._audit.emit(
            {
                "event": event,
                "signing_key_id": signing_key_id,
                "status": "recorded",
            }
        )

    def retention_extension(self, receipt_id: str, retain_until: datetime) -> None:
        self._audit.emit(
            {
                "event": "retention_or_legal_hold_extension",
                "receipt_id": receipt_id,
                "retain_until": retain_until.astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "status": "recorded",
            }
        )

    def denied_delete(self, receipt_id: str) -> None:
        self._audit.emit(
            {
                "event": "denied_delete",
                "receipt_id": receipt_id,
                "status": "denied",
            }
        )


__all__ = [
    "CrossTenantReceipt",
    "InvalidSubmission",
    "KeyRecord",
    "ReceiptDigestConflict",
    "ReceiptControlAudit",
    "ReceiptService",
    "ReceiptSubmission",
    "RetentionWriteDenied",
    "S3ObjectLockReceiptStore",
    "ScannerIdentity",
    "SigningUnavailable",
    "signing_body",
    "verify_receipt",
]
