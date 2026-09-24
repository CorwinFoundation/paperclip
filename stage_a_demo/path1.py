"""Runnable Path 1 fixtures, including the two non-recoverable-loss cases.

Founder ruling 2026-09-24, section 2: the covered event is money the user cannot
get back. The next Stage A increment models it with a non-refundable booking
mismatch and a missed cancellation deadline, with the cancellation terms
captured as independent evidence at delegation time.

Fixture-based. No mailbox, no connector, no real agent: these stand in for a
pre-execution check on a thread BackBond can see and a confirmation email or
charge read afterwards.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import EvidenceStore
from .receipt import (
    ANSWER_OK,
    ANSWER_SKIPPED,
    ApprovalReceipt,
    ExecutedOutcome,
    ProposedAction,
    assess_warnings,
)

# What the user told BackBond they were comfortable with. Only ever a warning.
DEMO_LIMIT_CENTS = 40_000

# 1. Non-refundable booking mismatch. The user approved Ester on the 12th; the
#    agent booked the 19th. The whole amount is unrecoverable under the terms
#    read at delegation time, so the whole amount is at risk.
NON_REFUNDABLE_MISMATCH = ProposedAction(
    description="Hotel Ester, 12-15 Oct, 2 guests",
    merchant="bookings.example",
    amount_cents=48_000,
    deposit_cents=48_000,
    cancellation_terms="Non-refundable rate. No date changes, no refunds.",
    non_recoverable=True,
    terms_captured_at="2026-10-01T09:07:00Z",
    terms_evidence_ref="quote-ester-nonref-1",
)

# 2. Missed cancellation deadline on a delegated obligation. Cancelling costs
#    nothing; what is at risk is the deposit that stops being recoverable when
#    the deadline in the captured terms passes.
CANCELLATION_OBLIGATION = ProposedAction(
    description="Cancel the Ester booking before the refund deadline",
    merchant="bookings.example",
    amount_cents=0,
    deposit_cents=12_000,
    cancellation_terms="Deposit refundable until 2026-10-11T12:00:00Z, non-refundable after.",
    cancellation_deadline="2026-10-11T12:00:00Z",
    terms_captured_at="2026-10-01T09:07:00Z",
    terms_evidence_ref="booking-ester-terms-1",
    is_cancellation_obligation=True,
)

# Control: the same booking, approved and executed as approved.
CLEAN_BOOKING = ProposedAction(
    description="Hotel Ester, 12-15 Oct, 2 guests",
    merchant="bookings.example",
    amount_cents=38_000,
    deposit_cents=9_500,
    cancellation_terms="Deposit non-refundable after 24h; balance refundable to 7 days before arrival.",
    cancellation_deadline="2026-10-02T09:07:00Z",
    terms_captured_at="2026-10-01T09:07:00Z",
    terms_evidence_ref="booking-ester-terms-0",
)


def _receipt(task_id, action, answer, checked_at, answered_at, recorded_at):
    return ApprovalReceipt(
        receipt_id=f"receipt-{task_id}",
        task_id=task_id,
        proposed_action=action,
        answer=answer,
        checked_at=checked_at,
        answered_at=answered_at,
        recorded_at=recorded_at,
        limit_cents=DEMO_LIMIT_CENTS,
        warnings=assess_warnings(action, DEMO_LIMIT_CENTS),
    )


CASES: tuple[tuple[str, ApprovalReceipt, ExecutedOutcome], ...] = (
    (
        "clean-booking",
        _receipt("clean-booking", CLEAN_BOOKING, ANSWER_OK,
                 "2026-10-01T09:07:00Z", "2026-10-01T09:09:00Z", "2026-10-01T09:09:05Z"),
        ExecutedOutcome("Hotel Ester, 12-15 Oct, 2 guests", "bookings.example", 38_000,
                        evidence_ref="confirmation-ester-clean", observed_at="2026-10-01T09:20:00Z"),
    ),
    (
        "non-refundable-mismatch",
        _receipt("non-refundable-mismatch", NON_REFUNDABLE_MISMATCH, ANSWER_OK,
                 "2026-10-01T09:07:00Z", "2026-10-01T09:09:00Z", "2026-10-01T09:09:05Z"),
        ExecutedOutcome("Hotel Ester, 19-22 Oct, 2 guests", "bookings.example", 48_000,
                        evidence_ref="confirmation-ester-wrong-dates",
                        observed_at="2026-10-01T09:41:00Z"),
    ),
    (
        "missed-cancellation-deadline",
        _receipt("missed-cancellation-deadline", CANCELLATION_OBLIGATION, ANSWER_OK,
                 "2026-10-10T09:00:00Z", "2026-10-10T09:05:00Z", "2026-10-10T09:05:05Z"),
        ExecutedOutcome("Cancel the Ester booking before the refund deadline", "bookings.example",
                        0, evidence_ref="no-cancellation-in-inbox", performed=False,
                        observed_at="2026-10-11T12:30:00Z"),
    ),
    (
        "cancellation-still-in-window",
        _receipt("cancellation-still-in-window", CANCELLATION_OBLIGATION, ANSWER_OK,
                 "2026-10-10T09:00:00Z", "2026-10-10T09:05:00Z", "2026-10-10T09:05:05Z"),
        ExecutedOutcome("Cancel the Ester booking before the refund deadline", "bookings.example",
                        0, evidence_ref="not-cancelled-yet", performed=False,
                        observed_at="2026-10-10T18:00:00Z"),
    ),
    (
        "check-skipped",
        _receipt("check-skipped", NON_REFUNDABLE_MISMATCH, ANSWER_SKIPPED,
                 "2026-10-01T09:07:00Z", None, "2026-10-01T09:41:10Z"),
        ExecutedOutcome("Hotel Ester, 12-15 Oct, 2 guests", "bookings.example", 48_000,
                        evidence_ref="confirmation-ester-unasked",
                        observed_at="2026-10-01T09:41:00Z"),
    ),
)


def run_path1_cases(db_path: str | Path) -> list[dict[str, Any]]:
    """Record each receipt before its outcome, then reconstruct every claim."""
    store = EvidenceStore(db_path)
    try:
        reviews = []
        for task_id, receipt, outcome in CASES:
            store.record_pre_execution_receipt(receipt)
            reviews.append(store.record_outcome(task_id, outcome, executed_at=outcome.observed_at))
        return reviews
    finally:
        store.close()
