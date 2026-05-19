"""BEAAA-546 — T7 §5#7: public re-export of tenant-isolation helper.

Pins the contract that T1's /scan handler module (and any future sibling
handler) imports `verify_tenant_isolation` from `api.handlers` rather than
copying the body. Both call sites must yield byte-identical 409 messages.

Classification: rule-execution. N=5 calibration delta on golden = 0.0000.
"""
from __future__ import annotations

import unittest

import api.handlers as handlers_module
from api.handlers import HandlerError, verify_tenant_isolation


class TenantIsolationReuseTests(unittest.TestCase):
    def test_public_alias_is_same_object_as_private_helper(self):
        # Re-export, not fork. Same callable identity guarantees no drift.
        self.assertIs(verify_tenant_isolation, handlers_module._verify_tenant_isolation)

    def test_missing_header_raises_409_with_pinned_message(self):
        with self.assertRaises(HandlerError) as ctx:
            verify_tenant_isolation("t1", None)
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(
            ctx.exception.message,
            "X-Tenant-Id header required for tenant isolation",
        )

    def test_empty_header_raises_409_with_pinned_message(self):
        with self.assertRaises(HandlerError) as ctx:
            verify_tenant_isolation("t1", "")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(
            ctx.exception.message,
            "X-Tenant-Id header required for tenant isolation",
        )

    def test_mismatch_raises_409_with_pinned_message(self):
        with self.assertRaises(HandlerError) as ctx:
            verify_tenant_isolation("t2", "t1")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(
            ctx.exception.message,
            "tenant mismatch between header and body",
        )

    def test_match_returns_none(self):
        self.assertIsNone(verify_tenant_isolation("t1", "t1"))


if __name__ == "__main__":
    unittest.main()
