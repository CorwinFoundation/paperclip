"""Versioned program requirements used by the Stage B pilot.

Encodes the eligibility, covered-event and limit requirements from
BEAAA-34391 and the packet separation rules from BEAAA-34392. Those issues
closed before their JSON artifacts were written to the repository, so this
module is the executable restatement of their requirement text and is the
single place the pilot reads program rules from.

Two rules here are load-bearing and are asserted by the tests:

* Assessment eligibility is not claim entitlement. Reaching review is not
  being owed anything.
* An agent deviating from its mandate cannot automatically deny the very
  mistake being considered for coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROGRAM_REQUIREMENTS_VERSION = "v0.1.0-stage-b-pilot"

# Coverage decision values. Kept separate from execution state and from
# payment status per BEAAA-34392.
NOT_ASSESSED = "not_assessed"
ELIGIBLE_FOR_HUMAN_REVIEW = "eligible_for_human_review"
RESERVED_PENDING_EVIDENCE = "reserved_pending_evidence"
INELIGIBLE_PROTECTION_NOT_ACTIVE = "ineligible_protection_not_active"

# Payment status. The pilot has no payment rail and no payment authority.
NO_PAYMENT_AUTHORIZED = "no_payment_authorized"

# Execution states, produced by comparing mandate against independent evidence.
EXECUTION_MATCH = "MATCH"
EXECUTION_MISMATCH = "MISMATCH"
EXECUTION_UNKNOWN = "UNKNOWN/INCOMPLETE"

# Missing-evidence reason codes.
REASON_NO_INDEPENDENT_EVIDENCE = "no_independent_merchant_evidence"
REASON_NO_EXECUTION_RECORD = "no_execution_record"
REASON_PROTECTION_NOT_ACTIVE = "protection_not_active"


@dataclass(frozen=True)
class Limits:
    """Limits are stated per event, per member and per cohort.

    A limit is a ceiling on what a human reviewer could consider. It is not a
    promise to pay, and no value here is an approved benefit.
    """

    per_event_cents: int = 25_000
    per_member_aggregate_cents: int = 100_000
    cohort_capacity_cents: int = 2_500_000

    def ceiling_for(self, loss_cents: int) -> int:
        return max(0, min(loss_cents, self.per_event_cents))


LIMITS = Limits()

# Requirements that must all hold before any claim entitlement could exist.
# The pilot satisfies none of the binding ones, by design.
COVERAGE_PREREQUISITES: tuple[str, ...] = (
    "protection_active_per_activation_gate",
    "approved_versioned_terms_in_force",
    "named_human_claims_reviewer_and_payment_approver",
    "funded_financial_security",
)

# Inputs that may never establish a rate or a payable coverage decision.
NON_RATING_INPUTS: tuple[str, ...] = (
    "scan_counts",
    "aggregate_scanner_score",
    "receipt_status",
    "subscription_charge",
    "referral_attribution",
)


def assessment_eligible(*, has_mandate: bool, has_receipt: bool) -> bool:
    """Whether a task can be *assessed*. Says nothing about entitlement."""
    return bool(has_mandate and has_receipt)


def claim_entitlement(*, protection_active: bool) -> bool:
    """Whether anything could be owed. False for the whole pilot."""
    return bool(protection_active)


def coverage_decision(
    *,
    protection_active: bool,
    execution_state: str,
    has_independent_evidence: bool,
) -> str:
    """Decide how far a candidate travels. Never denies on agent deviation.

    A mandate deviation is the mistake under consideration, so it can only
    move a candidate toward human review, never to a denial.
    """
    if not protection_active:
        return INELIGIBLE_PROTECTION_NOT_ACTIVE
    if not has_independent_evidence or execution_state == EXECUTION_UNKNOWN:
        return RESERVED_PENDING_EVIDENCE
    return ELIGIBLE_FOR_HUMAN_REVIEW


def as_dict() -> dict[str, Any]:
    return {
        "program_requirements_version": PROGRAM_REQUIREMENTS_VERSION,
        "limits": {
            "per_event_cents": LIMITS.per_event_cents,
            "per_member_aggregate_cents": LIMITS.per_member_aggregate_cents,
            "cohort_capacity_cents": LIMITS.cohort_capacity_cents,
        },
        "coverage_prerequisites": list(COVERAGE_PREREQUISITES),
        "non_rating_inputs": list(NON_RATING_INPUTS),
        "payment_status": NO_PAYMENT_AUTHORIZED,
    }
