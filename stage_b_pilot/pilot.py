"""End-to-end pilot run: delegation to receipt, instrumented throughout.

One supported task type (purchase) travels the whole seam:

    task intent -> versioned mandate -> human approval -> mandate check ->
    simulated transaction -> receipt -> protection state -> covered-event
    candidate on a seeded failure

The mandate check observes; it does not block. The reference agent stands in
for an agent we do not control, so a deviation is something we detect after
the fact, which is the case the program has to be able to price.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stage_a_demo.approval import build_issuer
from stage_a_demo.core import (
    EvidenceStore,
    HumanApprovalIssuer,
    Mandate,
    SyntheticMerchantAdapter,
)

from . import MODE
from .activation_view import ActivationStatus, RecordRegistry, pilot_status
from .agent import PlannedAction, ReferenceAgent, TaskIntent
from .claims import ClaimsReviewPacket, MandateRecord, build_packet
from . import program
from .telemetry import Telemetry

MANDATE_VERSION = "mandate-v1"


@dataclass
class TaskRun:
    intent: TaskIntent
    mandate: MandateRecord
    planned: PlannedAction
    deviations: list[dict[str, Any]]
    receipt_packet: dict[str, Any]
    covered_event_candidate: ClaimsReviewPacket | None
    observed_loss_cents: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent.intent_id,
            "mandate": self.mandate.as_dict(),
            "planned_action": {
                "sku": self.planned.sku,
                "amount_cents": self.planned.amount_cents,
                "rationale": self.planned.rationale,
            },
            "mandate_deviations": self.deviations,
            "observed_loss_cents": self.observed_loss_cents,
            "receipt": {
                "case_id": self.receipt_packet["case_id"],
                "status": self.receipt_packet["status"],
                "execution": self.receipt_packet["execution"],
                "protection": self.receipt_packet["protection"],
            },
            "covered_event_candidate": (
                self.covered_event_candidate.as_dict()
                if self.covered_event_candidate
                else None
            ),
        }


def derive_mandate(intent: TaskIntent) -> MandateRecord:
    return MandateRecord(
        mandate_id=f"mandate-{intent.intent_id}",
        mandate_version=MANDATE_VERSION,
        sku=intent.sku,
        amount_cents=intent.max_amount_cents,
        derived_from_intent_id=intent.intent_id,
    )


def check_mandate(mandate: MandateRecord, planned: PlannedAction) -> list[dict[str, Any]]:
    """Compare the agent's plan against the mandate. Observes, never blocks."""
    deviations: list[dict[str, Any]] = []
    if planned.sku != mandate.sku:
        deviations.append(
            {"field": "sku", "mandate": mandate.sku, "planned": planned.sku}
        )
    if planned.amount_cents > mandate.amount_cents:
        deviations.append(
            {
                "field": "amount_cents",
                "mandate": mandate.amount_cents,
                "planned": planned.amount_cents,
            }
        )
    return deviations


def run_task(
    *,
    intent: TaskIntent,
    agent: ReferenceAgent,
    store: EvidenceStore,
    telemetry: Telemetry,
    human: HumanApprovalIssuer | None = None,
    merchant: SyntheticMerchantAdapter | None = None,
    independent_evidence: bool = True,
    registry: RecordRegistry | None = None,
    status: ActivationStatus | None = None,
) -> TaskRun:
    if not intent.supported():
        raise ValueError(f"unsupported task type: {intent.task_type}")

    # The signing capability is injected by the caller, or built once here from
    # an injected secret. It is never derived from the task being authorised.
    human = human or build_issuer()
    merchant = merchant or SyntheticMerchantAdapter()
    status = status or pilot_status(registry=registry)

    telemetry.tasks_delegated += 1
    telemetry.show(
        "task_delegated",
        mode=MODE,
        headline=f"Task delegated to {agent.agent_id}: {intent.description}",
        protection_active=status.active,
        protection_statement=status.statement,
    )

    mandate_record = derive_mandate(intent)
    planned = agent.plan(intent)
    deviations = check_mandate(mandate_record, planned)
    if deviations:
        telemetry.mandate_deviations += 1
        telemetry.show(
            "mandate_deviation_detected",
            mode=MODE,
            headline=(
                "The agent did not follow the mandate. "
                "Recorded for review; nothing is owed by this alone."
            ),
            protection_active=status.active,
            protection_statement=status.statement,
        )

    core_mandate = Mandate(
        mandate_record.mandate_id, mandate_record.sku, mandate_record.amount_cents
    )
    token, verifier = human.approve(core_mandate)
    case_id = f"case-{intent.intent_id}"
    receipt_packet = store.record_case(
        case_id=case_id,
        mandate=core_mandate,
        approval_token=token,
        asserted_sku=planned.sku,
        asserted_amount_cents=planned.amount_cents,
        idempotency_key=f"idem-{intent.intent_id}",
        approval_verifier=verifier,
        merchant=merchant,
        independent_evidence=independent_evidence,
    )
    telemetry.receipts_produced += 1
    telemetry.show(
        "receipt_produced",
        mode=MODE,
        headline=f"Task receipt {case_id} produced, execution {receipt_packet['status']}.",
        protection_active=status.active,
        protection_statement=receipt_packet["protection"]["statement"],
    )

    observed_loss_cents = max(
        0, planned.amount_cents - mandate_record.amount_cents
    ) if deviations else 0
    if deviations and any(d["field"] == "sku" for d in deviations):
        observed_loss_cents = planned.amount_cents

    candidate: ClaimsReviewPacket | None = None
    raise_candidate = bool(deviations) or receipt_packet["status"] in (
        program.EXECUTION_MISMATCH,
        program.EXECUTION_UNKNOWN,
    )
    if raise_candidate:
        candidate = build_packet(
            case_id=case_id,
            intent={
                "intent_id": intent.intent_id,
                "task_type": intent.task_type,
                "description": intent.description,
                "sku": intent.sku,
                "max_amount_cents": intent.max_amount_cents,
            },
            mandate=mandate_record,
            receipt_packet=receipt_packet,
            status=status,
            loss_cents=observed_loss_cents,
        )
        telemetry.covered_event_candidates_raised += 1
        telemetry.show(
            "covered_event_candidate_raised",
            mode=MODE,
            headline=(
                "Candidate raised for human review. "
                f"Decision state {candidate.coverage_decision}, "
                f"payment {candidate.payment_status}."
            ),
            protection_active=status.active,
            protection_statement=status.statement,
        )

    return TaskRun(
        intent=intent,
        mandate=mandate_record,
        planned=planned,
        deviations=deviations,
        receipt_packet=receipt_packet,
        covered_event_candidate=candidate,
        observed_loss_cents=observed_loss_cents,
    )


CLEAN_INTENT = TaskIntent(
    intent_id="clean-001",
    task_type="purchase",
    description="Buy the 500g bag of the house filter coffee, up to $18.",
    sku="SKU-COFFEE-500G",
    max_amount_cents=1800,
)

MISTAKE_INTENT = TaskIntent(
    intent_id="mistake-001",
    task_type="purchase",
    description="Book one night at the budget room rate, up to $140.",
    sku="SKU-ROOM-STANDARD",
    max_amount_cents=14000,
)

MISTAKE_DEVIATION = {
    "sku": "SKU-ROOM-SUITE",
    "amount_cents": 41000,
    "rationale": "standard room looked unavailable, upgraded without asking",
}


def run_seeded_scenarios(
    store: EvidenceStore, *, registry: RecordRegistry | None = None
) -> dict[str, Any]:
    """Two scenarios end to end: one clean task, one covered mistake."""
    telemetry = Telemetry()
    status = pilot_status(registry=registry)
    runs = {
        "clean": run_task(
            intent=CLEAN_INTENT,
            agent=ReferenceAgent(),
            store=store,
            telemetry=telemetry,
            status=status,
        ),
        "covered_mistake": run_task(
            intent=MISTAKE_INTENT,
            agent=ReferenceAgent(deviation=MISTAKE_DEVIATION),
            store=store,
            telemetry=telemetry,
            status=status,
        ),
    }
    return {
        "mode": MODE,
        "program": program.as_dict(),
        "protection": status.as_dict(),
        "scenarios": {name: run.as_dict() for name, run in runs.items()},
        "telemetry": telemetry.as_dict(),
    }
