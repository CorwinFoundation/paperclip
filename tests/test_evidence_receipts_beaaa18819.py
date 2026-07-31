from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from api.evidence_receipts import (
    CrossTenantReceipt,
    KeyRecord,
    ReceiptDigestConflict,
    ReceiptControlAudit,
    ReceiptService,
    S3ObjectLockReceiptStore,
    ScannerIdentity,
    verify_receipt,
)

NOW = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
TENANT = "opaque_tenant_ref_123456789"


class KmsFacade:
    active_key_id = "receipt-key-2026-07"

    def __init__(self) -> None:
        self._key = Ed25519PrivateKey.generate()

    def sign(self, message: bytes) -> bytes:
        return self._key.sign(message)

    def public_bytes(self) -> bytes:
        return self._key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )


class Store:
    def __init__(self) -> None:
        self.rows: dict[str, bytes] = {}
        self.retention: dict[str, datetime] = {}

    def get_by_scan_id(self, scan_id: str) -> bytes | None:
        return self.rows.get(scan_id)

    def create_locked(self, *, scan_id, receipt_bytes, retain_until) -> None:
        if scan_id in self.rows:
            raise PermissionError("overwrite denied")
        self.rows[scan_id] = receipt_bytes
        self.retention[scan_id] = retain_until

    def delete_as_writer(self, scan_id: str) -> None:
        raise PermissionError("writer has no delete/bypass authority")


class Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, str]] = []

    def emit(self, event) -> None:
        self.events.append(dict(event))


def submission(scan_id="scan-1", tenant=TENANT, digit="a"):
    digest = "sha256:" + digit * 64
    return {
        "version": "evidence_receipt_v1",
        "scan_id": scan_id,
        "tenant_ref": tenant,
        "evidence_bundle_sha256": digest,
        "probe_manifest_sha256": digest,
        "score_snapshot_sha256": digest,
        "scanner_build_digest": digest,
        "idempotency_nonce": "nonce_1234567890123456",
    }


def service():
    signer, store, audit = KmsFacade(), Store(), Audit()
    instance = ReceiptService(
        signer=signer,
        store=store,
        audit=audit,
        clock=lambda: NOW,
        new_receipt_id=lambda: "12345678-1234-4234-8234-123456789012",
    )
    identity = ScannerIdentity("scanner-runtime-1", frozenset({TENANT}))
    return instance, signer, store, audit, identity


def directory(signer, status="active", key_id=None):
    key_id = key_id or signer.active_key_id
    return {
        key_id: KeyRecord(
            key_id,
            status,
            signer.public_bytes(),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2037, 1, 1, tzinfo=timezone.utc),
        )
    }


def test_issuance_is_canonical_signed_and_retained_at_write_time():
    svc, signer, store, audit, identity = service()
    receipt = svc.issue(submission(), identity=identity)
    body = json.loads(receipt)

    assert receipt == svc.issue(submission(), identity=identity)
    assert body["received_at"] == "2026-07-29T08:00:00Z"
    assert store.retention["scan-1"] == datetime(2036, 7, 29, 8, 0, tzinfo=timezone.utc)
    assert verify_receipt(receipt, key_directory=directory(signer), now=NOW)
    assert [event["event"] for event in audit.events] == ["create", "identical_retry"]
    with pytest.raises(PermissionError):
        store.delete_as_writer("scan-1")


def test_conflict_and_cross_tenant_substitution_fail_closed_and_are_audited():
    svc, _, store, audit, identity = service()
    original = svc.issue(submission(), identity=identity)
    with pytest.raises(ReceiptDigestConflict):
        svc.issue(submission(digit="b"), identity=identity)

    store.rows["scan-2"] = original.replace(b"opaque_tenant_ref_123456789", b"opaque_other_tenant_1234567")
    with pytest.raises(CrossTenantReceipt):
        svc.issue(submission(scan_id="scan-2"), identity=identity)
    assert [event["event"] for event in audit.events][-2:] == [
        "digest_conflict",
        "cross_tenant_rejection",
    ]


def test_replay_tamper_unknown_revoked_and_retired_key_behaviors():
    svc, signer, _, _, identity = service()
    receipt = svc.issue(submission(), identity=identity)
    tampered = receipt.replace(b'"scan-1"', b'"scan-2"')

    assert not verify_receipt(tampered, key_directory=directory(signer), now=NOW)
    assert not verify_receipt(receipt, key_directory={}, now=NOW)
    assert not verify_receipt(
        receipt, key_directory=directory(signer, "revoked"), now=NOW
    )
    assert verify_receipt(
        receipt, key_directory=directory(signer, "retired"), now=NOW
    )


def test_s3_adapter_requests_compliance_lock_and_conditional_create():
    class Client:
        def __init__(self):
            self.kwargs = None

        def put_object(self, **kwargs):
            self.kwargs = kwargs

    client = Client()
    store = S3ObjectLockReceiptStore(client, "backbond-receipts")
    retain_until = datetime(2036, 7, 29, tzinfo=timezone.utc)
    store.create_locked(
        scan_id="scan-1", receipt_bytes=b"{}", retain_until=retain_until
    )
    assert client.kwargs["IfNoneMatch"] == "*"
    assert client.kwargs["ObjectLockMode"] == "COMPLIANCE"
    assert client.kwargs["ObjectLockRetainUntilDate"] == retain_until
    assert "Delete" not in " ".join(client.kwargs)


def test_control_events_cover_key_lifecycle_retention_and_denied_delete():
    audit = Audit()
    controls = ReceiptControlAudit(audit)
    controls.key_lifecycle("key_rotation", "receipt-key-2027-01")
    controls.key_lifecycle("key_retirement", "receipt-key-2026-07")
    controls.key_lifecycle("key_revocation", "receipt-key-compromised")
    controls.retention_extension(
        "12345678-1234-4234-8234-123456789012",
        datetime(2038, 1, 1, tzinfo=timezone.utc),
    )
    controls.denied_delete("12345678-1234-4234-8234-123456789012")
    assert [event["event"] for event in audit.events] == [
        "key_rotation",
        "key_retirement",
        "key_revocation",
        "retention_or_legal_hold_extension",
        "denied_delete",
    ]


def test_writer_policy_explicitly_denies_delete_and_bypass():
    policy = json.loads(
        open("infra/evidence-receipts/receipt-writer-policy.json", encoding="utf-8").read()
    )
    denied = next(
        statement for statement in policy["Statement"] if statement["Effect"] == "Deny"
    )
    assert set(denied["Action"]) == {
        "s3:DeleteObject",
        "s3:DeleteObjectVersion",
        "s3:BypassGovernanceRetention",
    }
