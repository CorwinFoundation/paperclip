"""BEAAA-34396 W4/W5: a claim must be orderable against the coverage window.

The activation gate is time-bound, so "was this covered" and "is the programme
active now" are different questions. Before this, a claim carried only the
second: one settled loss read `protection_active=True` inside the window and
`protection_active=False` after it closed, with the verdict identical and
nothing in the payload placing the event against the window.

These drive the real `EvidenceStore` calls end to end — the receipt-write, the
outcome-record and the claim read — against a real on-disk activation registry,
rather than reproducing what `claim_for` does. The join under test lives in
`claim_for`, so a test that assembles the payload itself cannot see it.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stage_a_demo.activation import ACTIVATION_RECORDS_ENV, PROGRAM_VERSION_ENV
from stage_a_demo.core import EvidenceStore
from stage_a_demo.receipt import (ANSWER_OK, ApprovalReceipt, ExecutedOutcome,
                                  ProposedAction)

PV = "stage-a-window-test"
# The read-time block is computed against the real clock, so these sit in the
# past: the window opened 2026-08-01 and closed 2026-09-10, both behind us.
APPROVED_AT = "2026-08-01T00:00:00Z"
EXPIRY = "2026-09-10T00:00:00Z"
INSIDE = "2026-09-05T09:41:00Z"        # executed while the window was open
AFTER_EXPIRY = "2026-09-20T00:00:00Z"  # executed after it closed
STILL_OPEN = "2027-01-01T00:00:00Z"    # a registry whose window is open today

ACTION = ProposedAction(description="Hotel Ester, 12-15 Sep, 2 guests",
                        merchant="bookings.example", amount_cents=48_000,
                        non_recoverable=True, cancellation_terms="non-refundable")
OUTCOME = ExecutedOutcome("Hotel Ester, 19-22 Sep, 2 guests", "bookings.example", 48_000,
                          evidence_ref="confirmation-wrong-dates", observed_at=INSIDE)


def records(expires_at=EXPIRY):
    return {
        "governing_legal_structure": {"record_id": "r1", "program_version": PV,
            "status": "in_good_standing", "entity_name": "BackBond", "jurisdiction": "DE"},
        "executed_backing_authority": {"record_id": "r2", "program_version": PV, "status": "executed",
            "counterparty": "carrier", "executed_at": APPROVED_AT, "document_sha256": "a" * 64},
        "funded_financial_security": {"record_id": "r3", "program_version": PV, "status": "funded",
            "instrument": "trust", "funded_amount_cents": 1_000_000, "funded_at": APPROVED_AT},
        "approved_versioned_terms": {"record_id": "r4", "program_version": PV, "status": "approved",
            "terms_version": "1.0", "approved_at": APPROVED_AT, "expires_at": expires_at},
        "named_claims_and_payment_humans": {"record_id": "r5", "program_version": PV, "status": "named",
            "claims_reviewer": "a human", "payment_approver": "another human"},
        "approved_consumer_disclosure_set": {"record_id": "r6", "program_version": PV, "status": "approved",
            "disclosure_set_id": "d1", "approved_at": APPROVED_AT},
    }


class ClaimCarriesTheWindowInForceAtExecution(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "activation"
        root.mkdir()
        (root / f"{PV}.json").write_text(json.dumps(records()))
        self.env = mock.patch.dict(os.environ, {ACTIVATION_RECORDS_ENV: str(root),
                                                PROGRAM_VERSION_ENV: PV})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.root = root
        self.store = EvidenceStore(Path(self.tmp.name) / "evidence.sqlite3")
        self.addCleanup(self.store.close)

    def settled(self, task_id="t-window", executed_at=INSIDE):
        self.store.record_pre_execution_receipt(ApprovalReceipt(
            "r-window", task_id, ACTION, ANSWER_OK,
            "2026-09-05T09:07:00Z", "2026-09-05T09:09:00Z", "2026-09-05T09:09:05Z"))
        return self.store.record_outcome(task_id, OUTCOME, executed_at=executed_at)

    def test_the_claim_names_the_window_in_force_at_execution(self):
        claim = self.settled()
        at_execution = claim["protection_at_execution"]
        self.assertTrue(at_execution["ordered"])
        self.assertEqual(at_execution["executed_at"], INSIDE)
        self.assertTrue(at_execution["protection_active"])
        self.assertTrue(at_execution["recorded_at_execution"])
        self.assertIsNotNone(at_execution["activation_record_id"])

    def test_the_window_at_execution_survives_the_window_closing(self):
        """W4. The window closed on 2026-09-10 and the event is inside it, so
        reading the claim today gives an inactive read-time block. The block for
        the moment of the event does not move with it."""
        late = self.settled()
        self.assertFalse(late["protection"]["protection_active"])
        self.assertTrue(late["protection_at_execution"]["protection_active"])
        self.assertTrue(late["covered"])
        reread = self.store.claim_for("t-window")
        self.assertTrue(reread["protection_at_execution"]["protection_active"])

    def test_an_event_outside_the_window_is_not_dressed_in_a_live_block(self):
        """W5. Executed 2026-09-20, after the window closed. The terms are then
        renewed, so the read-time block is live again today - and the claim for
        an event that was never covered must not borrow it."""
        self.settled(task_id="t-outside", executed_at=AFTER_EXPIRY)
        self.root.joinpath(f"{PV}.json").write_text(
            json.dumps(records(expires_at=STILL_OPEN)))
        claim = self.store.claim_for("t-outside")
        self.assertTrue(claim["protection"]["protection_active"])
        self.assertFalse(claim["protection_at_execution"]["protection_active"])
        self.assertTrue(claim["covered"])

    def test_the_verdict_never_moves_with_the_window(self):
        """A10 stays intact: coverage is not what decides the claim."""
        self.settled(task_id="t-verdict")
        inside = self.store.claim_for("t-verdict")
        self.root.joinpath(f"{PV}.json").write_text(
            json.dumps(records(expires_at=STILL_OPEN)))
        after = self.store.claim_for("t-verdict")
        for field in ("claim", "covered", "outcome_state", "amount_at_risk_cents"):
            self.assertEqual(inside[field], after[field], field)

    def test_a_row_written_before_the_column_is_recomputed_and_says_so(self):
        claim = self.settled(task_id="t-legacy")
        self.assertTrue(claim["protection_at_execution"]["recorded_at_execution"])
        with self.store.db:
            self.store.db.execute(
                "UPDATE approval_receipts SET protection_at_execution_json = NULL WHERE task_id = ?",
                ("t-legacy",))
        legacy = self.store.claim_for("t-legacy")
        self.assertFalse(legacy["protection_at_execution"]["recorded_at_execution"])
        self.assertTrue(legacy["protection_at_execution"]["protection_active"])

    def test_an_unorderable_execution_time_says_so_rather_than_picking_a_side(self):
        claim = self.settled(task_id="t-unzoned", executed_at="2026-09-05T09:41:00")
        at_execution = claim["protection_at_execution"]
        self.assertFalse(at_execution["ordered"])
        self.assertEqual(at_execution["reason"], "executed_at_not_orderable")
        self.assertIsNone(at_execution["protection_active"])


if __name__ == "__main__":
    unittest.main()
