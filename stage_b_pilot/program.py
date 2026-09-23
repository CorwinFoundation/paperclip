"""Program rules for the Stage B pilot, loaded from the landed contracts.

The rules live in ``contracts/consumer/`` as the versioned artifacts they
already were on BEAAA-34391 and BEAAA-34392. This module reads them; it does
not restate them. Where an earlier hand-encoding disagreed with an artifact,
the artifact won - see BEAAA-35015 for the list.

Two rules here are load-bearing and are asserted by the tests:

* Assessment eligibility is not claim entitlement. Reaching review is not
  being owed anything.
* An agent deviating from its mandate cannot automatically deny the very
  mistake being considered for coverage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts" / "consumer"

REQUIREMENTS_FILENAME = "consumer-program-requirements.v0.1.0-stage-a.json"
FIXTURES_FILENAME = "consumer-claims-review-fixtures-v0.1.json"
PACKET_CONTRACT_FILENAME = "consumer-claims-review-contract-v0.1.md"


@lru_cache(maxsize=None)
def load_requirements() -> dict[str, Any]:
    """The BEAAA-34391 consumer program requirements, verbatim."""
    with (CONTRACTS_DIR / REQUIREMENTS_FILENAME).open(encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=None)
def load_fixtures() -> list[dict[str, Any]]:
    """The BEAAA-34392 claims-review packet fixtures, verbatim."""
    with (CONTRACTS_DIR / FIXTURES_FILENAME).open(encoding="utf-8") as handle:
        return list(json.load(handle)["fixtures"])


_REQUIREMENTS = load_requirements()
_MOCK_CONFIG = _REQUIREMENTS["stage_a_mock_configuration"]
_DOMAINS = _REQUIREMENTS["decision_domains"]

PROGRAM_REQUIREMENTS_VERSION = _REQUIREMENTS["schema_version"]
PACKET_FIXTURES_VERSION = "claims-review-packet-fixtures.v0.1"
CURRENCY = _MOCK_CONFIG["currency"]
STARTER_PERIOD_DAYS = _MOCK_CONFIG["starter_period_days"]

# Coverage decision values, taken from the fixture vocabulary rather than the
# pilot's own invented names. Kept separate from execution state and from
# payment status per BEAAA-34392.
NOT_ASSESSED = "not_evaluated"
ELIGIBLE_FOR_HUMAN_REVIEW = "reviewing"
RESERVED_PENDING_EVIDENCE = "evidence_needed"
INELIGIBLE_PROTECTION_NOT_ACTIVE = "ineligible_protection_not_active"

# The nine states a claim may occupy once the program is real. The pilot
# reaches only the first three; the rest need a human reviewer and a payer.
CLAIM_ENTITLEMENT_STATES: tuple[str, ...] = tuple(
    _DOMAINS["claim_entitlement"]["allowed_states"]
)

# Payment status. The pilot has no payment rail and no payment authority.
NO_PAYMENT_AUTHORIZED = "not_authorized"

# Execution states, produced by comparing mandate against independent evidence.
EXECUTION_MATCH = "MATCH"
EXECUTION_MISMATCH = "MISMATCH"
EXECUTION_UNKNOWN = "UNKNOWN/INCOMPLETE"

# Missing-evidence reason codes.
REASON_NO_INDEPENDENT_EVIDENCE = "no_independent_merchant_evidence"
REASON_NO_EXECUTION_RECORD = "no_execution_record"
REASON_PROTECTION_NOT_ACTIVE = "protection_not_active"

# The two workflows the program covers, and the events that make a task a
# candidate. Both carry automatic_denial_if_agent_deviated: false.
COVERED_WORKFLOWS: tuple[str, ...] = tuple(_MOCK_CONFIG["covered_workflows"])
DEFERRED_WORKFLOWS: tuple[str, ...] = tuple(_MOCK_CONFIG["deferred_workflows"])
COVERED_EVENT_CODES: tuple[str, ...] = tuple(
    event["code"] for event in _REQUIREMENTS["covered_event_candidates"]
)


@dataclass(frozen=True)
class Limits:
    """Limits as the requirements state them: per task, and per period.

    A limit is a ceiling on what a human reviewer could consider. It is not a
    promise to pay, and no value here is an approved benefit. There is no
    cohort figure because the requirements name ``cohort_program_aggregate``
    as a ledger that must exist and never state an amount for it.
    """

    per_event_cents: int
    program_period_cents: int
    currency: str = CURRENCY

    def ceiling_for(self, loss_cents: int) -> int:
        return max(0, min(loss_cents, self.per_event_cents))


LIMITS = Limits(
    per_event_cents=_MOCK_CONFIG[
        "illustrative_maximum_eligible_exposure_per_task_minor"
    ],
    program_period_cents=_MOCK_CONFIG["illustrative_program_period_limit_minor"],
)

# The ledgers the requirements say must be kept apart. The pilot keeps no
# money, but a packet that conflates these is wrong even when empty.
SEPARATE_LEDGERS: tuple[str, ...] = tuple(
    _REQUIREMENTS["financial_controls"]["separate_ledgers"]
)

# Everything that must hold before any claim entitlement could exist. These
# are the artifact's two prerequisite lists, kept in their own domains rather
# than collapsed into one summary. The pilot satisfies neither set.
ACTIVATION_PREREQUISITES: tuple[str, ...] = tuple(
    _DOMAINS["enrollment_activation"]["activation_prerequisites"]
)
TASK_CONFIRMATION_PREREQUISITES: tuple[str, ...] = tuple(
    _DOMAINS["task_protection"]["confirmation_prerequisites"]
)
COVERAGE_PREREQUISITES: tuple[str, ...] = (
    ACTIVATION_PREREQUISITES + TASK_CONFIRMATION_PREREQUISITES
)

# Inputs that may never establish a rate or a payable coverage decision.
NON_RATING_INPUTS: tuple[str, ...] = (
    "scan_counts",
    "aggregate_scanner_score",
    "receipt_status",
    "subscription_charge",
    "referral_attribution",
)

# A mandate deviation is the mistake under review, so it can never be a
# denial reason on its own. Stated by the artifact in two places.
MANDATE_DEVIATION_RULE = _DOMAINS["claim_entitlement"]["mandate_deviation_rule"]
HISTORICAL_RIGHTS_RULE = _DOMAINS["task_protection"]["historical_rights_rule"]
PROHIBITED_FINAL_DECIDER = _DOMAINS["claim_entitlement"]["prohibited_final_decider"]


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
    candidate_event: bool | None = None,
) -> str:
    """Decide how far a candidate travels. Never denies on agent deviation.

    ``candidate_event`` is the artifact's own gate: when nothing went wrong
    there is no coverage question to answer, and the honest answer is
    ``not_evaluated`` rather than a review a reviewer has no reason to open.
    Left unset it is derived from ``execution_state``.
    """
    if candidate_event is None:
        candidate_event = execution_state != EXECUTION_MATCH
    if not candidate_event:
        return NOT_ASSESSED
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
            "program_period_cents": LIMITS.program_period_cents,
            "currency": LIMITS.currency,
        },
        "covered_workflows": list(COVERED_WORKFLOWS),
        "covered_event_codes": list(COVERED_EVENT_CODES),
        "coverage_prerequisites": list(COVERAGE_PREREQUISITES),
        "non_rating_inputs": list(NON_RATING_INPUTS),
        "payment_status": NO_PAYMENT_AUTHORIZED,
    }
