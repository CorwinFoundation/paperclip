"""BEAAA-35456: the three denial-direction defects the BEAAA-34396 seam probes
found at master f460358d, each pinned by the case that used to deny a real loss.

Every case here settles the same missed deadline. Before the fix each one read
as no loss at all, for a different reason: a label on the money, a flag left
unset, or a deadline we ourselves failed to capture with a zone.
"""
import unittest

from stage_a_demo.receipt import (
    ANSWER_OK,
    CLAIM_COVERED_MISSED_CANCELLATION_DEADLINE,
    CLAIM_COVERED_OUTCOME_DIFFERS,
    CLAIM_INCOMPLETE_NO_CAPTURED_AMOUNT_AT_RISK,
    CLAIM_INCOMPLETE_NO_ORDERABLE_OBSERVATION,
    ApprovalReceipt,
    ExecutedOutcome,
    ProposedAction,
    amount_at_risk_cents,
    captured_deadline_instant,
    review_claim,
)

DEADLINE = "2026-10-11T12:00:00Z"
DEPOSIT = 40_000


def action(**kw):
    base = dict(description="Cancel the Ester booking before the refund deadline",
                merchant="bookings.example", amount_cents=0, deposit_cents=DEPOSIT,
                cancellation_terms="deposit refundable until 2026-10-11T12:00:00Z",
                cancellation_deadline=DEADLINE, terms_captured_at="2026-10-10T09:00:00Z",
                terms_evidence_ref="terms-ester", is_cancellation_obligation=True)
    base.update(kw)
    return ProposedAction(**base)


def outcome(**kw):
    base = dict(description="Cancel the Ester booking before the refund deadline",
                merchant="bookings.example", amount_cents=0,
                evidence_ref="no-cancellation-in-inbox", performed=False,
                observed_at="2026-10-11T12:30:00Z")
    base.update(kw)
    return ExecutedOutcome(**base)


def settle(act, out):
    receipt = ApprovalReceipt("r-test", "t-test", act, ANSWER_OK,
                              "2026-10-10T09:00:00Z", "2026-10-10T09:05:00Z",
                              "2026-10-10T09:05:05Z")
    return review_claim(receipt, out)


class NonRecoverableLabelNeverLowersTheRisk(unittest.TestCase):
    """S1-2. The whole point of the label is that the money is harder to get
    back, so it can never be the reason less of it is at risk."""

    def test_labelled_deposit_keeps_the_same_exposure(self):
        self.assertEqual(amount_at_risk_cents(action()), DEPOSIT)
        self.assertEqual(amount_at_risk_cents(action(non_recoverable=True)), DEPOSIT)

    def test_labelled_deposit_still_settles_the_missed_deadline(self):
        claim = settle(action(non_recoverable=True), outcome())
        self.assertEqual(claim["claim"], CLAIM_COVERED_MISSED_CANCELLATION_DEADLINE)
        self.assertTrue(claim["covered"])
        self.assertEqual(claim["amount_at_risk_cents"], DEPOSIT)

    def test_a_real_non_recoverable_purchase_is_unchanged(self):
        purchase = ProposedAction(description="Buy the non-refundable fare",
                                  merchant="air.example", amount_cents=90_000,
                                  deposit_cents=0, non_recoverable=True)
        self.assertEqual(amount_at_risk_cents(purchase), 90_000)


class TheObligationFlagIsSelfAttested(unittest.TestCase):
    """S1-3. Nothing cross-checked the flag against an action that costs
    nothing, carries a captured deadline and sits on a deposit."""

    def test_flag_unset_still_reads_as_an_obligation(self):
        self.assertTrue(action(is_cancellation_obligation=False).is_delegated_cancellation)

    def test_flag_unset_does_not_settle_as_an_approved_non_loss(self):
        claim = settle(action(is_cancellation_obligation=False), outcome())
        self.assertEqual(claim["claim"], CLAIM_COVERED_MISSED_CANCELLATION_DEADLINE)
        self.assertTrue(claim["covered"])
        self.assertEqual(claim["amount_at_risk_cents"], DEPOSIT)

    def test_a_mismatch_is_not_swallowed_by_the_omission_path(self):
        """BEAAA-34396 R4. The derived reading routed any zero-cost action with
        a deadline and a deposit through the omission path, which is entered
        before the approved-versus-executed comparison. A hold delivered on the
        wrong dates then settled as self-reported performance instead of as the
        mismatch it is."""
        hold = action(is_cancellation_obligation=False,
                      description="Hold Hotel Ester, 12-15 Oct, 2 guests",
                      cancellation_terms="pay at property, deposit held")
        delivered = ExecutedOutcome("Hotel Ester, 19-22 Oct, 2 guests", "bookings.example", 0,
                                    evidence_ref="confirmation-wrong-dates", performed=True,
                                    observed_at="2026-10-11T09:41:00Z")
        claim = settle(hold, delivered)
        self.assertEqual(claim["claim"], CLAIM_COVERED_OUTCOME_DIFFERS)
        self.assertTrue(claim["covered"])
        self.assertEqual(claim["amount_at_risk_cents"], DEPOSIT)

    def test_an_ordinary_purchase_is_not_swept_in(self):
        purchase = ProposedAction(description="Buy the thing", merchant="shop.example",
                                  amount_cents=12_000, deposit_cents=2_000,
                                  cancellation_deadline=DEADLINE)
        self.assertFalse(purchase.is_delegated_cancellation)
        self.assertEqual(amount_at_risk_cents(purchase), 2_000)


class AnUnorderableDeadlineIsOurDefect(unittest.TestCase):
    """S2-5. A deadline captured without a zone is a capture defect, and a
    capture defect of ours never resolves against the user."""

    def test_zone_less_deadline_is_named_at_capture(self):
        self.assertEqual(action(cancellation_deadline="2026-10-11T12:00:00").deadline_capture_defect,
                         "cancellation_deadline_not_orderable")
        self.assertIsNone(action().deadline_capture_defect)

    def test_plainly_past_observation_still_settles_the_loss(self):
        claim = settle(action(cancellation_deadline="2026-10-11T12:00:00"),
                       outcome(observed_at="2026-10-11T23:00:00Z"))
        self.assertEqual(claim["claim"], CLAIM_COVERED_MISSED_CANCELLATION_DEADLINE)
        self.assertTrue(claim["covered"])
        self.assertIn("cancellation_deadline_not_orderable", claim["record_defects"])

    def test_an_observation_before_the_deadline_as_written_is_not_a_loss(self):
        claim = settle(action(cancellation_deadline="2026-10-11T12:00:00"),
                       outcome(observed_at="2026-10-10T21:00:00Z"))
        self.assertFalse(claim["covered"])

    def test_the_deadline_is_read_as_written_and_not_shifted_earlier(self):
        """BEAAA-34396 R1. Reading a zone-less deadline at the earliest instant
        it could denote turned a fourteen-hour band before the captured stamp
        into covered losses, on bookings still inside their window."""
        self.assertEqual(captured_deadline_instant("2026-10-11T12:00:00").isoformat(),
                         "2026-10-11T12:00:00+00:00")
        claim = settle(action(cancellation_deadline="2026-10-11T12:00:00"),
                       outcome(observed_at="2026-10-11T00:00:00Z"))
        self.assertFalse(claim["covered"])
        self.assertIn("cancellation_deadline_not_orderable", claim["record_defects"])

    def test_a_deadline_that_is_prose_is_never_guessed_into_an_instant(self):
        self.assertIsNone(captured_deadline_instant("before the end of the week"))
        claim = settle(action(cancellation_deadline="before the end of the week"),
                       outcome(observed_at="2026-10-11T23:00:00Z"))
        self.assertEqual(claim["claim"], CLAIM_INCOMPLETE_NO_ORDERABLE_OBSERVATION)
        self.assertFalse(claim["covered"])

    def test_a_zone_less_observation_is_still_nothing_observed(self):
        claim = settle(action(), outcome(observed_at="2026-10-11T13:30:00"))
        self.assertEqual(claim["claim"], CLAIM_INCOMPLETE_NO_ORDERABLE_OBSERVATION)
        self.assertFalse(claim["covered"])


class AClaimStringMatchesItsOwnVerdict(unittest.TestCase):
    """The label was chosen before the money was, so a missed deadline with
    nothing captured to lose read 'covered_...' next to covered=False."""

    def test_no_captured_deposit_does_not_read_as_a_covered_loss(self):
        claim = settle(action(deposit_cents=0), outcome())
        self.assertEqual(claim["claim"], CLAIM_INCOMPLETE_NO_CAPTURED_AMOUNT_AT_RISK)
        self.assertFalse(claim["covered"])
        self.assertIn("amount_at_risk_not_captured", claim["record_defects"])

    def test_every_covered_claim_string_carries_a_true_verdict(self):
        for act in (action(), action(non_recoverable=True), action(is_cancellation_obligation=False)):
            with self.subTest(action=act.description, flag=act.is_cancellation_obligation):
                claim = settle(act, outcome())
                self.assertEqual(claim["claim"].startswith("covered_"), claim["covered"])


if __name__ == "__main__":
    unittest.main()
