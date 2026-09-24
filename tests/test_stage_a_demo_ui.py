import tempfile
import unittest
from pathlib import Path

from stage_a_demo.core import EvidenceStore
from stage_a_demo.activation import NO_COVERAGE_STATEMENT
from stage_a_demo.approval import build_issuer
from stage_a_demo.ui import (
    BACKEND_CASES,
    FIXTURES,
    approve_case,
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
        grant = approve_case(name, self.db_path, issuer=build_issuer("test-only-approval-secret"))["grant"]
        return execute_case(name, self.db_path, grant)

    def test_happy_path_and_demo_labels_are_rendered(self):
        page = render_page()
        self.assertIn("Give your agent one clear task.", page)
        self.assertIn("Approve this task", page)
        self.assertIn("Demo · not activated", page)
        self.assertIn("Simulated receipt", page)
        self.assertIn("Passive Monitor observation does not authorize", page)

    def test_every_required_fixture_is_directly_reachable(self):
        required = {"correct", "incorrect", "duplicate", "missing-evidence", "reconstructed-claim", "unsupported", "disconnected", "expired", "denied", "error"}
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
        invitation = invitation_payload()
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


if __name__ == "__main__":
    unittest.main()
