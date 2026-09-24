import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from stage_a_demo.core import EvidenceStore
from stage_a_demo.activation import NO_COVERAGE_STATEMENT
from stage_a_demo.approval import build_issuer
from stage_a_demo.ui import (
    BACKEND_CASES,
    FIXTURES,
    approve_case,
    build_server,
    execute_case,
    invitation_payload,
    invitation_transition,
    receipt_fixture,
    render_page,
)


class StageADemoUITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "ui.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def open_case(self, name):
        """Approve first when the case executes; read-only fixtures need no grant."""
        if name not in BACKEND_CASES:
            return receipt_fixture(name, self.db_path)
        grant = approve_case(name, self.db_path, issuer=build_issuer())["grant"]
        return execute_case(name, self.db_path, grant)

    def test_happy_path_and_demo_labels_are_rendered(self):
        page = render_page()
        self.assertIn("Give your agent one clear task.", page)
        self.assertIn("Approve this task", page)
        self.assertIn("Demo · not activated", page)
        self.assertIn("Simulated receipt", page)
        self.assertIn("Passive Monitor observation does not authorize", page)

    def test_every_required_fixture_is_directly_reachable(self):
        required = {"correct", "incorrect", "duplicate", "missing-evidence", "reconstructed-claim", "over-mandate", "unsupported", "disconnected", "expired", "denied", "error"}
        self.assertEqual(required, set(FIXTURES))
        for name in required:
            self.assertEqual(name, self.open_case(name)["case"])

    def test_purchase_fixtures_use_persisted_backend_packets(self):
        expected = {"correct": "MATCH", "incorrect": "MISMATCH", "duplicate": "MATCH", "missing-evidence": "UNKNOWN/INCOMPLETE", "reconstructed-claim": "MATCH"}
        for name, status in expected.items():
            response = self.open_case(name)
            self.assertEqual(status, response["backend_packet"]["status"])
            self.assertEqual(1, response["authoritative_executions"])
            reopened = EvidenceStore(self.db_path)
            self.assertEqual(status, reopened.packet(name)["status"])
            reopened.close()

    def test_ui_only_states_do_not_fabricate_backend_packets(self):
        for name in ("unsupported", "disconnected", "expired", "denied", "error"):
            self.assertIsNone(self.open_case(name)["backend_packet"])

    def test_invitation_is_privacy_safe(self):
        invitation = invitation_payload("2026-09-21T10:15:00Z")
        self.assertEqual("21 Sep 2026", invitation["completed"])
        self.assertEqual({"brand", "mode", "category", "completed", "invitation_code"}, set(invitation))
        serialized = str(invitation).lower()
        for private_field in ("merchant", "amount", "item", "identity", "mandate", "evidence", "receipt"):
            self.assertNotIn(private_field, serialized)

    def test_invitation_controls_enforce_verified_result_and_privacy(self):
        created = invitation_transition("MATCH", "create")
        self.assertEqual("created", created["state"])
        self.assertEqual(NO_COVERAGE_STATEMENT, created["protection"])
        self.assertEqual(invitation_payload(), created["payload"])
        for status in ("MISMATCH", "UNKNOWN/INCOMPLETE", "ERROR"):
            self.assertEqual({"state": "unavailable", "payload": None, "protection": NO_COVERAGE_STATEMENT}, invitation_transition(status, "create"))
        self.assertEqual({"state": "kept_private", "payload": None, "protection": NO_COVERAGE_STATEMENT}, invitation_transition("MATCH", "keep_private"))

    def test_done_control_has_deterministic_reset_transition(self):
        self.assertEqual({"state": "reset", "payload": None, "protection": NO_COVERAGE_STATEMENT}, invitation_transition("MATCH", "done"))
        page = render_page()
        self.assertNotIn("nav:after", page)
        self.assertIn("function resetFlow()", page)
        self.assertIn("function renderState()", page)

    def test_supported_agent_and_keyboard_native_denial_are_explicit(self):
        page = render_page()
        self.assertIn('id="supported-agent"', page)
        self.assertIn("Synthetic purchase agent · demo", page)
        self.assertIn('id="deny-approval"', page)
        self.assertIn("Deny and cancel", page)
        self.assertIn("function denyApproval()", page)
        self.assertNotIn("onclick=", page)

    def test_state_drives_status_progress_and_controls(self):
        page = render_page()
        self.assertIn("const statusByStep=", page)
        self.assertIn("const primaryLabels=", page)
        self.assertIn("renderState();if(step===5)loadReceipt('correct')", page)
        self.assertIn("stepNames[step]", page)

    def test_document_title_and_privacy_actions_are_functional(self):
        page = render_page()
        self.assertIn("<title>BackBond Stage A demo · Demo not activated</title>", page)
        self.assertIn("function createInvitation()", page)
        self.assertIn("function keepPrivate()", page)
        self.assertIn("function resetFlow()", page)
        self.assertIn("No invitation was created or shared.", page)

    def test_accessibility_and_responsive_contracts_exist(self):
        page = render_page()
        for marker in ('aria-live="polite"', ':focus-visible', '@media(max-width:600px)', '@media(max-width:900px)', 'min-height:48px', 'tabindex="-1"'):
            self.assertIn(marker, page)


class StageASharingSeamTests(unittest.TestCase):
    """BEAAA-35453 A13/A14, measured over the served HTTP surface.

    The gate in :func:`invitation_transition` was already correct and already
    asserted four ways above; what failed was that nothing served called it.
    These tests drive the same routes the page's script drives, so a fix that
    leaves the served path ungated cannot pass them.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = build_server("127.0.0.1", 0, Path(self.tmp.name) / "ui.sqlite3")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def post(self, path, body):
        request = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def run_case(self, name):
        if name not in BACKEND_CASES:
            request = urllib.request.Request(f"{self.base}/api/receipt?case={name}")
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode())
        _, granted = self.post("/api/approve", {"case": name, "origin": "fixture-explorer"})
        _, receipt = self.post("/api/receipt", {"case": name, "grant": granted["grant"]})
        return receipt

    def test_reading_a_receipt_delivers_no_shareable_note(self):
        for name in ("correct", "incorrect", "over-mandate", "denied", "expired"):
            self.assertIsNone(self.run_case(name)["invitation"], name)

    def test_opt_in_route_hands_the_note_over_only_when_asked(self):
        self.run_case("correct")
        status, created = self.post("/api/invitation", {"case": "correct", "action": "create"})
        self.assertEqual(200, status)
        self.assertEqual("created", created["state"])
        self.assertEqual(
            {"brand", "mode", "category", "completed", "invitation_code"},
            set(created["payload"]),
        )

    def test_keep_private_reaches_the_server(self):
        self.run_case("correct")
        status, kept = self.post("/api/invitation", {"case": "correct", "action": "keep_private"})
        self.assertEqual(200, status)
        self.assertEqual({"state": "kept_private", "payload": None, "protection": kept["protection"]}, kept)

    def test_a_refused_or_denied_result_can_never_be_shared(self):
        self.run_case("over-mandate")
        status, refused = self.post("/api/invitation", {"case": "over-mandate", "action": "create"})
        # Nothing executed, so there is no result to share and no date to name.
        self.assertEqual(409, status)
        self.assertEqual("approval_required", refused["error"])
        self.run_case("denied")
        status, denied = self.post("/api/invitation", {"case": "denied", "action": "create"})
        self.assertEqual(200, status)
        self.assertEqual("unavailable", denied["state"])
        self.assertIsNone(denied["payload"])

    def test_the_shared_date_comes_from_the_execution(self):
        receipt = self.run_case("correct")
        recorded = receipt["completed_at"]
        self.assertIsNotNone(recorded)
        _, created = self.post("/api/invitation", {"case": "correct", "action": "create"})
        expected = time.strftime("%d %b %Y", time.strptime(recorded, "%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(expected, created["payload"]["completed"])
        self.assertNotEqual("21 Sep 2026", expected)

    def test_a_caller_cannot_assert_its_own_result(self):
        self.run_case("incorrect")
        # The request names a case, never a status: the gate judges what this
        # server recorded, so "give me the MATCH note" is not sayable.
        _, asserted = self.post(
            "/api/invitation",
            {"case": "incorrect", "action": "create", "status": "MATCH"},
        )
        self.assertEqual("unavailable", asserted["state"])
        self.assertIsNone(asserted["payload"])

    def test_the_page_carries_the_choice_to_the_server(self):
        page = render_page()
        self.assertIn("/api/invitation", page)
        self.assertIn("function decideInvitation(action)", page)
        self.assertIn("'create'", page)
        self.assertIn("'keep_private'", page)


if __name__ == "__main__":
    unittest.main()
