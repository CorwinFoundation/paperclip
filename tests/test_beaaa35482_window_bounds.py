"""BEAAA-35482 X2/X7: the coverage window has a beginning, and it stays put.

X2. The activation gate compared `expires_at` against now and nothing else, so
it could tell "expired" from "live" but not "not yet in force" from "in force".
A loss that predates the programme therefore read as covered: records approved
on 2026-08-01, an execution on 2026-07-01, `protection_active=True`,
`reasons=[]`. That direction manufactures coverage rather than denying it.

X7. `record_pre_execution_receipt` refuses to overwrite a receipt; the window
pinned at execution had no equivalent guard, and `record_outcome` recomputed it
on every call. The same claim could be made to sit inside or outside the window
by re-recording it at a different `executed_at`.

These drive the real `EvidenceStore` and the real on-disk registry, so the
guard under test is the one that ships, not a reproduction of it.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from stage_a_demo.activation import (ACTIVATION_RECORDS_ENV, PROGRAM_VERSION_ENV,
                                     check_activation, FileRegistry)
from stage_a_demo.core import EvidenceStore
from stage_a_demo.receipt import (ANSWER_OK, ApprovalReceipt, ExecutedOutcome,
                                  ProposedAction)

PV = "stage-a-bounds-test"
APPROVED_AT = "2026-08-01T00:00:00Z"
EXPIRY = "2026-09-10T00:00:00Z"
INSIDE = "2026-09-05T09:41:00Z"
BEFORE_ACTIVATION = "2026-07-01T09:41:00Z"   # 31 days before anything was approved

ACTION = ProposedAction(description="Hotel Ester, 12-15 Sep, 2 guests",
                        merchant="bookings.example", amount_cents=48_000,
                        non_recoverable=True, cancellation_terms="non-refundable")
OUTCOME = ExecutedOutcome("Hotel Ester, 19-22 Sep, 2 guests", "bookings.example", 48_000,
                          evidence_ref="confirmation-wrong-dates", observed_at=INSIDE)


def records(**overrides):
    base = {
        "governing_legal_structure": {"record_id": "r1", "program_version": PV,
            "status": "in_good_standing", "entity_name": "BackBond", "jurisdiction": "DE"},
        "executed_backing_authority": {"record_id": "r2", "program_version": PV, "status": "executed",
            "counterparty": "carrier", "executed_at": APPROVED_AT, "document_sha256": "a" * 64},
        "funded_financial_security": {"record_id": "r3", "program_version": PV, "status": "funded",
            "instrument": "trust", "funded_amount_cents": 1_000_000, "funded_at": APPROVED_AT},
        "approved_versioned_terms": {"record_id": "r4", "program_version": PV, "status": "approved",
            "terms_version": "1.0", "approved_at": APPROVED_AT, "expires_at": EXPIRY},
        "named_claims_and_payment_humans": {"record_id": "r5", "program_version": PV, "status": "named",
            "claims_reviewer": "a human", "payment_approver": "another human"},
        "approved_consumer_disclosure_set": {"record_id": "r6", "program_version": PV, "status": "approved",
            "disclosure_set_id": "d1", "approved_at": APPROVED_AT},
    }
    for key, patch in overrides.items():
        base[key] = {**base[key], **patch}
    return base


def at(moment):
    return datetime.fromisoformat(moment.replace("Z", "+00:00")).astimezone(timezone.utc)


class RegistryFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "activation"
        self.root.mkdir()
        self.write(records())
        self.env = mock.patch.dict(os.environ, {ACTIVATION_RECORDS_ENV: str(self.root),
                                                PROGRAM_VERSION_ENV: PV})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write(self, payload):
        (self.root / f"{PV}.json").write_text(json.dumps(payload))

    def status(self, moment):
        return check_activation(PV, FileRegistry(str(self.root)), now=at(moment))


class TheWindowHasABeginning(RegistryFixture):
    """X2."""

    def test_an_instant_before_activation_is_not_covered(self):
        status = self.status(BEFORE_ACTIVATION)
        self.assertFalse(status.active)
        self.assertIn("approved_versioned_terms:not_yet_in_force", status.reasons)
        self.assertIn("executed_backing_authority:not_yet_in_force", status.reasons)
        self.assertIn("funded_financial_security:not_yet_in_force", status.reasons)
        self.assertIn("approved_consumer_disclosure_set:not_yet_in_force", status.reasons)

    def test_the_gate_still_holds_inside_the_window(self):
        status = self.status(INSIDE)
        self.assertTrue(status.active, status.reasons)
        self.assertEqual(status.reasons, ())

    def test_the_instant_a_record_comes_into_force_is_covered(self):
        """The boundary belongs to the window: at `approved_at` exactly, the
        record is in force. Only strictly-before is not yet."""
        self.assertTrue(self.status(APPROVED_AT).active, self.status(APPROVED_AT).reasons)

    def test_the_end_bound_still_holds(self):
        status = self.status("2026-09-20T00:00:00Z")
        self.assertFalse(status.active)
        self.assertIn("approved_versioned_terms:expired", status.reasons)

    def test_one_late_record_is_enough_to_close_the_gate(self):
        """Funding that arrived after the loss cannot back the loss."""
        self.write(records(funded_financial_security={"funded_at": "2026-09-08T00:00:00Z"}))
        status = self.status(INSIDE)
        self.assertFalse(status.active)
        self.assertEqual([r for r in status.reasons if "funded" in r],
                         ["funded_financial_security:not_yet_in_force"])

    def test_a_start_that_cannot_be_ordered_is_not_leaned_either_way(self):
        self.write(records(approved_versioned_terms={"approved_at": "whenever"}))
        status = self.status(INSIDE)
        self.assertFalse(status.active)
        self.assertIn("approved_versioned_terms:malformed_start", status.reasons)


class ThePinnedWindowIsNotRewritten(RegistryFixture):
    """X7."""

    def setUp(self):
        super().setUp()
        self.store = EvidenceStore(Path(self.tmp.name) / "evidence.sqlite3")
        self.addCleanup(self.store.close)

    def settle(self, task_id="t-bounds", executed_at=INSIDE):
        self.store.record_pre_execution_receipt(ApprovalReceipt(
            "r-bounds", task_id, ACTION, ANSWER_OK,
            "2026-09-05T09:07:00Z", "2026-09-05T09:09:00Z", "2026-09-05T09:09:05Z"))
        return self.store.record_outcome(task_id, OUTCOME, executed_at=executed_at)

    def test_re_recording_the_same_task_is_refused(self):
        self.settle()
        with self.assertRaises(PermissionError) as raised:
            self.store.record_outcome("t-bounds", OUTCOME, executed_at="2026-09-20T00:00:00Z")
        self.assertIn("not rewritten", str(raised.exception))

    def test_the_refused_rewrite_leaves_the_stored_window_alone(self):
        first = self.settle()
        self.assertTrue(first["protection_at_execution"]["protection_active"])
        with self.assertRaises(PermissionError):
            self.store.record_outcome("t-bounds", OUTCOME, executed_at="2026-09-20T00:00:00Z")
        after = self.store.claim_for("t-bounds")
        self.assertEqual(after["executed_at"], INSIDE)
        self.assertTrue(after["protection_at_execution"]["protection_active"])
        self.assertTrue(after["protection_at_execution"]["recorded_at_execution"])

    def test_a_loss_before_activation_is_pinned_as_not_covered(self):
        """X2 and X7 together: the direction that manufactured coverage, on the
        stored block a claim is judged against."""
        claim = self.settle(task_id="t-early", executed_at=BEFORE_ACTIVATION)
        pinned = claim["protection_at_execution"]
        self.assertTrue(pinned["ordered"])
        self.assertFalse(pinned["protection_active"])
        self.assertIn("approved_versioned_terms:not_yet_in_force", pinned["reasons"])


if __name__ == "__main__":
    unittest.main()
