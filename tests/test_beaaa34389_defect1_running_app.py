"""BEAAA-34389 defect 1: self-approval must be impossible against the running app.

The Founder gate on BEAAA-34387 asked for "the test that proves it", and the CEO
ruling required that a caller-minted token be rejected by the *running app's*
verifier rather than by one a test builds with foreign key material. Every
assertion here is therefore made against the server built by `ui.build_server`,
the same wiring `python3 -m stage_a_demo.ui` uses.

It also covers the entrypoint: nothing in the suite imported `__main__`, so
`python3 -m stage_a_demo` could regress to a TypeError over a green suite.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from stage_a_demo import approval, ui
from stage_a_demo.core import (
    ApprovalVerifier,
    EvidenceStore,
    HumanApprovalIssuer,
    Mandate,
    SyntheticMerchantAdapter,
    _approval_digest,
)

RETIRED_DEFAULT = "stage-a-human-demo-secret"


class RunningAppSelfApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "app.sqlite3"
        self.server = ui.build_server("127.0.0.1", 0, self.db)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def running_issuer(self):
        return self.server.RequestHandlerClass.approval_issuer

    def test_the_approved_path_still_works_end_to_end(self):
        status, approved = self.post("/api/approve", {"case": "correct", "origin": "approval-step"})
        self.assertEqual(200, status)
        status, receipt = self.post("/api/receipt", {"case": "correct", "grant": approved["grant"]})
        self.assertEqual(200, status)
        self.assertEqual("MATCH", receipt["status"])
        self.assertEqual(1, receipt["authoritative_executions"])

    def test_unapproved_get_executes_nothing(self):
        """The GET path is read-only: no approval, no execution, no receipt."""
        status, body = self.get("/api/receipt?case=correct")
        self.assertIn(status, (200, 409))
        self.assertIsNone(body.get("backend_packet"))
        store = EvidenceStore(self.db)
        try:
            self.assertEqual(0, store.case_count())
            self.assertEqual(0, store.approval_count("correct"))
        finally:
            store.close()

    def test_a_forged_grant_executes_nothing(self):
        status, body = self.post("/api/receipt", {"case": "correct", "grant": "grant-deadbeefdeadbeef"})
        self.assertEqual(403, status)
        self.assertEqual("approval_required", body["error"])
        store = EvidenceStore(self.db)
        try:
            self.assertEqual(0, store.case_count())
        finally:
            store.close()

    def test_caller_minted_tokens_are_rejected_by_the_running_issuers_verifier(self):
        mandate = ui._mandate_for("correct")
        _, running_verifier = self.running_issuer().approve(mandate)
        for guess in (RETIRED_DEFAULT, "secret", "demo", "a" * 64):
            self.assertFalse(
                running_verifier.verify(mandate, HumanApprovalIssuer(guess).approve(mandate)[0]),
                f"the running app's verifier accepted a token minted with {guess!r}",
            )
            self.assertFalse(running_verifier.verify(mandate, _approval_digest(guess, mandate)))

    def test_holding_the_running_verifier_does_not_confer_minting(self):
        mandate = ui._mandate_for("correct")
        _, verifier = self.running_issuer().approve(mandate)
        for value in vars(verifier).values():
            candidates = [value] if isinstance(value, str) else []
            if isinstance(value, dict):
                candidates = [k for k in value if isinstance(k, str)] + [
                    v for v in value.values() if isinstance(v, str)
                ]
            for candidate in candidates:
                forged = HumanApprovalIssuer(candidate).approve(mandate)[0]
                self.assertFalse(
                    verifier.verify(mandate, forged),
                    "verifier state was sufficient to mint an accepted token",
                )

    def test_self_approved_execution_is_refused(self):
        mandate = Mandate("m-attacker-v1", "SKU-EXPENSIVE", 999_900)
        _, human_verifier = self.running_issuer().approve(mandate)
        attacker_token = HumanApprovalIssuer(RETIRED_DEFAULT).approve(mandate)[0]
        store = EvidenceStore(Path(self.tmp.name) / "attack.sqlite3")
        try:
            with self.assertRaises(PermissionError):
                store.record_case(
                    case_id="attacker", mandate=mandate, approval_token=attacker_token,
                    asserted_sku="SKU-EXPENSIVE", asserted_amount_cents=999_900,
                    idempotency_key="idem-attacker", approval_verifier=human_verifier,
                    merchant=SyntheticMerchantAdapter(),
                )
            self.assertEqual(0, store.execution_count("idem-attacker"))
        finally:
            store.close()

    def test_the_request_path_cannot_reach_a_signing_capability(self):
        source = inspect.getsource(ui)
        self.assertNotIn("HumanApprovalIssuer", source)
        self.assertFalse(hasattr(ui, "HumanApprovalIssuer"))
        # The execution step never receives an issuer, only a recorded grant.
        self.assertNotIn("issuer", inspect.signature(ui.execute_case).parameters)
        self.assertIn("issuer", inspect.signature(ui.approve_case).parameters)

    def test_approval_without_a_wired_issuer_refuses(self):
        with self.assertRaises(ui.ApprovalRequired):
            ui.approve_case("correct", self.db, "approval-step", issuer=None)

    def test_no_default_key_material_exists(self):
        for module in (ui, approval):
            self.assertNotIn(RETIRED_DEFAULT, inspect.getsource(module))
        with self.assertRaises(TypeError):
            HumanApprovalIssuer()
        with self.assertRaises(TypeError):
            ApprovalVerifier()

    def test_injected_secret_is_preferred_and_the_fallback_is_random(self):
        import os

        os.environ[approval.APPROVAL_SECRET_ENV] = "injected-value"
        try:
            self.assertEqual("injected-value", approval.load_approval_secret())
        finally:
            del os.environ[approval.APPROVAL_SECRET_ENV]
        self.assertNotEqual(approval.load_approval_secret(), approval.load_approval_secret())


class EntrypointTests(unittest.TestCase):
    """`python3 -m stage_a_demo` is not imported by any other test, so it needs its own."""

    def test_module_entrypoint_runs_all_five_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "cli.sqlite3"
            proc = subprocess.run(
                [sys.executable, "-m", "stage_a_demo", "--db", str(db)],
                capture_output=True, text=True, timeout=120,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(
                [("correct", "MATCH"), ("mismatch", "MISMATCH"), ("duplicate", "MATCH"),
                 ("incomplete", "UNKNOWN/INCOMPLETE"), ("reconstruct", "MATCH")],
                [(c["case_id"], c["status"]) for c in payload["cases"]],
            )
            self.assertEqual(1, payload["duplicate_authoritative_executions"])
            self.assertEqual("DEMO", payload["mode"])

    def test_ui_entrypoint_builds_a_server_with_a_wired_issuer(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = ui.build_server("127.0.0.1", 0, Path(tmp) / "ui.sqlite3")
            try:
                self.assertIsNotNone(server.RequestHandlerClass.approval_issuer)
            finally:
                server.server_close()


if __name__ == "__main__":
    unittest.main()
