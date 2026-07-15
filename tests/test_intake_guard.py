"""Tests for BEAAA-22 §3 hosted-eval-only intake guard."""
from __future__ import annotations

import unittest

from api.intake_guard import (
    HOSTED_RUNNER,
    EvalProvenanceVerifier,
    IntakeError,
    enforce_hosted_eval_only,
    sign_hosted_eval_entry,
)

TENANT_ID = "tenant-a"
SNAPSHOT_HASH = "sha256:" + "b" * 64
SIGNING_KEY = b"test-hosted-eval-secret"
NOW = 1_800_000_000
JTI = "22222222-2222-4222-8222-222222222222"


def _ok_entry(seed: str = "11111111-1111-1111-1111-111111111111") -> dict:
    return {
        "raw": 2.0,
        "evidence_uri": "https://e/x",
        "evidence_sha256": "sha256:" + "a" * 64,
        "runner": HOSTED_RUNNER,
        "eval_run_id": seed,
    }


def _signed_entry(
    *,
    dim_id: str = "P1.1",
    tenant_id: str = TENANT_ID,
    snapshot_hash: str = SNAPSHOT_HASH,
    entry: dict | None = None,
    issued_at: int = NOW,
    ttl_seconds: int = 300,
    jti: str = JTI,
) -> dict:
    return sign_hosted_eval_entry(
        tenant_id=tenant_id,
        snapshot_hash=snapshot_hash,
        dim_id=dim_id,
        entry=entry or _ok_entry(),
        signing_key=SIGNING_KEY,
        issued_at=issued_at,
        ttl_seconds=ttl_seconds,
        jti=jti,
    )


class IntakeGuardTests(unittest.TestCase):
    def test_accepts_hosted_runner_with_uuid(self):
        enforce_hosted_eval_only({"P1.1": _ok_entry()})

    def test_rejects_empty(self):
        with self.assertRaisesRegex(IntakeError, "non-empty"):
            enforce_hosted_eval_only({})

    def test_rejects_missing_runner(self):
        e = _ok_entry()
        e.pop("runner")
        with self.assertRaisesRegex(IntakeError, "hosted-eval"):
            enforce_hosted_eval_only({"P1.1": e})

    def test_rejects_self_reported_runner(self):
        e = _ok_entry()
        e["runner"] = "customer-self-reported"
        with self.assertRaisesRegex(IntakeError, "hosted-eval"):
            enforce_hosted_eval_only({"P1.1": e})

    def test_rejects_non_uuid_run_id(self):
        e = _ok_entry()
        e["eval_run_id"] = "not-a-uuid"
        with self.assertRaisesRegex(IntakeError, "UUID"):
            enforce_hosted_eval_only({"P1.1": e})

    def test_rejects_non_dict_entry(self):
        with self.assertRaisesRegex(IntakeError, "object"):
            enforce_hosted_eval_only({"P1.1": "raw=2.0"})


class HostedEvalProvenanceHMACTests(unittest.TestCase):
    def test_accepts_signature_binding_scored_content_and_context(self):
        entry = _signed_entry()
        enforce_hosted_eval_only(
            {"P1.1": entry},
            tenant_id=TENANT_ID,
            snapshot_hash=SNAPSHOT_HASH,
            provenance_verifier=EvalProvenanceVerifier(SIGNING_KEY),
            now=NOW + 1,
        )

    def test_rejects_mutation_of_every_bound_field(self):
        cases = [
            ("tenant_id", None, {"tenant_id": "tenant-b"}),
            ("snapshot_hash", None, {"snapshot_hash": "sha256:" + "c" * 64}),
            ("dim_id", {"dim_id": "P1.2"}, {}),
            ("runner", {"runner": "customer-self-reported"}, {}),
            (
                "eval_run_id",
                {"eval_run_id": "33333333-3333-4333-8333-333333333333"},
                {},
            ),
            ("raw", {"raw": 3.0}, {}),
            ("evidence_uri", {"evidence_uri": "https://e/other"}, {}),
            ("evidence_sha256", {"evidence_sha256": "sha256:" + "d" * 64}, {}),
            ("provenance_issued_at", {"provenance_issued_at": NOW - 1}, {}),
            ("provenance_expires_at", {"provenance_expires_at": NOW + 299}, {}),
            (
                "provenance_jti",
                {"provenance_jti": "44444444-4444-4444-8444-444444444444"},
                {},
            ),
        ]
        for field_name, entry_patch, context_patch in cases:
            with self.subTest(field=field_name):
                dim_id = "P1.1"
                signed = _signed_entry(dim_id=dim_id)
                if entry_patch and "dim_id" in entry_patch:
                    dim_id = entry_patch["dim_id"]
                elif entry_patch:
                    signed.update(entry_patch)
                with self.assertRaises(IntakeError):
                    enforce_hosted_eval_only(
                        {dim_id: signed},
                        tenant_id=context_patch.get("tenant_id", TENANT_ID),
                        snapshot_hash=context_patch.get("snapshot_hash", SNAPSHOT_HASH),
                        provenance_verifier=EvalProvenanceVerifier(SIGNING_KEY),
                        now=NOW + 1,
                    )

    def test_rejects_cross_tenant_transplant_of_valid_tag(self):
        entry = _signed_entry(tenant_id=TENANT_ID)
        with self.assertRaisesRegex(IntakeError, "does not match"):
            enforce_hosted_eval_only(
                {"P1.1": entry},
                tenant_id="tenant-b",
                snapshot_hash=SNAPSHOT_HASH,
                provenance_verifier=EvalProvenanceVerifier(SIGNING_KEY),
                now=NOW + 1,
            )

    def test_rejects_expired_signature(self):
        entry = _signed_entry(issued_at=NOW, ttl_seconds=10)
        with self.assertRaisesRegex(IntakeError, "expired"):
            enforce_hosted_eval_only(
                {"P1.1": entry},
                tenant_id=TENANT_ID,
                snapshot_hash=SNAPSHOT_HASH,
                provenance_verifier=EvalProvenanceVerifier(SIGNING_KEY),
                now=NOW + 11,
            )

    def test_rejects_second_use_of_consumed_jti(self):
        verifier = EvalProvenanceVerifier(SIGNING_KEY)
        entry = _signed_entry()
        enforce_hosted_eval_only(
            {"P1.1": entry},
            tenant_id=TENANT_ID,
            snapshot_hash=SNAPSHOT_HASH,
            provenance_verifier=verifier,
            now=NOW + 1,
        )
        with self.assertRaisesRegex(IntakeError, "already consumed"):
            enforce_hosted_eval_only(
                {"P1.1": entry},
                tenant_id=TENANT_ID,
                snapshot_hash=SNAPSHOT_HASH,
                provenance_verifier=verifier,
                now=NOW + 1,
            )


if __name__ == "__main__":
    unittest.main()
