"""Tests for the v0.1 forensic bundle signing stub (Task B)."""
from __future__ import annotations

import unittest

from api.bundle import BundleError, BundleStore, derive_bundle_id, verify


class BundleStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = BundleStore()
        self.bundle_id = derive_bundle_id(
            snapshot_hash="sha256:" + "a" * 64,
            tenant_id="11111111-1111-1111-1111-111111111111",
            scored_at="2026-04-22T00:00:00+00:00",
        )

    def test_build_round_trip(self):
        b = self.store.build(
            bundle_id=self.bundle_id,
            snapshot_hash="sha256:abc",
            evidence=[{"dim_id": "P1.1", "uri": "https://e/x", "sha256": ""}],
        )
        self.assertEqual(b["bundle_id"], self.bundle_id)
        self.assertTrue(b["signed_manifest"].startswith("hmac-sha256:"))
        got = self.store.get(self.bundle_id)
        self.assertEqual(got, b)

    def test_get_unknown_returns_none(self):
        self.assertIsNone(self.store.get("00000000-0000-0000-0000-000000000000"))

    def test_verify_detects_tampering(self):
        b = self.store.build(
            bundle_id=self.bundle_id,
            snapshot_hash="sha256:abc",
            evidence=[{"dim_id": "P1.1", "uri": "https://e/x", "sha256": ""}],
        )
        b["evidence"][0]["uri"] = "https://e/tampered"
        with self.assertRaisesRegex(BundleError, "signature mismatch"):
            verify(b)

    def test_verify_detects_missing_signature(self):
        with self.assertRaisesRegex(BundleError, "missing signed_manifest"):
            verify({"bundle_id": "x", "snapshot_hash": "x", "evidence": []})

    def test_derive_bundle_id_is_deterministic(self):
        a = derive_bundle_id(
            snapshot_hash="sha256:abc", tenant_id="t1", scored_at="2026-01-01T00:00:00+00:00"
        )
        b = derive_bundle_id(
            snapshot_hash="sha256:abc", tenant_id="t1", scored_at="2026-01-01T00:00:00+00:00"
        )
        self.assertEqual(a, b)

    def test_derive_bundle_id_changes_with_inputs(self):
        a = derive_bundle_id(
            snapshot_hash="sha256:abc", tenant_id="t1", scored_at="2026-01-01T00:00:00+00:00"
        )
        b = derive_bundle_id(
            snapshot_hash="sha256:abc", tenant_id="t2", scored_at="2026-01-01T00:00:00+00:00"
        )
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
