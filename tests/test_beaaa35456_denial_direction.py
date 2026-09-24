"""BEAAA-35456: the three denial-direction defects the BEAAA-34396 seam probes
found at master f460358d, each pinned by the case that used to deny a real loss.

Every case here settles the same missed deadline. Before the fix each one read
as no loss at all, for a different reason: a label on the money, a flag left
unset, or a deadline we ourselves failed to capture with a zone.
"""
import tempfile
import unittest
from pathlib import Path

from stage_a_demo.core import EvidenceStore

from stage_a_demo.receipt import (
    ANSWER_OK,
    CLAIM_COVERED_MISSED_CANCELLATION_DEADLINE,
    CLAIM_COVERED_OUTCOME_DIFFERS,
    CLAIM_INCOMPLETE_NO_CAPTURED_AMOUNT_AT_RISK,
    CLAIM_INCOMPLETE_NO_ORDERABLE_OBSERVATION,
    ApprovalReceipt,
    ExecutedOutcome,
    ProposedAction,
    UnorderableCancellationDeadline,
    amount_at_risk_cents,
    TermsNotCaptured,
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


def make_receipt(act, task_id="t-test"):
    return ApprovalReceipt("r-test", task_id, act, ANSWER_OK,
                           "2026-10-10T09:00:00Z", "2026-10-10T09:05:00Z",
                           "2026-10-10T09:05:05Z")


def settle(act, out):
    return review_claim(make_receipt(act), out)


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


class AnUnorderableDeadlineIsRefusedAtCapture(unittest.TestCase):
    """S2-5, re-pointed by BEAAA-35480. A deadline captured without a zone was
    read at UTC and flagged as our defect. That reading is right at one offset
    and wrong across the rest of UTC+14..UTC-12, and no claim-time rule is right
    everywhere in that band, so the residual closes where it is made: capture
    refuses a deadline it cannot order."""

    def test_zone_less_deadline_is_refused_at_capture(self):
        with self.assertRaises(UnorderableCancellationDeadline):
            action(cancellation_deadline="2026-10-11T12:00:00")

    def test_a_deadline_that_is_prose_is_refused_and_an_offset_does_not_rescue_it(self):
        self.assertIsNone(captured_deadline_instant("before the end of the week"))
        with self.assertRaises(UnorderableCancellationDeadline):
            action(cancellation_deadline="before the end of the week")
        with self.assertRaises(UnorderableCancellationDeadline):
            action(cancellation_deadline="before the end of the week",
                   cancellation_deadline_utc_offset_minutes=0,
                   cancellation_deadline_offset_evidence_ref="terms-ester-offset")

    def test_an_offset_without_an_evidence_ref_is_the_same_guess(self):
        with self.assertRaises(UnorderableCancellationDeadline):
            action(cancellation_deadline="2026-10-11T12:00:00",
                   cancellation_deadline_utc_offset_minutes=-480)

    def test_a_local_time_deadline_with_its_captured_offset_is_accepted(self):
        local = action(cancellation_deadline="2026-10-11T12:00:00",
                       cancellation_deadline_utc_offset_minutes=-480,
                       cancellation_deadline_offset_evidence_ref="terms-ester-local-time")
        self.assertIsNone(local.deadline_capture_defect)
        self.assertEqual(local.deadline_instant.isoformat(), "2026-10-11T12:00:00-08:00")

    def test_the_captured_offset_orders_the_deadline_against_an_observation(self):
        local = action(cancellation_deadline="2026-10-11T12:00:00",
                       cancellation_deadline_utc_offset_minutes=-480,
                       cancellation_deadline_offset_evidence_ref="terms-ester-local-time")
        past = settle(local, outcome(observed_at="2026-10-11T20:30:00Z"))
        self.assertEqual(past["claim"], CLAIM_COVERED_MISSED_CANCELLATION_DEADLINE)
        self.assertTrue(past["covered"])
        self.assertNotIn("cancellation_deadline_not_orderable", past["record_defects"])
        inside = settle(local, outcome(observed_at="2026-10-11T19:30:00Z"))
        self.assertFalse(inside["covered"])

    def test_a_zoned_deadline_is_unaffected(self):
        self.assertIsNone(action().deadline_capture_defect)
        self.assertEqual(action().deadline_instant.isoformat(), "2026-10-11T12:00:00+00:00")

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


def legacy_action(deadline, **kw):
    """An action carrying a deadline the constructor would refuse today.

    BEAAA-35480 moved the refusal to ``ProposedAction.__post_init__``, so a bad
    deadline can no longer be built through the public path. Rows written
    before that guard still exist, and they still have to settle. This forces
    the field past the guard the way a row read back off disk does, which is
    the only way left to reach the claim-time branch that reads them.
    """
    built = action(**kw)
    object.__setattr__(built, "cancellation_deadline", deadline)
    return built


class TermsAreValidatedAtCapture(unittest.TestCase):
    """BEAAA-34396 R1, closed at source. Leaning an un-zoned deadline either way
    at claim time is a guess about a field BackBond captured itself: read early
    it pays on refundable money, read late it denies a real loss. The deadline
    is ordered against money, so a record that cannot order it is refused
    before anything executes, while the agent can still re-read the terms.

    BEAAA-35480 moved that refusal from this gate to the constructor, because
    the gate is only one of the doors: every ``ProposedAction`` in the tree is
    built directly, so a library caller reached the claim path without passing
    here. The gate keeps its own check for a row that arrives already built,
    and the claim path keeps its defensive reading for rows written before any
    of this existed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = EvidenceStore(Path(self.tmp.name) / "evidence.sqlite3")
        self.addCleanup(self.store.close)

    def test_a_zoned_deadline_is_recorded(self):
        self.assertEqual(self.store.record_pre_execution_receipt(make_receipt(action(), "t-ok")), "t-ok")
        self.assertIsNotNone(self.store.receipt_for("t-ok"))

    def test_an_un_zoned_deadline_cannot_be_recorded(self):
        """The refusal moved one step earlier, so the receipt is now
        unbuildable rather than unrecordable. ``UnorderableCancellationDeadline``
        is a ``TermsNotCaptured``, so the capture contract this asserted is the
        same contract; only the door it is enforced at changed."""
        with self.assertRaises(TermsNotCaptured) as caught:
            action(cancellation_deadline="2026-10-11T12:00:00")
        self.assertIn("cancellation_deadline_utc_offset_minutes", str(caught.exception))

    def test_a_prose_deadline_cannot_be_recorded_either(self):
        """R6's branch and R1's branch are now the same branch, refused in the
        same place, instead of one settling incomplete while the other pays."""
        with self.assertRaises(TermsNotCaptured):
            action(cancellation_deadline="before the end of the week")

    def test_the_gate_still_refuses_a_record_that_arrives_already_built(self):
        """The constructor is the first door, not the only one. A row rebuilt
        off disk, or any future ingestion that bypasses ``__post_init__``, is
        still turned away here before anything executes."""
        bad = make_receipt(legacy_action("2026-10-11T12:00:00"), "t-unzoned")
        with self.assertRaises(TermsNotCaptured) as caught:
            self.store.record_pre_execution_receipt(bad)
        self.assertIn("cancellation_deadline_not_orderable", str(caught.exception))
        self.assertIsNone(self.store.receipt_for("t-unzoned"))

    def test_capture_defects_are_listed_on_the_action(self):
        """Nothing buildable carries the defect any more. It survives only on a
        row that got past the constructor, which is exactly what the claim path
        still has to read."""
        self.assertEqual(action().capture_defects(), [])
        self.assertEqual(legacy_action("2026-10-11T12:00:00").capture_defects(),
                         ["cancellation_deadline_not_orderable"])

    def test_the_claim_path_stays_defensive_for_records_already_written(self):
        """The gate is new; rows written before it exist. Those still settle,
        read as written, with the capture defect on the claim."""
        claim = settle(legacy_action("2026-10-11T12:00:00"),
                       outcome(observed_at="2026-10-11T23:00:00Z"))
        self.assertTrue(claim["covered"])
        self.assertIn("cancellation_deadline_not_orderable", claim["record_defects"])


if __name__ == "__main__":
    unittest.main()
