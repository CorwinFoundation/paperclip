"""Path 1 approval receipt: BackBond records the user's approval.

Founder ruling 2026-09-24, Addendum 2 on BEAAA-34387. The earlier Stage A model
judged an agent's action against a mandate BackBond held its own copy of. The
2026-09-24 01:07-01:41Z Instinct run showed why that cannot work: the mandate
lives in the user's chat with their agent and moves there, so at 01:35 BackBond
issued a STOP against a plan the user had already changed in a conversation
BackBond cannot see. A copy of a mandate we do not own generates false STOPs.

So BackBond does not judge the action. It records the approval:

1. The agent's pre-execution check goes to the **user**, BackBond copied, carrying
   the exact action, price, deposit and cancellation terms.
2. The **user** answers OK or STOP on a thread BackBond can see. One approval.
3. Before execution BackBond writes an :class:`ApprovalReceipt`. That receipt is
   the evidence root for the task.
4. Outcome matching runs against the approved action on the receipt, never
   against a mandate.
5. Exactly two judgments are written, and both are warnings, never vetoes:
   "this is non-recoverable" and "this is over your limit".
6. A skipped or unanswered check is recorded as such and changes the claim
   outcome. Absence is never read as approval.

Stage A is fixture-based: no mailbox, no connector, no real agent.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

# What the user answered on the thread BackBond can see.
ANSWER_OK = "OK"
ANSWER_STOP = "STOP"
# The agent executed without ever asking.
ANSWER_SKIPPED = "SKIPPED"
# The check was sent and no answer arrived before execution.
ANSWER_UNANSWERED = "UNANSWERED"

ANSWERS = (ANSWER_OK, ANSWER_STOP, ANSWER_SKIPPED, ANSWER_UNANSWERED)

# The only two judgments BackBond makes. Both are advisory.
WARNING_NON_RECOVERABLE = "non_recoverable"
WARNING_OVER_LIMIT = "over_limit"

# Where the receipt's evidence came from. Stage A has one value; the Path 1
# pilot adds the mailbox thread. Kept explicit so a demo row can never be read
# as though it had come off a real thread.
EVIDENCE_SOURCE_FIXTURE = "fixture"

# Who says a delegated obligation was carried out. The acting agent's own word
# is the claim, not evidence for it: the same self-assertion the approval path
# already refuses (harness A1/SA8), on the outcome side. Only a counterparty
# confirmation - the merchant's cancellation notice, a refund on the card -
# closes an omission.
PERFORMANCE_SELF_REPORTED = "acting_agent"
PERFORMANCE_COUNTERPARTY_CONFIRMED = "counterparty_confirmation"


class CheckNotApproved(PermissionError):
    """Execution was attempted without an OK on the receipt."""


class TermsNotCaptured(ValueError):
    """The terms on the action are not in a state a receipt can be written on.

    Raised at capture time, before anything executes, so the agent re-reads the
    terms rather than BackBond guessing later what they meant.
    """


@dataclass(frozen=True)
class ProposedAction:
    """The action exactly as the agent put it to the user, with its terms.

    ``non_recoverable`` and ``deposit_cents`` come off the cancellation terms
    captured at delegation time, not reconstructed afterwards. That is the whole
    point of capturing them in the pre-execution check.
    """

    description: str
    merchant: str
    amount_cents: int
    deposit_cents: int = 0
    cancellation_terms: str = ""
    non_recoverable: bool = False
    # The cancellation terms, captured as independent evidence at delegation
    # time rather than reconstructed from memory after a loss (Founder ruling
    # section 2). ``cancellation_deadline`` is the moment after which the money
    # stops being recoverable; ``terms_captured_at`` and ``terms_evidence_ref``
    # say when we read that, and off what.
    cancellation_deadline: str | None = None
    terms_captured_at: str | None = None
    terms_evidence_ref: str = ""
    # True when the task the user approved is an obligation to act by the
    # deadline — cancel this booking, not buy this thing. Missing the deadline
    # on one of these is itself the loss.
    is_cancellation_obligation: bool = False

    @property
    def is_delegated_cancellation(self) -> bool:
        """Whether this is an obligation to act by a deadline.

        ``is_cancellation_obligation`` is the caller's own word for it, and a
        self-attested flag can only add a reading, never remove one. An action
        that costs nothing, carries a captured deadline and sits on a deposit
        is that obligation whatever the flag says, so leaving the flag unset
        cannot make a missed deadline settle as an approved non-loss.
        """
        return self.is_cancellation_obligation or (
            self.amount_cents == 0
            and bool(self.cancellation_deadline)
            and self.deposit_cents > 0
        )

    @property
    def deadline_capture_defect(self) -> str | None:
        """A deadline we wrote down but cannot order, named where it was made.

        A stamp with no zone denotes a different instant in every offset, so it
        cannot be ordered against an observation. That is a defect in what we
        captured at delegation time, not something the observation did wrong,
        and the claim path reads it as ours rather than as a missing
        observation.
        """
        if not self.cancellation_deadline:
            return None
        if observation_instant(self.cancellation_deadline) is not None:
            return None
        return "cancellation_deadline_not_orderable"

    def capture_defects(self) -> list[str]:
        """Everything wrong with the terms as captured, in a fixed order.

        Read at capture time by the receipt-writing gate. A defect here is not
        something the user did: it is the record we wrote about their booking
        being unusable for deciding money later, which is ours to catch before
        execution rather than to interpret after a loss (BEAAA-34396 R1).
        """
        return [d for d in (self.deadline_capture_defect,) if d]

    def terms_evidence(self) -> dict[str, Any]:
        """What we knew about getting the money back, and when we knew it."""
        return {
            "text": self.cancellation_terms,
            "deadline": self.cancellation_deadline,
            "deposit_cents": self.deposit_cents,
            "non_recoverable": self.non_recoverable,
            "captured_at": self.terms_captured_at,
            "evidence_ref": self.terms_evidence_ref,
            "capture_defect": self.deadline_capture_defect,
        }

    def matches(self, outcome: "ExecutedOutcome") -> bool:
        """Did the thing that happened match the thing the user approved?"""
        return (
            self.description == outcome.description
            and self.merchant == outcome.merchant
            and self.amount_cents == outcome.amount_cents
        )

    def differences(self, outcome: "ExecutedOutcome") -> list[dict[str, Any]]:
        deltas = []
        for field_name in ("description", "merchant", "amount_cents"):
            approved = getattr(self, field_name)
            happened = getattr(outcome, field_name)
            if approved != happened:
                deltas.append({"field": field_name, "approved": approved, "outcome": happened})
        return deltas


@dataclass(frozen=True)
class ExecutedOutcome:
    """What actually happened, read off independent evidence.

    In Stage A this is a fixture standing in for a confirmation email or a card
    charge. ``independent`` is False when no third-party evidence arrived, which
    is never the same as a match.
    """

    description: str
    merchant: str
    amount_cents: int
    evidence_ref: str = ""
    independent: bool = True
    # False when the approved action was never performed. An omission is a real
    # outcome: a delegated cancellation that never happened is exactly the loss
    # the user is exposed to.
    performed: bool = True
    # When independent evidence was read. Compared against the cancellation
    # deadline to decide whether the money is still recoverable. An ISO-8601
    # stamp carrying a zone; anything else cannot be ordered against a deadline
    # and is read as no observation at all.
    observed_at: str = ""
    # Who attests that a delegated obligation was performed. Defaults to the
    # acting agent, which is a self-assertion and never closes an omission.
    performed_source: str = PERFORMANCE_SELF_REPORTED


def observation_instant(stamp: str | None) -> datetime | None:
    """The instant an ISO-8601 stamp denotes, or None if it cannot be ordered.

    Timestamps decide money here, so they are compared as instants and never as
    strings. ``2026-10-11T09:00:00-08:00`` sorts below ``2026-10-11T12:00:00Z``
    as text and is five hours past it in fact. A stamp with no zone denotes a
    different instant in every offset, so it is not orderable against a Zulu
    deadline at all; neither is one that does not parse, nor an empty one. All
    of those read as "nothing was observed", never as a side.
    """
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("z", "Z").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def captured_deadline_instant(stamp: str | None) -> datetime | None:
    """The instant a captured deadline denotes, reading a zone-less stamp at UTC.

    A deadline is ours to capture, so a missing zone is our defect and never
    makes the deadline vanish: it is read as written, at UTC, and the defect is
    recorded on the claim beside it. That is the only reading the captured text
    supports. Guessing an offset in either direction would decide money on a
    zone nobody wrote down - too early and a live booking settles as a loss,
    too late and a real loss settles as none.

    An observation is not treated this way. Nothing was observed at a time we
    cannot order, so an un-zoned observation stays unorderable.
    """
    zoned = observation_instant(stamp)
    if zoned is not None:
        return zoned
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


# The widest UTC offsets in civil use. A zone-less local time denotes its
# earliest instant at UTC+14 and its latest at UTC-12.
WIDEST_UTC_OFFSET = timedelta(hours=14)


def earliest_possible_instant(stamp: str | None) -> datetime | None:
    """The earliest instant a zone-less stamp could denote, at UTC+14.

    Reports the width of the ambiguity a zone-less stamp carries. It is not
    what settles a claim: BEAAA-34396 round two showed that reading a deadline
    at its earliest possible instant turns a fourteen-hour band on either side
    of the captured stamp into covered losses, while on the stamp's plain
    reading the deposit is still refundable. Leaning a capture defect that hard
    against the record manufactures claims instead of preventing denials.
    """
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("z", "Z").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed
    return parsed.replace(tzinfo=timezone.utc) - WIDEST_UTC_OFFSET


def assess_warnings(action: ProposedAction, limit_cents: int | None) -> list[str]:
    """The two advisory judgments, in a fixed order. Never a veto.

    Returning a warning does not stop anything: it is written onto the receipt
    so the user sees it next to their own OK, and so a later claim can show what
    they were told at the time.
    """
    warnings: list[str] = []
    if action.non_recoverable or action.deposit_cents > 0:
        warnings.append(WARNING_NON_RECOVERABLE)
    if limit_cents is not None and action.amount_cents > limit_cents:
        warnings.append(WARNING_OVER_LIMIT)
    return warnings


def amount_at_risk_cents(action: ProposedAction) -> int:
    """What the user cannot get back if this goes wrong.

    The deposit when the booking is otherwise refundable; the whole amount when
    the terms say non-recoverable. Section 2 of the ruling: a covered event is
    money the user cannot get back.
    """
    if action.is_delegated_cancellation:
        # Cancelling costs nothing, so the action's own amount is zero. What is
        # at risk is the deposit on the booking being cancelled, which stops
        # being recoverable once the deadline passes. ``non_recoverable`` is a
        # label on that same deposit, so it is read first here and can only
        # raise the exposure - a warning about the money must never be the
        # reason less of it is at risk.
        return max(action.deposit_cents, action.amount_cents)
    if action.non_recoverable:
        return action.amount_cents
    return min(action.deposit_cents, action.amount_cents)


@dataclass(frozen=True)
class ApprovalReceipt:
    """The evidence root for one task. Written before execution or it is not a receipt."""

    receipt_id: str
    task_id: str
    proposed_action: ProposedAction
    answer: str
    checked_at: str
    answered_at: str | None
    recorded_at: str
    evidence_source: str = EVIDENCE_SOURCE_FIXTURE
    limit_cents: int | None = None
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.answer not in ANSWERS:
            raise ValueError(f"unknown answer: {self.answer!r}")
        if self.answer in (ANSWER_OK, ANSWER_STOP) and not self.answered_at:
            raise ValueError(f"an answer of {self.answer} needs the time it was given")
        if self.answer in (ANSWER_SKIPPED, ANSWER_UNANSWERED) and self.answered_at:
            raise ValueError(f"{self.answer} means no answer was given, so it cannot carry a time")

    @property
    def approved(self) -> bool:
        """Only an explicit OK is an approval. Absence never is."""
        return self.answer == ANSWER_OK

    @property
    def amount_at_risk_cents(self) -> int:
        return amount_at_risk_cents(self.proposed_action)

    def to_json(self) -> str:
        payload = asdict(self)
        payload["amount_at_risk_cents"] = self.amount_at_risk_cents
        return json.dumps(payload, sort_keys=True)


# Claim outcomes.
CLAIM_NOT_COVERED_APPROVED = "not_covered_user_approved_this"
CLAIM_COVERED_OUTCOME_DIFFERS = "covered_outcome_differs_from_approved"
CLAIM_COVERED_EXECUTED_AGAINST_STOP = "covered_executed_against_stop"
CLAIM_COVERED_NO_APPROVAL = "covered_executed_without_an_answered_check"
CLAIM_INCOMPLETE_NO_INDEPENDENT_EVIDENCE = "incomplete_no_independent_evidence"
# The two non-recoverable-loss shapes named in the Founder ruling, section 2.
CLAIM_COVERED_MISSED_CANCELLATION_DEADLINE = "covered_missed_cancellation_deadline"
CLAIM_NOT_YET_A_LOSS_WITHIN_CANCELLATION_WINDOW = "not_yet_a_loss_still_within_cancellation_window"
# An omission that cannot be settled: nothing independent says the obligation
# was met, or nothing orderable says when it was looked at.
CLAIM_INCOMPLETE_PERFORMANCE_SELF_REPORTED = "incomplete_performance_only_self_reported"
CLAIM_INCOMPLETE_NO_ORDERABLE_OBSERVATION = "incomplete_no_orderable_observation_time"
# The deadline passed with the act undone, but nothing captured at delegation
# time says what it cost. A claim cannot name a covered loss and pay zero.
CLAIM_INCOMPLETE_NO_CAPTURED_AMOUNT_AT_RISK = "incomplete_no_captured_amount_at_risk"


def review_claim(receipt: ApprovalReceipt, outcome: ExecutedOutcome | None) -> dict[str, Any]:
    """Reconstruct a claim from the receipt and what independent evidence says.

    Reads only the receipt and the outcome. It never consults a mandate, and it
    never infers an approval that is not written down.
    """
    base: dict[str, Any] = {
        "task_id": receipt.task_id,
        "receipt_id": receipt.receipt_id,
        "answer": receipt.answer,
        "warnings": list(receipt.warnings),
        "amount_at_risk_cents": receipt.amount_at_risk_cents,
        "evidence_source": receipt.evidence_source,
        "approved_action": asdict(receipt.proposed_action),
        # What we knew about getting the money back, and when we read it.
        "cancellation_terms_evidence": receipt.proposed_action.terms_evidence(),
        "outcome": asdict(outcome) if outcome else None,
        "differences": [],
        # Record defects are reported alongside the claim rather than folded
        # into it: a missing approval is always on the record even when the
        # money turns out to be recoverable.
        "record_defects": [],
    }

    if outcome is None or not outcome.independent:
        # No third-party evidence is never a match and never a denial.
        return {**base, "outcome_state": "UNKNOWN/INCOMPLETE",
                "claim": CLAIM_INCOMPLETE_NO_INDEPENDENT_EVIDENCE, "covered": False}

    differences = receipt.proposed_action.differences(outcome)
    matched = not differences
    base["differences"] = differences

    if receipt.answer == ANSWER_STOP:
        # The user said STOP and it happened anyway. Nothing the outcome says
        # makes that approved.
        return {**base, "outcome_state": "MATCH" if matched else "MISMATCH",
                "claim": CLAIM_COVERED_EXECUTED_AGAINST_STOP,
                "covered": receipt.amount_at_risk_cents > 0}

    action = receipt.proposed_action
    if action.is_delegated_cancellation and matched:
        # The omission path exists because a cancellation has nothing to match
        # against: it is amount 0 and echoes the approved description back. An
        # outcome that genuinely differs from what the user approved does have
        # something to match against, and that mismatch is the claim - the
        # obligation reading must not swallow it (BEAAA-34396 R4).
        settled = _review_cancellation_obligation(receipt, outcome, base)
        if settled is not None:
            return settled

    if receipt.answer in (ANSWER_SKIPPED, ANSWER_UNANSWERED):
        return {**base, "outcome_state": "MATCH" if matched else "MISMATCH",
                "claim": CLAIM_COVERED_NO_APPROVAL,
                "covered": receipt.amount_at_risk_cents > 0}

    if matched:
        # Approved X, X happened. Not a covered event, and the record shows the
        # user approved it.
        return {**base, "outcome_state": "MATCH", "claim": CLAIM_NOT_COVERED_APPROVED, "covered": False}

    # Approved X, Y happened. Covered only to the extent the money is
    # unrecoverable (ruling section 2).
    return {**base, "outcome_state": "MISMATCH", "claim": CLAIM_COVERED_OUTCOME_DIFFERS,
            "covered": receipt.amount_at_risk_cents > 0}


def _review_cancellation_obligation(
    receipt: ApprovalReceipt, outcome: ExecutedOutcome, base: dict[str, Any]
) -> dict[str, Any] | None:
    """Settle a delegated obligation to act by a deadline, or return None.

    The user delegated an act — cancel this booking — and the loss shape is an
    omission, so there is nothing to match against: a cancellation is amount 0
    and its description is the approved one echoed back. What decides it is the
    deadline in the terms captured at delegation time, and whether anything
    other than the acting party says the act happened.

    Returns None only when performance is confirmed by a counterparty, in which
    case the ordinary outcome-matching path applies.
    """
    action = receipt.proposed_action
    defects = (
        [f"check_{receipt.answer.lower()}"]
        if receipt.answer in (ANSWER_SKIPPED, ANSWER_UNANSWERED)
        else []
    )
    record = {**base, "record_defects": defects}

    if outcome.performed:
        if outcome.performed_source == PERFORMANCE_COUNTERPARTY_CONFIRMED:
            return None
        # The party that was supposed to act is the only one saying it acted.
        # That is the claim, not evidence for it, and it cannot close a loss.
        return {**record, "outcome_state": "UNKNOWN/INCOMPLETE",
                "claim": CLAIM_INCOMPLETE_PERFORMANCE_SELF_REPORTED, "covered": False}

    observed = observation_instant(outcome.observed_at)
    if observed is None:
        # Nothing orderable was observed. That is not evidence the money is
        # still recoverable, and it is not evidence it is gone (harness A8).
        return {**record, "outcome_state": "UNKNOWN/INCOMPLETE",
                "claim": CLAIM_INCOMPLETE_NO_ORDERABLE_OBSERVATION, "covered": False}

    deadline = observation_instant(action.cancellation_deadline)
    if deadline is None:
        # We have an orderable observation and a deadline with no zone. That is
        # our capture defect, not a missing observation: it is recorded as ours
        # and the deadline is read as written, at UTC. A deadline we failed to
        # write down properly is never the reason a user's loss reads as no
        # loss at all, and never the reason a live booking reads as one.
        capture_defect = action.deadline_capture_defect or "cancellation_deadline_not_captured"
        record = {**record, "record_defects": [*record["record_defects"], capture_defect]}
        deadline = captured_deadline_instant(action.cancellation_deadline)
        if deadline is None:
            return {**record, "outcome_state": "UNKNOWN/INCOMPLETE",
                    "claim": CLAIM_INCOMPLETE_NO_ORDERABLE_OBSERVATION, "covered": False}

    if observed > deadline:
        # The deadline passed with the act undone: this is the money the user
        # cannot get back, whatever the record says about the approval.
        if receipt.amount_at_risk_cents <= 0:
            # Nothing captured at delegation time says what the omission cost.
            # Naming this a covered loss while paying zero would be a claim
            # string that contradicts its own verdict, so it settles as the
            # capture defect it is.
            return {**record, "outcome_state": "NOT_PERFORMED",
                    "claim": CLAIM_INCOMPLETE_NO_CAPTURED_AMOUNT_AT_RISK,
                    "record_defects": [*record["record_defects"], "amount_at_risk_not_captured"],
                    "covered": False}
        return {**record, "outcome_state": "NOT_PERFORMED",
                "claim": CLAIM_COVERED_MISSED_CANCELLATION_DEADLINE,
                "covered": True}

    # Still inside the window: the deposit is recoverable, so nothing is lost
    # yet. Recorded, not denied, and not covered — a missing approval is a
    # record defect and does not by itself make refundable money unrecoverable
    # (Founder ruling, section 2).
    return {**record, "outcome_state": "NOT_PERFORMED",
            "claim": CLAIM_NOT_YET_A_LOSS_WITHIN_CANCELLATION_WINDOW, "covered": False}
