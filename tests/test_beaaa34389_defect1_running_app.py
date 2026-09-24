"""BEAAA-34389 defect 1: self-approval must be impossible against the running app.

The Founder gate on BEAAA-34387 asked for "the test that proves it", and the CEO
ruling required that a caller-minted token be rejected by the *running app's*
verifier rather than by one a test builds with foreign key material. Every
assertion here is therefore made against the server built by `ui.build_server`,
the same wiring `python3 -m stage_a_demo.ui` uses.

It also covers the entrypoint: nothing in the suite imported `__main__`, so
`python3 -m stage_a_demo` could regress to a TypeError over a green suite.

Non-regression guard for the Addendum 2 receipt rewrite (BEAAA-34389). When the
approval receipt becomes the evidence root, this file is what proves the
BEAAA-35331 narrowing survived it. The invariant as the code actually implements
it, which is not the same as the PR #626 shape:

* No authorisation-shaped argument crosses into execution. `record_case` takes a
  `grant_id`; it does not take a mandate, a token, a verifier, an issuer or a
  secret, and it takes no `**kwargs` that could carry one.
* The verifier is *not* injected at `EvidenceStore` construction. `__init__`
  takes a path only. `record_case` consumes the approvals row itself, re-checks
  `origin` against `APPROVAL_ORIGINS` at consume time, and rebuilds the verifier
  internally from the recorded token.
* SA4 through SA7 fail closed, and SA8 with them: a token is bound to its grant
  id, so a genuine token replayed under a second row is not attested.

A receipt object may be the evidence root without ever being an argument to
`record_case`. If a change needs one of these assertions relaxed, that is a
ruling, not a refactor.
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
from stage_a_demo.approval import _process_issuer, approve_mandate
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

    def test_caller_minted_tokens_are_rejected_by_the_running_approval_authority(self):
        mandate = ui._mandate_for("correct")
        authority = self.running_issuer()
        for guess in (RETIRED_DEFAULT, "secret", "demo", "a" * 64):
            self.assertFalse(
                authority.attests(mandate, "grant-x", HumanApprovalIssuer(guess).approve(mandate, "grant-x")),
                f"the running app's approval authority accepted a token minted with {guess!r}",
            )
            self.assertFalse(authority.attests(mandate, "grant-x", _approval_digest(guess, mandate, "grant-x")))

    def test_the_verifying_half_is_not_obtainable_by_a_caller(self):
        """BEAAA-35331 SA5/SA6: approving returns no verifier and none can be built."""
        mandate = ui._mandate_for("correct")
        token = self.running_issuer().approve(mandate, "grant-x")
        self.assertIsInstance(token, str)
        self.assertFalse(hasattr(ApprovalVerifier, "for_recorded_approval"))
        with self.assertRaises(TypeError):
            ApprovalVerifier({"any": "mapping"})

    def test_self_approved_execution_is_refused(self):
        """BEAAA-35331 SA4/SA7: no caller-supplied verifier, no caller-written grant."""
        mandate = Mandate("m-attacker-v1", "SKU-EXPENSIVE", 999_900)
        attacker_token = HumanApprovalIssuer(RETIRED_DEFAULT).approve(mandate, "grant-self-token")
        store = EvidenceStore(Path(self.tmp.name) / "attack.sqlite3")

        class CallerSuppliedVerifier:
            def verify(self, *_args, **_kwargs):
                return True

        try:
            # SA4: record_case has no verifier or token parameter to hijack.
            with self.assertRaises(TypeError):
                store.record_case(
                    case_id="attacker", grant_id="grant-attacker",
                    asserted_sku="SKU-EXPENSIVE", asserted_amount_cents=999_900,
                    idempotency_key="idem-attacker", merchant=SyntheticMerchantAdapter(),
                    approval_verifier=CallerSuppliedVerifier(), approval_token="not-a-token",
                )
            # SA7: the execution side has no public way to write an approvals row,
            # and the private one refuses a foreign issuer and an invented origin.
            self.assertFalse(hasattr(store, "record_approval"))
            self.assertFalse(hasattr(store, "consume_approval"))
            with self.assertRaises(PermissionError):
                store._record_approval(
                    grant_id="grant-attacker", case_id="attacker", mandate=mandate,
                    approval_token=attacker_token, origin="agent-invented-origin",
                    issuer=HumanApprovalIssuer(RETIRED_DEFAULT),
                )
            with self.assertRaises(PermissionError):
                store.record_case(
                    case_id="attacker", grant_id="grant-attacker",
                    asserted_sku="SKU-EXPENSIVE", asserted_amount_cents=999_900,
                    idempotency_key="idem-attacker", merchant=SyntheticMerchantAdapter(),
                )
            self.assertEqual(0, store.execution_count("idem-attacker"))
            self.assertEqual(0, store.case_count())
        finally:
            store.close()

    def test_a_self_written_approvals_row_does_not_execute(self):
        """BEAAA-35331 SA7: a row written straight into the table still refuses.

        Origin is re-validated at consume time, and the token is checked against
        the process approval authority, so neither a self-chosen token nor an
        invented origin reaches an execution.
        """
        mandate = Mandate("m-attacker-v2", "SKU-EXPENSIVE", 999_900)
        store = EvidenceStore(Path(self.tmp.name) / "attack2.sqlite3")
        try:
            import json as _json
            from dataclasses import asdict

            def insert(grant_id, token, origin):
                with store.db:
                    store.db.execute(
                        "INSERT INTO approvals (grant_id, case_id, mandate_json, approval_token, origin)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (grant_id, "attacker", _json.dumps(asdict(mandate), sort_keys=True), token, origin),
                    )

            # Self-chosen token on an allowed origin: refused by the authority check.
            insert("grant-self-token", "agent-chosen-token", "approval-step")
            with self.assertRaises(PermissionError):
                store.record_case(case_id="attacker", grant_id="grant-self-token",
                    asserted_sku="SKU-EXPENSIVE", asserted_amount_cents=999_900,
                    idempotency_key="idem-a2", merchant=SyntheticMerchantAdapter())
            # Authority-minted token on an invented origin: refused at consume time.
            insert("grant-bad-origin", _process_issuer().approve(mandate, "grant-bad-origin"), "agent-invented-origin")
            with self.assertRaises(PermissionError):
                store.record_case(case_id="attacker", grant_id="grant-bad-origin",
                    asserted_sku="SKU-EXPENSIVE", asserted_amount_cents=999_900,
                    idempotency_key="idem-a3", merchant=SyntheticMerchantAdapter())
            self.assertEqual(0, store.case_count())
        finally:
            store.close()

    def test_nothing_authorisation_shaped_crosses_into_execution(self):
        """BEAAA-35331: the execution entry points take a grant id and nothing else."""
        params = set(inspect.signature(EvidenceStore.record_case).parameters)
        self.assertIn("grant_id", params)
        self.assertEqual(set(), params & {"approval_token", "approval_verifier", "mandate", "issuer", "secret"})
        exec_params = set(inspect.signature(ui.execute_case).parameters)
        self.assertEqual(set(), exec_params & {"issuer", "approval_token", "approval_verifier"})
        # A name-only check passes if the rewrite adds a catch-all, so pin the kinds too:
        # no **kwargs on either entry point, and no verifier smuggled in at construction.
        for fn in (EvidenceStore.record_case, ui.execute_case):
            kinds = {p.kind for p in inspect.signature(fn).parameters.values()}
            self.assertNotIn(inspect.Parameter.VAR_KEYWORD, kinds, fn.__qualname__)
            self.assertNotIn(inspect.Parameter.VAR_POSITIONAL, kinds, fn.__qualname__)
        init_params = set(inspect.signature(EvidenceStore.__init__).parameters)
        self.assertEqual(set(), init_params & {"approval_verifier", "verifier", "issuer", "secret"})

    def test_the_approved_path_through_the_ui_still_executes(self):
        """The supported human path still works end to end after the narrowing."""
        approved = ui.approve_case("correct", self.db, "approval-step", issuer=self.running_issuer())
        receipt = ui.execute_case("correct", self.db, approved["grant"])
        self.assertEqual("MATCH", receipt["status"])
        # The grant is single use.
        with self.assertRaises(ui.ApprovalRequired):
            ui.execute_case("incorrect", self.db, approved["grant"])

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
