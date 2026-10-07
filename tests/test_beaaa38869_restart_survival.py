"""BEAAA-38869 item 2: accepted scans survive a scanner API restart.

Verifies that the AcceptedScanStore persistence layer:
- round-trips accept -> mark_terminal without data loss
- survives a close/reopen (simulating a process restart)
- marks non-resumable scans (those that need credentials) correctly
- outbox prevents double-delivery of the terminal webhook
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

from api.accepted_scan_store import (
    AcceptedScanStore,
    SqliteOutboxStore,
    credential_free,
    request_from_record,
    request_to_record,
    result_from_record,
    result_to_record,
)
from api.webhook_outbox import OutboxRecord, STATUS_PENDING, STATUS_DELIVERED
from scanners.live_probe_orchestrator import ScanRequest, ScanResult


def _make_scan_request(scan_id="s-1", tenant_id="t-1", auth_type="none"):
    return ScanRequest(
        scan_id=scan_id,
        tenant_id=tenant_id,
        agent_endpoint="https://example.com/agent",
        credential_ref=None if auth_type == "none" else "ref:secret",
        corpus_snapshot_hash="abc",
        scanner_version="0.3.6",
        corpus_version="1.2.0",
        seed=0,
        endpoint_config={"auth": {"type": auth_type}},
    )


def _make_result(scan_id="s-1", status="terminal"):
    return ScanResult(
        scan_id=scan_id,
        status=status,
        scanner_version="0.3.6",
        corpus_version="1.2.0",
        rows_emitted=12,
    )


def _make_outbox_record(key="idem-1", status=STATUS_PENDING):
    return OutboxRecord(
        idempotency_key=key,
        url="https://example.com/cb",
        secret_ref="",
        payload={"scan_id": "s-1"},
        delivery_id=key,
        status=status,
    )


class TestAcceptedScanStoreRoundtrip(unittest.TestCase):

    def setUp(self):
        self.store = AcceptedScanStore(":memory:")

    def tearDown(self):
        self.store.close()

    def test_accept_mark_terminal_round_trip(self):
        req = _make_scan_request()
        self.store.accept(req, agent_id="a-1", invocation_context=None, webhook_cfg=None)
        row = self.store.get("s-1")
        self.assertIsNotNone(row)
        self.assertEqual(row["state"], "accepted")
        self.assertTrue(row["resumable"])  # auth=none -> credential-free -> resumable

        result = _make_result()
        self.store.mark_terminal("s-1", result)
        row = self.store.get("s-1")
        self.assertEqual(row["state"], "terminal")
        self.assertEqual(row['result'].status, "terminal")

    def test_non_resumable_scan_has_credential(self):
        req = _make_scan_request(auth_type="bearer")
        self.store.accept(req, agent_id="a-1", invocation_context=None, webhook_cfg=None)
        row = self.store.get("s-1")
        self.assertFalse(row["resumable"])

    def test_unfinished_returns_only_non_terminal(self):
        req1 = _make_scan_request("s-1")
        req2 = _make_scan_request("s-2")
        self.store.accept(req1, agent_id="a-1", invocation_context=None, webhook_cfg=None)
        self.store.accept(req2, agent_id="a-1", invocation_context=None, webhook_cfg=None)
        self.store.mark_terminal("s-1", _make_result("s-1"))
        unfinished = [r["scan_id"] for r in self.store.unfinished()]
        self.assertNotIn("s-1", unfinished)
        self.assertIn("s-2", unfinished)

    def test_persistence_survives_reopen(self):
        """Close and reopen on the same file; row still accessible."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            store1 = AcceptedScanStore(path)
            req = _make_scan_request()
            store1.accept(req, agent_id="a-1", invocation_context=None, webhook_cfg=None)
            store1.mark_terminal("s-1", _make_result())
            store1.close()

            store2 = AcceptedScanStore(path)
            row = store2.get("s-1")
            store2.close()

            self.assertIsNotNone(row)
            self.assertEqual(row["state"], "terminal")
            self.assertEqual(row['result'].scan_id, "s-1")
        finally:
            os.unlink(path)

    def test_purge_expired_removes_old_rows(self):
        req = _make_scan_request()
        self.store.accept(req, agent_id="a-1", invocation_context=None, webhook_cfg=None)
        self.store.mark_terminal("s-1", _make_result())
        # Age the row past retention
        with self.store._write() as conn:
            conn.execute(
                "UPDATE accepted_scans SET accepted_at=? WHERE scan_id='s-1'",
                (time.time() - self.store.retention_seconds - 1,)
            )
        self.store.purge_expired()
        self.assertIsNone(self.store.get("s-1"))


class TestWebhookOutboxNoDoubleDelivery(unittest.TestCase):

    def setUp(self):
        self.store = AcceptedScanStore(":memory:")
        self.outbox = self.store.outbox_store()

    def tearDown(self):
        self.store.close()

    def test_pending_then_delivered_not_retriable(self):
        rec = _make_outbox_record()
        self.outbox.put(rec)
        pending = list(self.outbox.pending())
        self.assertEqual(len(pending), 1)

        rec.status = STATUS_DELIVERED
        self.outbox.put(rec)
        self.assertEqual(list(self.outbox.pending()), [])

    def test_idempotent_re_put_does_not_duplicate(self):
        rec = _make_outbox_record("k-dup")
        self.outbox.put(rec)
        self.outbox.put(rec)
        self.assertEqual(len(list(self.outbox.pending())), 1)
