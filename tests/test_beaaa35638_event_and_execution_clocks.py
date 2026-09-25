"""BEAAA-35638: one claim, one event instant, or a named disagreement.

Round twelve on BEAAA-34396 found two clocks reaching one claim payload by two
routes with nothing reconciling them. `review_claim` took its `event_at` from
`event_instant`, which prefers the independent evidence's `observed_at`;
`_protection_in_force_at` pinned the coverage window against the `executed_at`
the execution side passed in. So the party the record is meant to be
independent of chose which window judged its loss, in both directions:

V1  executed_at inside the window, observed_at four days after it closed ->
    `protection_active=True`, and the payload showed a live window for an event
    the independent record places outside it.
V3  the honest-user direction. executed_at outside, observed_at inside ->
    `protection_active=False`, so a covered loss lost cover to someone else's
    timestamp.
V2  the structural form: `claim event_at` and `at_execution event_at` disagreed
    in one payload with `reason=None`, which is the two-unrelated-facts shape
    BEAAA-35636 set out to remove, one layer down.
V4  the library-level form. `review_claim` carried a caller-asserted block
    verbatim, so `executed_at=1999-01-01` became the claim's own `event_at`.

Neither clock governs alone. Preferring the independent stamp would read a loss
the execution side places before the programme existed as covered, which is the
BEAAA-35482 X2 direction arriving through this door. So the window must come out
the same against both instants or the claim reports no window and names the
disagreement, and the claim's `event_at` is decided in one place.
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
                                  ProposedAction, event_instant, review_claim,
                                  same_instant)

ANSWERED = "2026-09-05T09:00:00Z"
ACTION = ProposedAction(description="Hotel Ester, 12-15 Sep", merchant="bookings.example",
                        amount_cents=48_000, non_recoverable=True,
                        cancellation_terms="non-refundable")


def receipt():
    return ApprovalReceipt(task_id="t-1", receipt_id="r-1", proposed_action=ACTION,
                           answer=ANSWER_OK, answered_at=ANSWERED,
                           recorded_at=ANSWERED, checked_at=ANSWERED, evidence_source="thread")


class TheClaimsEventInstantIsDecidedOnce(unittest.TestCase):

    def test_the_execution_clock_names_the_event_when_nothing_independent_does(self):
        """The receipt is written before the act, so its stamps describe the
        approval. `executed_at` on the record is the better statement."""
        outcome = ExecutedOutcome("Hotel Ester", "bookings.example", 48_000,
                                  evidence_ref="conf-1")
        self.assertEqual(event_instant(receipt(), outcome, "2026-09-20T00:00:00Z"),
                         "2026-09-20T00:00:00Z")

    def test_independent_evidence_outranks_the_execution_clock(self):
        outcome = ExecutedOutcome("Hotel Ester", "bookings.example", 48_000,
                                  evidence_ref="conf-1", observed_at="2026-09-21T00:00:00Z")
        self.assertEqual(event_instant(receipt(), outcome, "2026-09-20T00:00:00Z"),
                         "2026-09-21T00:00:00Z")

    def test_unorderable_stamps_are_never_equal_to_anything(self):
        """V2's comparison has to be on instants. An un-zoned stamp denotes a
        different instant in every offset, so it agrees with nothing."""
        self.assertTrue(same_instant("2026-09-05T09:00:00Z", "2026-09-05T04:00:00-05:00"))
        self.assertFalse(same_instant("2026-09-05T09:00:00", "2026-09-05T09:00:00"))
        self.assertFalse(same_instant(None, None))


class ACallerAssertedWindowCannotNameTheEvent(unittest.TestCase):
    """V4. Needs no store: this is `review_claim` on its own."""

    def setUp(self):
        self.supplied = {"ordered": True, "protection_active": True,
                         "executed_at": "1999-01-01T00:00:00Z",
                         "activation_record_id": "r-asserted"}

    def test_the_asserted_instant_does_not_become_the_claims_event_at(self):
        claim = review_claim(receipt(), None, protection_at_execution=self.supplied)
        block = claim["protection_at_execution"]
        self.assertEqual(block["event_at"], ANSWERED)
        self.assertEqual(block["ordered_against"], "1999-01-01T00:00:00Z")

    def test_a_window_ordered_against_another_instant_reports_no_state(self):
        claim = review_claim(receipt(), None, protection_at_execution=self.supplied)
        block = claim["protection_at_execution"]
        self.assertFalse(block["resolved"])
        self.assertIsNone(block["protection_active"])
        self.assertEqual(block["reason"],
                         "window_ordered_against_a_different_instant_than_the_event")

    def test_a_window_ordered_against_the_event_is_still_reported(self):
        """The guard refuses a disagreement, not every supplied block."""
        supplied = {**self.supplied, "executed_at": ANSWERED}
        block = review_claim(receipt(), None,
                             protection_at_execution=supplied)["protection_at_execution"]
        self.assertTrue(block["resolved"])
        self.assertTrue(block["protection_active"])

    def test_the_record_stamp_outranks_the_asserted_one(self):
        """`executed_at` comes from the row, never from inside the block."""
        block = review_claim(receipt(), None, protection_at_execution=self.supplied,
                             executed_at="2026-09-20T00:00:00Z")["protection_at_execution"]
        self.assertEqual(block["event_at"], "2026-09-20T00:00:00Z")
        self.assertIsNone(block["protection_active"])


PV = "stage-a-35638"
APPROVED_AT = "2026-08-01T00:00:00Z"
EXPIRY = "2026-09-10T00:00:00Z"
INSIDE = "2026-09-05T09:41:00Z"
AFTER_EXPIRY = "2026-09-25T00:00:00Z"
BEFORE_ACTIVATION = "2026-07-01T09:41:00Z"


def activation_records():
    return {
        "governing_legal_structure": {"record_id": "r1", "program_version": PV,
            "status": "in_good_standing", "entity_name": "BackBond", "jurisdiction": "DE"},
        "executed_backing_authority": {"record_id": "r2", "program_version": PV,
            "status": "executed", "counterparty": "carrier", "executed_at": APPROVED_AT,
            "document_sha256": "a" * 64},
        "funded_financial_security": {"record_id": "r3", "program_version": PV,
            "status": "funded", "instrument": "trust", "funded_amount_cents": 1_000_000,
            "funded_at": APPROVED_AT},
        "approved_versioned_terms": {"record_id": "r4", "program_version": PV,
            "status": "approved", "terms_version": "1.0", "approved_at": APPROVED_AT,
            "expires_at": EXPIRY},
        "named_claims_and_payment_humans": {"record_id": "r5", "program_version": PV,
            "status": "named", "claims_reviewer": "a human",
            "payment_approver": "another human"},
        "approved_consumer_disclosure_set": {"record_id": "r6", "program_version": PV,
            "status": "approved", "disclosure_set_id": "d1", "approved_at": APPROVED_AT},
    }


class NeitherClockPicksTheWindowOnItsOwn(unittest.TestCase):
    """V1 and V3 against the real store and the real on-disk registry."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "activation"
        root.mkdir()
        (root / f"{PV}.json").write_text(json.dumps(activation_records()))
        env = mock.patch.dict(os.environ, {ACTIVATION_RECORDS_ENV: str(root),
                                           PROGRAM_VERSION_ENV: PV})
        env.start()
        self.addCleanup(env.stop)

    def settle(self, task_id, *, executed_at, observed_at):
        store = EvidenceStore(str(Path(self.tmp.name) / f"{task_id}.db"))
        store.record_pre_execution_receipt(ApprovalReceipt(
            task_id=task_id, receipt_id=f"r-{task_id}", proposed_action=ACTION,
            answer=ANSWER_OK, answered_at=INSIDE, recorded_at=INSIDE, checked_at=INSIDE,
            evidence_source="thread"))
        outcome = ExecutedOutcome("Hotel Ester, 19-22 Sep", "bookings.example", 48_000,
                                  evidence_ref="conf", observed_at=observed_at)
        claim = store.record_outcome(task_id, outcome, executed_at=executed_at)
        return claim["protection_at_execution"]

    def test_the_execution_side_cannot_activate_a_window_the_record_places_outside_it(self):
        """V1, the false-activation direction. The execution clock sits inside
        the window and the independent record places the event 15 days after it
        closed, so no window is reported."""
        block = self.settle("v1", executed_at=INSIDE, observed_at=AFTER_EXPIRY)
        self.assertIsNone(block["protection_active"])
        self.assertEqual(block["reason"], "event_and_execution_clocks_straddle_the_window")
        self.assertEqual(block["event_at"], AFTER_EXPIRY)
        self.assertEqual(block["executed_at"], INSIDE)
        self.assertEqual(block["clock_disagreement"]["protection_active_at_executed_at"], True)

    def test_a_later_execution_stamp_does_not_quietly_deny_a_covered_loss(self):
        """V3, the honest-user direction. Previously `protection_active=False`
        with `reason=None`, which reads as a settled denial."""
        block = self.settle("v3", executed_at=BEFORE_ACTIVATION, observed_at=INSIDE)
        self.assertIsNone(block["protection_active"])
        self.assertEqual(block["reason"], "event_and_execution_clocks_straddle_the_window")
        self.assertIn("approved_versioned_terms:not_yet_in_force", block["reasons"])

    def test_one_instant_reports_the_window_normally(self):
        """The guard refuses a disagreement, not the ordinary path."""
        block = self.settle("agree", executed_at=INSIDE, observed_at=INSIDE)
        self.assertTrue(block["protection_active"])
        self.assertNotIn("clock_disagreement", block)


if __name__ == "__main__":
    unittest.main()
