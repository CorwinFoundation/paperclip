"""The two non-recoverable-loss cases, Founder ruling 2026-09-24 section 2.

The covered event is money the user cannot get back: a non-refundable booking
executed with wrong details, and a missed cancellation deadline on a delegated
obligation. The cancellation terms are captured as independent evidence at
delegation time, not reconstructed after the loss.

Refundable mistakes stay in the demo as evidence cases and are explicitly not
the offer, so this file pins the boundary in both directions.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from stage_a_demo import path1
from stage_a_demo.core import EvidenceStore
from stage_a_demo.receipt import (
    ANSWER_OK,
    CLAIM_COVERED_MISSED_CANCELLATION_DEADLINE,
    CLAIM_COVERED_OUTCOME_DIFFERS,
    CLAIM_NOT_COVERED_APPROVED,
    CLAIM_NOT_YET_A_LOSS_WITHIN_CANCELLATION_WINDOW,
    WARNING_NON_RECOVERABLE,
    ApprovalReceipt,
    ExecutedOutcome,
    ProposedAction,
    assess_warnings,
    review_claim,
)


class NonRecoverableLossTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reviews = {r["task_id"]: r for r in path1.run_path1_cases(Path(self.tmp.name) / "p1.sqlite3")}

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_non_refundable_booking_with_wrong_details_is_a_covered_event(self):
        review = self.reviews["non-refundable-mismatch"]
        self.assertEqual(CLAIM_COVERED_OUTCOME_DIFFERS, review["claim"])
        self.assertTrue(review["covered"])
        # Non-refundable, so the whole amount is what the user cannot get back.
        self.assertEqual(48_000, review["amount_at_risk_cents"])
        self.assertEqual(
            [{"field": "description",
              "approved": "Hotel Ester, 12-15 Oct, 2 guests",
              "outcome": "Hotel Ester, 19-22 Oct, 2 guests"}],
            review["differences"],
        )

    def test_a_missed_cancellation_deadline_is_a_covered_event(self):
        review = self.reviews["missed-cancellation-deadline"]
        self.assertEqual(CLAIM_COVERED_MISSED_CANCELLATION_DEADLINE, review["claim"])
        self.assertTrue(review["covered"])
        self.assertEqual("NOT_PERFORMED", review["outcome_state"])
        # Cancelling costs nothing; the deposit is what stops being recoverable.
        self.assertEqual(12_000, review["amount_at_risk_cents"])

    def test_the_same_omission_inside_the_window_is_not_yet_a_loss(self):
        review = self.reviews["cancellation-still-in-window"]
        self.assertEqual(CLAIM_NOT_YET_A_LOSS_WITHIN_CANCELLATION_WINDOW, review["claim"])
        self.assertFalse(review["covered"])
        self.assertEqual("NOT_PERFORMED", review["outcome_state"])

    def test_the_cancellation_terms_are_evidence_captured_at_delegation_time(self):
        review = self.reviews["missed-cancellation-deadline"]
        terms = review["cancellation_terms_evidence"]
        self.assertEqual("2026-10-11T12:00:00Z", terms["deadline"])
        self.assertEqual(12_000, terms["deposit_cents"])
        self.assertEqual("booking-ester-terms-1", terms["evidence_ref"])
        # Captured before the deadline it describes, and before the loss.
        self.assertLess(terms["captured_at"], terms["deadline"])
        self.assertLess(terms["captured_at"], review["executed_at"])

    def test_a_correct_booking_is_not_covered_and_shows_the_users_own_approval(self):
        review = self.reviews["clean-booking"]
        self.assertEqual(CLAIM_NOT_COVERED_APPROVED, review["claim"])
        self.assertFalse(review["covered"])
        self.assertEqual(ANSWER_OK, review["answer"])

    def test_a_refundable_mistake_is_recorded_and_is_not_the_offer(self):
        """Section 2: refundable errors stay as evidence cases, not covered events."""
        refundable = ProposedAction(
            description="Refundable room, 12-15 Oct", merchant="bookings.example",
            amount_cents=30_000, deposit_cents=0,
            cancellation_terms="Free cancellation until arrival.",
            terms_captured_at="2026-10-01T09:07:00Z", terms_evidence_ref="quote-flex-1")
        receipt = ApprovalReceipt(
            receipt_id="r", task_id="t", proposed_action=refundable, answer=ANSWER_OK,
            checked_at="2026-10-01T09:07:00Z", answered_at="2026-10-01T09:09:00Z",
            recorded_at="2026-10-01T09:09:05Z", limit_cents=40_000,
            warnings=assess_warnings(refundable, 40_000))
        review = review_claim(receipt, ExecutedOutcome(
            "Refundable room, 19-22 Oct", "bookings.example", 30_000,
            evidence_ref="conf-flex", observed_at="2026-10-01T09:41:00Z"))
        self.assertEqual(CLAIM_COVERED_OUTCOME_DIFFERS, review["claim"])
        self.assertFalse(review["covered"])
        self.assertEqual(0, review["amount_at_risk_cents"])
        self.assertNotIn(WARNING_NON_RECOVERABLE, review["warnings"])

    def test_a_covered_event_still_never_implies_an_active_program(self):
        for task_id in ("non-refundable-mismatch", "missed-cancellation-deadline"):
            self.assertFalse(self.reviews[task_id]["protection"]["protection_active"])

    def test_every_claim_reconstructs_from_the_store_alone(self):
        db = Path(self.tmp.name) / "reopened.sqlite3"
        path1.run_path1_cases(db)
        store = EvidenceStore(db)
        try:
            for task_id, _, _ in path1.CASES:
                self.assertEqual(task_id, store.claim_for(task_id)["task_id"])
        finally:
            store.close()


class EntrypointTests(unittest.TestCase):
    def test_the_runnable_demo_emits_both_non_recoverable_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, "-m", "stage_a_demo", "--db", str(Path(tmp) / "demo.sqlite3")],
                capture_output=True, text=True, timeout=120,
                cwd=str(Path(__file__).resolve().parent.parent))
            self.assertEqual(0, proc.returncode, proc.stderr)
            payload = json.loads(proc.stdout)
            claims = {r["task_id"]: r["claim"] for r in payload["path1_cases"]}
            self.assertEqual(CLAIM_COVERED_OUTCOME_DIFFERS, claims["non-refundable-mismatch"])
            self.assertEqual(
                CLAIM_COVERED_MISSED_CANCELLATION_DEADLINE, claims["missed-cancellation-deadline"])


if __name__ == "__main__":
    unittest.main()
