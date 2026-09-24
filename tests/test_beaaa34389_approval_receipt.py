"""Path 1 approval receipt, Founder ruling Addendum 2 on BEAAA-34387.

One test per numbered requirement, plus the claim matrix the receipt has to
support. Fixture-based throughout: no mailbox, no connector, no real agent.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from stage_a_demo.core import EvidenceStore, SyntheticMerchantAdapter
from stage_a_demo.receipt import (
    ANSWER_OK,
    ANSWER_SKIPPED,
    ANSWER_STOP,
    ANSWER_UNANSWERED,
    CLAIM_COVERED_EXECUTED_AGAINST_STOP,
    CLAIM_COVERED_NO_APPROVAL,
    CLAIM_COVERED_OUTCOME_DIFFERS,
    CLAIM_INCOMPLETE_NO_INDEPENDENT_EVIDENCE,
    CLAIM_NOT_COVERED_APPROVED,
    EVIDENCE_SOURCE_FIXTURE,
    WARNING_NON_RECOVERABLE,
    WARNING_OVER_LIMIT,
    ApprovalReceipt,
    CheckNotApproved,
    ExecutedOutcome,
    ProposedAction,
    assess_warnings,
)

# The action from the 2026-09-24 Instinct run, as a fixture.
ESTER = ProposedAction(
    description="Hotel Ester, 3 nights, 12-15 Oct, 2 guests",
    merchant="bookings.example",
    amount_cents=48_000,
    deposit_cents=12_000,
    cancellation_terms="Deposit non-refundable after 24h; balance refundable to 7 days before arrival",
)
MONA = ProposedAction(
    description="Hotel Mona, 3 nights, 12-15 Oct, 2 guests",
    merchant="bookings.example",
    amount_cents=51_000,
    deposit_cents=12_000,
    cancellation_terms="Deposit non-refundable after 24h",
)


def receipt(task_id, action=ESTER, answer=ANSWER_OK, limit_cents=None, **kw):
    answered = None if answer in (ANSWER_SKIPPED, ANSWER_UNANSWERED) else "2026-09-24T01:09:00Z"
    return ApprovalReceipt(
        receipt_id=f"receipt-{task_id}",
        task_id=task_id,
        proposed_action=action,
        answer=answer,
        checked_at="2026-09-24T01:07:00Z",
        answered_at=answered,
        recorded_at="2026-09-24T01:09:05Z",
        limit_cents=limit_cents,
        warnings=assess_warnings(action, limit_cents),
        **kw,
    )


class ReceiptShapeTests(unittest.TestCase):
    def test_1_the_check_carries_the_exact_action_price_deposit_and_terms(self):
        r = receipt("t1")
        action = r.proposed_action
        self.assertEqual("Hotel Ester, 3 nights, 12-15 Oct, 2 guests", action.description)
        self.assertEqual(48_000, action.amount_cents)
        self.assertEqual(12_000, action.deposit_cents)
        self.assertIn("non-refundable", action.cancellation_terms)
        self.assertEqual(EVIDENCE_SOURCE_FIXTURE, r.evidence_source)

    def test_2_one_answer_from_the_user_and_it_is_timestamped(self):
        r = receipt("t2")
        self.assertEqual(ANSWER_OK, r.answer)
        self.assertTrue(r.approved)
        self.assertEqual("2026-09-24T01:07:00Z", r.checked_at)
        self.assertEqual("2026-09-24T01:09:00Z", r.answered_at)
        # An OK with no time it was given is not a record of an answer.
        with self.assertRaises(ValueError):
            ApprovalReceipt("r", "t", ESTER, ANSWER_OK, "2026-09-24T01:07:00Z", None, "2026-09-24T01:09:05Z")
        # And an unanswered check cannot carry one.
        with self.assertRaises(ValueError):
            ApprovalReceipt("r", "t", ESTER, ANSWER_UNANSWERED, "2026-09-24T01:07:00Z",
                            "2026-09-24T01:09:00Z", "2026-09-24T01:09:05Z")

    def test_5_two_judgments_only_and_both_are_warnings(self):
        refundable = ProposedAction("Refundable room", "bookings.example", 10_000, 0, "Free cancellation")
        self.assertEqual([], assess_warnings(refundable, limit_cents=50_000))
        self.assertEqual([WARNING_NON_RECOVERABLE], assess_warnings(ESTER, limit_cents=None))
        self.assertEqual([WARNING_OVER_LIMIT], assess_warnings(refundable, limit_cents=5_000))
        self.assertEqual([WARNING_NON_RECOVERABLE, WARNING_OVER_LIMIT], assess_warnings(ESTER, 40_000))
        # A warning is not a veto: the over-limit, non-recoverable action still
        # executes when the user says OK.
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp) / "e.sqlite3")
            store.record_pre_execution_receipt(receipt("t5", limit_cents=40_000))
            review = store.execute_approved_task(
                "t5", merchant=SyntheticMerchantAdapter(), executed_at="2026-09-24T01:10:00Z")
            self.assertEqual(CLAIM_NOT_COVERED_APPROVED, review["claim"])
            self.assertEqual([WARNING_NON_RECOVERABLE, WARNING_OVER_LIMIT], review["warnings"])
            store.close()

    def test_amount_at_risk_is_the_deposit_or_the_whole_non_recoverable_amount(self):
        self.assertEqual(12_000, receipt("t").amount_at_risk_cents)
        whole = ProposedAction("Non-refundable fare", "air.example", 90_000, 0, "No refunds", non_recoverable=True)
        self.assertEqual(90_000, receipt("t", action=whole).amount_at_risk_cents)


class ReceiptIsWrittenBeforeExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EvidenceStore(Path(self.tmp.name) / "evidence.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_3_execution_without_a_receipt_refuses(self):
        with self.assertRaises(CheckNotApproved):
            self.store.execute_approved_task(
                "never-checked", merchant=SyntheticMerchantAdapter(), executed_at="2026-09-24T01:10:00Z")

    def test_3_a_receipt_is_not_rewritten_after_the_fact(self):
        self.store.record_pre_execution_receipt(receipt("t3"))
        with self.assertRaises(PermissionError):
            self.store.record_pre_execution_receipt(receipt("t3", action=MONA))
        self.assertEqual(ESTER.description, self.store.receipt_for("t3").proposed_action.description)

    def test_6_stop_skipped_and_unanswered_all_refuse_execution(self):
        for answer in (ANSWER_STOP, ANSWER_SKIPPED, ANSWER_UNANSWERED):
            task = f"t6-{answer}"
            self.store.record_pre_execution_receipt(receipt(task, answer=answer))
            with self.assertRaises(CheckNotApproved):
                self.store.execute_approved_task(
                    task, merchant=SyntheticMerchantAdapter(), executed_at="2026-09-24T01:10:00Z")
            # The refusal is on the record, not silently dropped.
            self.assertEqual(answer, self.store.claim_for(task)["answer"])

    def test_the_merchant_is_never_reached_without_an_ok(self):
        class CountingMerchant(SyntheticMerchantAdapter):
            calls = 0

            def purchase(self, **kw):
                type(self).calls += 1
                return super().purchase(**kw)

        merchant = CountingMerchant()
        self.store.record_pre_execution_receipt(receipt("t-stop", answer=ANSWER_STOP))
        with self.assertRaises(CheckNotApproved):
            self.store.execute_approved_task("t-stop", merchant=merchant, executed_at="2026-09-24T01:10:00Z")
        self.assertEqual(0, CountingMerchant.calls)


class ClaimMatrixTests(unittest.TestCase):
    """4. Outcome matching runs against the approved action, never a mandate."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EvidenceStore(Path(self.tmp.name) / "evidence.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def review(self, task, answer, outcome):
        self.store.record_pre_execution_receipt(receipt(task, answer=answer))
        return self.store.record_outcome(task, outcome, executed_at="2026-09-24T01:41:00Z")

    def outcome(self, action=ESTER, **kw):
        base = dict(description=action.description, merchant=action.merchant,
                    amount_cents=action.amount_cents, evidence_ref="conf-1")
        base.update(kw)
        return ExecutedOutcome(**base)

    def test_approved_x_and_x_happened_is_not_covered(self):
        review = self.review("m1", ANSWER_OK, self.outcome())
        self.assertEqual(CLAIM_NOT_COVERED_APPROVED, review["claim"])
        self.assertFalse(review["covered"])
        self.assertEqual("MATCH", review["outcome_state"])
        self.assertEqual([], review["differences"])

    def test_approved_x_and_y_happened_is_a_covered_event(self):
        review = self.review("m2", ANSWER_OK, self.outcome(description=MONA.description, amount_cents=51_000))
        self.assertEqual(CLAIM_COVERED_OUTCOME_DIFFERS, review["claim"])
        self.assertTrue(review["covered"])
        self.assertEqual(
            [{"field": "description", "approved": ESTER.description, "outcome": MONA.description},
             {"field": "amount_cents", "approved": 48_000, "outcome": 51_000}],
            review["differences"],
        )
        # Covered to the extent the money cannot be got back.
        self.assertEqual(12_000, review["amount_at_risk_cents"])

    def test_a_fully_refundable_mismatch_is_recorded_but_not_covered(self):
        """Section 2: refundable purchase errors stay in the demo as evidence, not as the offer."""
        refundable = ProposedAction("Refundable room", "bookings.example", 10_000, 0, "Free cancellation")
        self.store.record_pre_execution_receipt(receipt("m3", action=refundable))
        review = self.store.record_outcome(
            "m3", ExecutedOutcome("Different room", "bookings.example", 11_000),
            executed_at="2026-09-24T01:41:00Z")
        self.assertEqual(CLAIM_COVERED_OUTCOME_DIFFERS, review["claim"])
        self.assertFalse(review["covered"])
        self.assertEqual(0, review["amount_at_risk_cents"])

    def test_executed_against_a_stop_is_covered(self):
        review = self.review("m4", ANSWER_STOP, self.outcome())
        self.assertEqual(CLAIM_COVERED_EXECUTED_AGAINST_STOP, review["claim"])
        self.assertTrue(review["covered"])

    def test_a_skipped_or_unanswered_check_is_never_read_as_approval(self):
        for answer in (ANSWER_SKIPPED, ANSWER_UNANSWERED):
            review = self.review(f"m5-{answer}", answer, self.outcome())
            self.assertEqual(CLAIM_COVERED_NO_APPROVAL, review["claim"])
            self.assertTrue(review["covered"])
            self.assertEqual(answer, review["answer"])

    def test_no_independent_evidence_is_never_a_match(self):
        review = self.review("m6", ANSWER_OK, self.outcome(independent=False))
        self.assertEqual(CLAIM_INCOMPLETE_NO_INDEPENDENT_EVIDENCE, review["claim"])
        self.assertFalse(review["covered"])
        self.assertEqual("UNKNOWN/INCOMPLETE", review["outcome_state"])

    def test_the_false_stop_from_the_instinct_run_cannot_happen_here(self):
        """The 01:35 wrong STOP: BackBond judged Ester against its stale Mona copy.

        Nothing in this path consults a mandate. The user approved Ester and
        Ester happened, so the claim reads not-covered and no STOP is produced,
        whatever some older copy of the plan said.
        """
        review = self.review("m7", ANSWER_OK, self.outcome())
        self.assertEqual(CLAIM_NOT_COVERED_APPROVED, review["claim"])
        self.assertNotIn("mandate", review)

    def test_a_claim_reconstructs_from_the_store_alone(self):
        self.review("m8", ANSWER_OK, self.outcome(description=MONA.description))
        self.store.close()
        reopened = EvidenceStore(Path(self.tmp.name) / "evidence.sqlite3")
        try:
            review = reopened.claim_for("m8")
            self.assertEqual(CLAIM_COVERED_OUTCOME_DIFFERS, review["claim"])
            self.assertEqual("2026-09-24T01:41:00Z", review["executed_at"])
            self.assertEqual("DEMO", review["mode"])
        finally:
            reopened.close()
        self.store = reopened if False else EvidenceStore(Path(self.tmp.name) / "evidence.sqlite3")

    def test_a_receipt_never_implies_coverage(self):
        review = self.review("m9", ANSWER_OK, self.outcome())
        self.assertFalse(review["protection"]["protection_active"])


if __name__ == "__main__":
    unittest.main()
