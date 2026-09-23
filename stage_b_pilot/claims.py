"""Claims-review packet for a pilot task.

Implements the packet contract from BEAAA-34392 against the eligibility and
limit requirements from BEAAA-34391. The three states it reports are kept
apart on purpose:

* ``execution`` - what the agent did versus its mandate.
* ``coverage_decision`` - how far the candidate travels toward a human.
* ``payment_status`` - always ``not_authorized`` in the pilot.

A packet is not a decision. Nothing in it can be read as an amount owed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import MODE
from .activation_view import ActivationStatus
from . import program


@dataclass(frozen=True)
class MandateRecord:
    """Immutable, versioned mandate derived from the human's intent."""

    mandate_id: str
    mandate_version: str
    sku: str
    amount_cents: int
    derived_from_intent_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "mandate_id": self.mandate_id,
            "mandate_version": self.mandate_version,
            "sku": self.sku,
            "amount_cents": self.amount_cents,
            "derived_from_intent_id": self.derived_from_intent_id,
        }


@dataclass
class ClaimsReviewPacket:
    case_id: str
    program_version: str
    program_requirements_version: str
    original_human_intent: dict[str, Any]
    mandate: dict[str, Any]
    agent_assertion: dict[str, Any]
    independent_order_outcome: dict[str, Any] | None
    execution: dict[str, Any]
    duplicate_proof: dict[str, Any]
    loss_and_refund_history: list[dict[str, Any]]
    coverage_decision: str
    payment_status: str
    missing_evidence_reason_codes: list[str]
    limits: dict[str, Any]
    reviewer_explanation: str
    initiated_by: str = "detector"
    prior_rights_preserved: bool = True
    mode: str = MODE
    source_links: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "case_id": self.case_id,
            "program_version": self.program_version,
            "program_requirements_version": self.program_requirements_version,
            "initiated_by": self.initiated_by,
            "original_human_intent": self.original_human_intent,
            "mandate": self.mandate,
            "agent_assertion": self.agent_assertion,
            "independent_order_outcome": self.independent_order_outcome,
            "execution": self.execution,
            "duplicate_proof": self.duplicate_proof,
            "loss_and_refund_history": self.loss_and_refund_history,
            "coverage_decision": self.coverage_decision,
            "payment_status": self.payment_status,
            "missing_evidence_reason_codes": self.missing_evidence_reason_codes,
            "limits": self.limits,
            "prior_rights_preserved": self.prior_rights_preserved,
            "reviewer_explanation": self.reviewer_explanation,
            "source_links": self.source_links,
        }


def _reviewer_explanation(
    *, execution_state: str, decision: str, loss_cents: int, status: ActivationStatus
) -> str:
    what_happened = {
        program.EXECUTION_MATCH: "The agent's purchase matched the mandate.",
        program.EXECUTION_MISMATCH: (
            "The agent bought something the mandate did not authorise."
        ),
        program.EXECUTION_UNKNOWN: (
            "No independent merchant evidence arrived, so what happened is unproven."
        ),
    }[execution_state]
    ceiling = program.LIMITS.ceiling_for(loss_cents)
    return (
        f"{what_happened} Decision state: {decision}. "
        f"Observed loss {loss_cents} cents, per-event ceiling {ceiling} cents. "
        f"{status.statement} No payment is authorised from this packet."
    )


def build_packet(
    *,
    case_id: str,
    intent: dict[str, Any],
    mandate: MandateRecord,
    receipt_packet: dict[str, Any],
    status: ActivationStatus,
    loss_cents: int,
    refunds: list[dict[str, Any]] | None = None,
    initiated_by: str = "detector",
    plan_cancelled: bool = False,
) -> ClaimsReviewPacket:
    execution_state = receipt_packet["status"]
    independent = receipt_packet["sources"]["independent_merchant_evidence"]
    execution = receipt_packet.get("execution") or {}

    reasons: list[str] = []
    if independent is None:
        reasons.append(program.REASON_NO_INDEPENDENT_EVIDENCE)
    if not execution:
        reasons.append(program.REASON_NO_EXECUTION_RECORD)
    if not status.active:
        reasons.append(program.REASON_PROTECTION_NOT_ACTIVE)

    decision = program.coverage_decision(
        protection_active=status.active,
        execution_state=execution_state,
        has_independent_evidence=independent is not None,
        # A clean purchase with an observed loss is still a candidate: the
        # loss is the thing a reviewer would look at.
        candidate_event=execution_state != program.EXECUTION_MATCH
        or loss_cents > 0,
    )

    history: list[dict[str, Any]] = list(refunds or [])
    if loss_cents:
        history.insert(
            0,
            {
                "kind": "observed_loss",
                "amount_cents": loss_cents,
                "source": "mandate_vs_independent_evidence",
            },
        )

    return ClaimsReviewPacket(
        case_id=case_id,
        program_version=status.program_version,
        program_requirements_version=program.PROGRAM_REQUIREMENTS_VERSION,
        original_human_intent=intent,
        mandate=mandate.as_dict(),
        agent_assertion=receipt_packet["sources"]["agent_assertion"],
        independent_order_outcome=independent,
        execution={
            "state": execution_state,
            "reviewable_mismatches": receipt_packet["reviewable_mismatches"],
        },
        duplicate_proof={
            "idempotency_key": execution.get("idempotency_key"),
            "ledger_allocation": execution.get("ledger_allocation"),
            "authoritative_executions": 1 if execution else 0,
        },
        loss_and_refund_history=history,
        coverage_decision=decision,
        payment_status=program.NO_PAYMENT_AUTHORIZED,
        missing_evidence_reason_codes=reasons,
        limits=program.as_dict()["limits"],
        reviewer_explanation=_reviewer_explanation(
            execution_state=execution_state,
            decision=decision,
            loss_cents=loss_cents,
            status=status,
        ),
        initiated_by=initiated_by,
        # Cancelling a plan does not erase what happened under it, so this
        # is True whether or not ``plan_cancelled`` is set.
        prior_rights_preserved=True,
        source_links=list(receipt_packet["claims_review"]["source_links"]),
    )
