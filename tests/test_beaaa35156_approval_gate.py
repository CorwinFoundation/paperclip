"""BEAAA-35156: the demo server executes only on approval evidence it did not mint."""

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from stage_a_demo.core import EvidenceStore, SyntheticMerchantAdapter
from stage_a_demo.ui import ApprovalRequired, CaseNotExecuted, approve_case, execute_case, make_handler, receipt_fixture


class StageADemoApprovalGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "ui.sqlite3"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.db_path))
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def request(self, path, payload=None):
        url = f"{self.base}{path}"
        if payload is None:
            req = urllib.request.Request(url)
        else:
            req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode())

    def ledger(self):
        store = EvidenceStore(self.db_path)
        try:
            return store.case_count(), store.execution_count("idem-correct")
        finally:
            store.close()

    def test_unauthenticated_get_neither_executes_nor_claims_approval(self):
        status, body = self.request("/api/receipt?case=correct")
        self.assertEqual(409, status)
        self.assertEqual("approval_required", body["error"])
        self.assertEqual((0, 0), self.ledger())

    def test_read_only_get_returns_the_stored_case_without_writing_again(self):
        grant = self.request("/api/approve", {"case": "correct", "origin": "approval-step"})[1]["grant"]
        self.assertEqual(200, self.request("/api/receipt", {"case": "correct", "grant": grant})[0])
        status, body = self.request("/api/receipt?case=correct")
        self.assertEqual(200, status)
        self.assertEqual("MATCH", body["status"])
        self.assertEqual(1, body["authoritative_executions"])
        self.assertEqual((1, 1), self.ledger())

    def test_execution_needs_a_grant_and_spends_it_once(self):
        status, body = self.request("/api/receipt", {"case": "correct"})
        self.assertEqual(403, status)
        self.assertEqual("approval_required", body["error"])
        self.assertEqual((0, 0), self.ledger())

        grant = self.request("/api/approve", {"case": "correct", "origin": "approval-step"})[1]["grant"]
        status, body = self.request("/api/receipt", {"case": "correct", "grant": grant})
        self.assertEqual(200, status)
        self.assertEqual("MATCH", body["status"])
        self.assertTrue(body["approved"])
        self.assertEqual({"label": "Human approval", "value": "Approved for mandate v1", "source": "Verified event"}, body["evidence"][0])

        replayed = self.request("/api/receipt", {"case": "correct", "grant": grant})
        self.assertEqual(403, replayed[0])
        self.assertEqual((1, 1), self.ledger())

    def test_a_forged_grant_executes_nothing(self):
        status, _ = self.request("/api/receipt", {"case": "correct", "grant": "grant-deadbeefdeadbeef"})
        self.assertEqual(403, status)
        self.assertEqual((0, 0), self.ledger())

    def test_display_only_fixtures_read_without_approval_or_writes(self):
        for name in ("unsupported", "disconnected", "expired", "denied", "error"):
            status, body = self.request(f"/api/receipt?case={name}")
            self.assertEqual(200, status)
            self.assertIsNone(body["backend_packet"])
            self.assertFalse(body["approved"])
            self.assertEqual("Missing", body["evidence"][0]["source"])
        self.assertEqual((0, 0), self.ledger())

    def test_concurrent_execution_answers_both_callers_and_records_one_execution(self):
        grants = [self.request("/api/approve", {"case": "correct", "origin": "approval-step"})[1]["grant"] for _ in range(2)]
        results = []

        def slow_purchase(self_adapter, *, case_id, sku, amount_cents):
            time.sleep(0.25)
            return SyntheticMerchantAdapter.purchase.__wrapped__(self_adapter, case_id=case_id, sku=sku, amount_cents=amount_cents) if hasattr(SyntheticMerchantAdapter.purchase, "__wrapped__") else {
                "merchant": "synthetic-merchant", "merchant_event_id": f"merchant-{case_id}", "sku": sku,
                "amount_cents": amount_cents, "outcome": "accepted", "synthetic": True,
            }

        def call(grant):
            try:
                results.append(self.request("/api/receipt", {"case": "correct", "grant": grant}))
            except Exception as error:  # a dropped connection is the defect under test
                results.append(("dropped", repr(error)))

        with patch.object(SyntheticMerchantAdapter, "purchase", slow_purchase):
            threads = [threading.Thread(target=call, args=(grant,)) for grant in grants]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)

        self.assertEqual(2, len(results))
        for status, body in results:
            self.assertEqual(200, status, body)
            self.assertEqual("MATCH", body["status"])
        self.assertEqual((1, 1), self.ledger())

    def test_library_surface_keeps_minting_off_the_read_and_execution_paths(self):
        with self.assertRaises(CaseNotExecuted):
            receipt_fixture("correct", self.db_path)
        with self.assertRaises(ApprovalRequired):
            execute_case("correct", self.db_path, None)
        with self.assertRaises(ApprovalRequired):
            approve_case("denied", self.db_path)
        self.assertEqual((0, 0), self.ledger())


if __name__ == "__main__":
    unittest.main()
