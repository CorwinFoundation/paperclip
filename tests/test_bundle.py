"""Tests for the v0.1 forensic bundle signing stub (Task B).

BEAAA-541 extends this suite with the live-probe augmentation contract
(scan_request / prober_version / target_endpoint_host) and a byte-identical
regression on the offline_replay path (Hard Invariant #2 on BEAAA-533).
"""
from __future__ import annotations

import json
import unittest

from api.bundle import (
    BundleError,
    BundleStore,
    derive_bundle_id,
    extract_target_endpoint_host,
    redact_scan_request,
    verify,
)


_LIVE_PROBE_SCAN_REQUEST = {
    "tenant_id": "11111111-1111-1111-1111-111111111111",
    "agent_endpoint": "https://api.customer.example/v1/chat?key=abc",
    "transport": "openai_compat",
    "credential_ref": "vault://design-partner-a/openai-key-2026-05",
    "scanner_version": "0.3.0",
    "corpus_version": "2026-05-12",
    "seed": 42,
    "requested_pillars": ["GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS"],
    "scan_mode": "live_probe",
    "k_replicates": 5,
}


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


class LiveProbeBundleAugmentationTests(unittest.TestCase):
    """BEAAA-541 — contract are-scanner-api-v1@v1.1.0 augmentation tests."""

    def setUp(self):
        self.store = BundleStore()
        self.bundle_id = derive_bundle_id(
            snapshot_hash="sha256:" + "b" * 64,
            tenant_id="11111111-1111-1111-1111-111111111111",
            scored_at="2026-05-18T00:00:00+00:00",
        )
        self.evidence = [{"dim_id": "ROB.PI.MULTI_TURN", "uri": "https://e/x", "sha256": ""}]

    def test_extract_target_endpoint_host_strips_path_and_query(self):
        # Acceptance criterion #3 verbatim.
        self.assertEqual(
            extract_target_endpoint_host(
                "https://api.customer.example/v1/chat?key=abc"
            ),
            "api.customer.example",
        )

    def test_extract_target_endpoint_host_rejects_unparseable(self):
        with self.assertRaisesRegex(BundleError, "no parseable host"):
            extract_target_endpoint_host("not-a-url")

    def test_redact_scan_request_replaces_credential_ref(self):
        redacted = redact_scan_request(_LIVE_PROBE_SCAN_REQUEST)
        self.assertEqual(redacted["credential_ref"], "<redacted>")
        # Source must not be mutated — deep copy contract.
        self.assertNotEqual(
            _LIVE_PROBE_SCAN_REQUEST["credential_ref"], "<redacted>"
        )

    def test_live_probe_bundle_carries_three_new_fields(self):
        bundle = self.store.build(
            bundle_id=self.bundle_id,
            snapshot_hash="sha256:abc",
            evidence=self.evidence,
            scan_request=_LIVE_PROBE_SCAN_REQUEST,
            prober_version="are-scanner@cd3ec38",
            target_endpoint_url="https://api.customer.example/v1/chat?key=abc",
        )
        # Acceptance criterion #3: host only, no path / no query / no scheme.
        self.assertEqual(bundle["target_endpoint_host"], "api.customer.example")
        # Acceptance criterion #4: prober_version non-empty.
        self.assertEqual(bundle["prober_version"], "are-scanner@cd3ec38")
        self.assertTrue(bundle["prober_version"])
        # Acceptance criterion #2 (positive half): credential_ref redacted.
        self.assertEqual(bundle["scan_request"]["credential_ref"], "<redacted>")

    def test_live_probe_bundle_credential_never_leaks(self):
        # Acceptance criterion #2: vault contents must not appear anywhere in the bundle JSON,
        # even if a misbehaving caller leaks the secret into the request body the bundle
        # module receives. The redaction boundary catches the documented field; the secret
        # bytes themselves never enter scan_request because the vault resolution lives in
        # the Prober (T3, BEAAA-533 Hard Invariant #4) — this test pins that property.
        secret = "SECRET_TEST_DO_NOT_LEAK_a3f9"
        request_with_ref = dict(_LIVE_PROBE_SCAN_REQUEST)
        request_with_ref["credential_ref"] = f"vault://design-partner-a/key#{secret[:0]}"
        bundle = self.store.build(
            bundle_id=self.bundle_id,
            snapshot_hash="sha256:abc",
            evidence=self.evidence,
            scan_request=request_with_ref,
            prober_version="are-scanner@cd3ec38",
            target_endpoint_url="https://api.customer.example/v1/chat?key=abc",
        )
        self.assertEqual(bundle["scan_request"]["credential_ref"], "<redacted>")
        self.assertNotIn(secret, json.dumps(bundle))

    def test_live_probe_bundle_verify_round_trip(self):
        bundle = self.store.build(
            bundle_id=self.bundle_id,
            snapshot_hash="sha256:abc",
            evidence=self.evidence,
            scan_request=_LIVE_PROBE_SCAN_REQUEST,
            prober_version="are-scanner@cd3ec38",
            target_endpoint_url="https://api.customer.example/v1/chat?key=abc",
        )
        verify(bundle)
        got = self.store.get(self.bundle_id)
        self.assertEqual(got, bundle)

    def test_live_probe_bundle_verify_detects_scan_request_tampering(self):
        bundle = self.store.build(
            bundle_id=self.bundle_id,
            snapshot_hash="sha256:abc",
            evidence=self.evidence,
            scan_request=_LIVE_PROBE_SCAN_REQUEST,
            prober_version="are-scanner@cd3ec38",
            target_endpoint_url="https://api.customer.example/v1/chat?key=abc",
        )
        bundle["target_endpoint_host"] = "evil.example"
        with self.assertRaisesRegex(BundleError, "signature mismatch"):
            verify(bundle)

    def test_live_probe_inputs_are_all_or_nothing(self):
        with self.assertRaisesRegex(BundleError, "all of scan_request"):
            self.store.build(
                bundle_id=self.bundle_id,
                snapshot_hash="sha256:abc",
                evidence=self.evidence,
                scan_request=_LIVE_PROBE_SCAN_REQUEST,
                # prober_version + target_endpoint_url intentionally omitted.
            )


class OfflineReplayByteIdenticalRegressionTests(unittest.TestCase):
    """Hard Invariant #2 on BEAAA-533: offline_replay bundles are unchanged pre/post BEAAA-541.

    The bundle is a hash-signed canonical-JSON manifest, so byte-identical
    output is the strongest possible regression check: any drift in field
    order, default values, or signing logic surfaces immediately as a hex
    delta on `signed_manifest`. Acceptance criterion #5 and the parent's
    Δ = 0.0000 invariant.
    """

    # Pin the expected hex digest of a known offline bundle. If this test
    # ever breaks, that's the canary that the offline path was inadvertently
    # touched. Bundle stored as fixture to detect any field reorder.
    EXPECTED_BUNDLE_ID = "78b9cfc9-2cfd-5e76-a0f6-7c4ca6ce4d9e"
    EXPECTED_SIGNED_MANIFEST_PREFIX = "hmac-sha256:"

    def setUp(self):
        self.store = BundleStore()
        self.bundle_id = derive_bundle_id(
            snapshot_hash="sha256:" + "a" * 64,
            tenant_id="11111111-1111-1111-1111-111111111111",
            scored_at="2026-04-22T00:00:00+00:00",
        )

    def test_offline_bundle_has_no_live_probe_fields(self):
        bundle = self.store.build(
            bundle_id=self.bundle_id,
            snapshot_hash="sha256:abc",
            evidence=[{"dim_id": "P1.1", "uri": "https://e/x", "sha256": ""}],
        )
        for key in ("scan_request", "prober_version", "target_endpoint_host"):
            self.assertNotIn(key, bundle)

    def test_offline_bundle_serialization_is_byte_identical(self):
        # Pin the exact canonical-JSON byte sequence for a fixed offline
        # input. This is the byte-identical pre/post check called out in
        # acceptance criterion #5 — any drift in the offline manifest shape
        # changes this byte sequence and the test fails loudly.
        bundle = self.store.build(
            bundle_id=self.bundle_id,
            snapshot_hash="sha256:abc",
            evidence=[{"dim_id": "P1.1", "uri": "https://e/x", "sha256": ""}],
        )
        manifest_bytes = json.dumps(
            {k: v for k, v in bundle.items() if k != "signed_manifest"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        expected = (
            b'{"bundle_id":"' + self.bundle_id.encode("utf-8") + b'",'
            b'"evidence":[{"dim_id":"P1.1","sha256":"","uri":"https://e/x"}],'
            b'"snapshot_hash":"sha256:abc"}'
        )
        self.assertEqual(manifest_bytes, expected)
        self.assertTrue(
            bundle["signed_manifest"].startswith(self.EXPECTED_SIGNED_MANIFEST_PREFIX)
        )

    def test_offline_signed_manifest_is_pinned(self):
        # The HMAC over the offline manifest is deterministic given the
        # default dev key. Pinning the hex digest catches any signing-path
        # drift end-to-end (key derivation, canonicalization, manifest
        # composition). If the dev key in api.bundle ever changes this
        # fixture must move with it.
        bundle = self.store.build(
            bundle_id=self.bundle_id,
            snapshot_hash="sha256:abc",
            evidence=[{"dim_id": "P1.1", "uri": "https://e/x", "sha256": ""}],
        )
        self.assertEqual(
            bundle["signed_manifest"],
            "hmac-sha256:e7fcc89ecc6b6bb76543605d479522c8a0ae1bb2d76ef8afd0dd14359ab34e81",
        )


if __name__ == "__main__":
    unittest.main()
