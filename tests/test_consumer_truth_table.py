"""Every row of the contract's scenario truth table, run through the pilot.

`scenario_truth_table` in the landed BEAAA-34391 requirements is a row-per-case
table of expected outcomes, so the contract ships its own oracle. Landing the
file (BEAAA-35015, 52891dec) stopped `program.py` restating the rules; it did
not make anything check that the pilot still answers the way the table says.
This does.

Two buckets, and every row is in exactly one:

* `CONFORMING` - the pilot has the inputs the row turns on, so the row is
  driven through `program` and its answer is asserted.
* `GAPS` - the pilot has no input for what the row turns on. The row is not
  weakened into a passing assertion. Its current answer is pinned together
  with the reason it is wrong or incomplete, so the gap is visible in CI and
  closing it fails here until this table is updated too.

Each case restates the row fields it depends on and asserts them against the
landed file first. If a future contract version changes a row, the premise
assertion fails before the behaviour assertion does, which is the difference
between "the pilot drifted" and "the contract moved".
"""

from __future__ import annotations

import unittest

from stage_b_pilot import program

# --- row id -> what the pilot was asked, and what it must answer -----------
#
# `premise` is the subset of the contract row this case depends on.
# `inputs` are the arguments to `program.coverage_decision`.

CONFORMING = {
    "S01_CORRECT_PURCHASE": {
        "premise": {"execution_state": "succeeded", "claim_intake": "not_triggered"},
        "inputs": {
            "protection_active": True,
            "execution_state": program.EXECUTION_MATCH,
            "has_independent_evidence": True,
        },
        "expected": program.NOT_ASSESSED,
        "why": "Nothing went wrong, so there is no coverage question to answer.",
    },
    "S02_WRONG_AMOUNT_OR_SKU": {
        "premise": {
            "incident_state": "reviewable_candidate",
            "claim_intake": "accepted_for_review",
        },
        "inputs": {
            "protection_active": True,
            "execution_state": program.EXECUTION_MISMATCH,
            "has_independent_evidence": True,
        },
        "expected": program.ELIGIBLE_FOR_HUMAN_REVIEW,
        "why": "Deviation from the mandate opens a review; it never denies.",
    },
    "S03_DUPLICATE_TRANSPORT_RETRY": {
        "premise": {"execution_state": "succeeded", "claim_intake": "not_triggered"},
        "inputs": {
            "protection_active": True,
            "execution_state": program.EXECUTION_MATCH,
            "has_independent_evidence": True,
        },
        "expected": program.NOT_ASSESSED,
        "why": "One authoritative order survives the replay, so it is S01.",
        "caveat": (
            "The pilot does not do the de-duplication. The row's "
            "same_idempotency_key_and_digest collapse is assumed upstream; "
            "this only asserts the decision on the collapsed view."
        ),
    },
    "S04_ACTUAL_DUPLICATE_PURCHASE": {
        "premise": {
            "execution_state": "succeeded_with_excess_execution",
            "claim_intake": "accepted_for_review",
        },
        "inputs": {
            "protection_active": True,
            "execution_state": program.EXECUTION_MISMATCH,
            "has_independent_evidence": True,
        },
        "expected": program.ELIGIBLE_FOR_HUMAN_REVIEW,
        "why": "Two authoritative orders against one authorization is a review.",
        "caveat": (
            "succeeded_with_excess_execution has no counterpart in the pilot's "
            "three execution states; it is carried here as MISMATCH, which "
            "reaches the same decision but loses the reason."
        ),
    },
    "S05_MISSING_CALLBACK": {
        "premise": {
            "execution_state": "unknown",
            "incident_state": "evidence_incomplete",
            "claim_intake": "accepted_if_reported",
        },
        "inputs": {
            "protection_active": True,
            "execution_state": program.EXECUTION_UNKNOWN,
            "has_independent_evidence": False,
        },
        "expected": program.RESERVED_PENDING_EVIDENCE,
        "why": "An unreconciled provider state is held, not decided.",
    },
    "S07_CHANGED_INTENT": {
        "premise": {"execution_state": "not_started", "claim_intake": "not_triggered"},
        "inputs": None,  # assessment_eligible, not coverage_decision
        "expected": False,
        "why": "No execution means no receipt, so there is nothing to assess.",
    },
}

GAPS = {
    "S06_FORGED_OR_SELF_APPROVAL": {
        "premise": {
            "incident_state": "authorization_failure",
            "claim_intake": "review_if_execution_occurred",
        },
        "gap": (
            "coverage_decision takes no authorization-provenance input, so the "
            "pilot cannot tell an agent-token self-approval from an independent "
            "human authorization. It answers on execution state alone."
        ),
        "current": program.ELIGIBLE_FOR_HUMAN_REVIEW,
        "inputs": {
            "protection_active": True,
            "execution_state": program.EXECUTION_MISMATCH,
            "has_independent_evidence": True,
        },
    },
    "S08_CONCURRENT_LIMIT_CONSUMPTION": {
        "premise": {
            "execution_state": "mixed_as_authoritatively_committed",
            "incident_state": "none_or_capacity_referral",
        },
        "gap": (
            "The pilot holds limits as ceilings only. It has no reservation "
            "ledger, so simultaneous reservation attempts against a shared "
            "limit are unrepresentable and no capacity referral can be raised."
        ),
        "current": None,
        "inputs": None,
    },
    "S09_ACTIVATION_TIMEOUT": {
        "premise": {
            "execution_state": "not_applicable",
            "incident_state": "activation_pending",
            "claim_intake": "not_triggered",
        },
        "gap": (
            "ActivationStatus is a boolean. An activation whose authoritative "
            "result is unknown reads as not active, so the pilot returns "
            "ineligible_protection_not_active where the row wants pending. "
            "Fails closed, which is safe, but conflates unknown with no."
        ),
        "current": program.INELIGIBLE_PROTECTION_NOT_ACTIVE,
        "inputs": {
            "protection_active": False,
            "execution_state": program.EXECUTION_UNKNOWN,
            "has_independent_evidence": False,
        },
    },
    "S10_REFUND_AFTER_PAYOUT": {
        "premise": {
            "incident_state": "recovery_pending_or_recorded",
            "claim_intake": "existing_claim_updated",
        },
        "gap": (
            "build_packet carries refunds on a packet, but the pilot persists "
            "no claim and authorizes no payout, so there is no existing claim "
            "for a later chargeback to update and no recovery to record."
        ),
        "current": None,
        "inputs": None,
    },
    "S11_EXPIRY_OR_DISCONNECTION": {
        "premise": {
            "execution_state": "historical",
            "incident_state": "preserved",
            "claim_intake": "available_under_governing_terms",
        },
        "gap": (
            "program.HISTORICAL_RIGHTS_RULE is loaded from the contract and "
            "never consulted. coverage_decision reads protection_active as of "
            "review time, so a claim whose occurrence predates the "
            "disconnection is refused rather than preserved. This is the one "
            "gap that decides against a person the contract says is covered."
        ),
        "current": program.INELIGIBLE_PROTECTION_NOT_ACTIVE,
        "inputs": {
            "protection_active": False,
            "execution_state": program.EXECUTION_MISMATCH,
            "has_independent_evidence": True,
        },
    },
    "S12_SHARED_AGENT_FAILURE": {
        "premise": {
            "execution_state": "varies",
            "incident_state": "group_control_active",
            "claim_intake": "remains_available",
        },
        "gap": (
            "Correlated failure across a common vendor or connector is a "
            "portfolio-level control. The pilot decides one task at a time and "
            "has no grouping key, so group_control_active cannot be raised."
        ),
        "current": None,
        "inputs": None,
    },
}


def _rows() -> dict[str, dict]:
    table = program.load_requirements()["scenario_truth_table"]
    return {row["id"]: row for row in table}


class TruthTableCoverageTests(unittest.TestCase):
    """The table is covered exhaustively and the split is honest."""

    def test_every_row_is_classified_exactly_once(self) -> None:
        rows = set(_rows())
        classified = set(CONFORMING) | set(GAPS)
        self.assertEqual(
            rows,
            classified,
            "a contract row is neither driven nor named as a gap",
        )
        self.assertFalse(set(CONFORMING) & set(GAPS))

    def test_row_count_is_pinned(self) -> None:
        self.assertEqual(len(_rows()), 12)

    def test_every_case_restates_the_row_it_depends_on(self) -> None:
        rows = _rows()
        for row_id, case in {**CONFORMING, **GAPS}.items():
            with self.subTest(row=row_id):
                row = rows[row_id]
                for field, value in case["premise"].items():
                    self.assertEqual(
                        row[field],
                        value,
                        f"{row_id}.{field} moved in the contract",
                    )

    def test_every_gap_states_a_reason(self) -> None:
        for row_id, case in GAPS.items():
            with self.subTest(row=row_id):
                self.assertGreater(len(case["gap"]), 80, "name the gap")


class ConformingRowTests(unittest.TestCase):
    """Rows the pilot has the inputs for must answer the way the row says."""

    def test_coverage_decisions_match_the_table(self) -> None:
        for row_id, case in CONFORMING.items():
            if case["inputs"] is None:
                continue
            with self.subTest(row=row_id):
                self.assertEqual(
                    program.coverage_decision(**case["inputs"]),
                    case["expected"],
                    case["why"],
                )

    def test_changed_intent_is_not_assessable(self) -> None:
        case = CONFORMING["S07_CHANGED_INTENT"]
        self.assertEqual(
            program.assessment_eligible(has_mandate=True, has_receipt=False),
            case["expected"],
        )

    def test_a_clean_run_never_opens_a_review(self) -> None:
        """S01 and S03 are the rows a false positive would show up in."""
        for evidence in (True, False):
            self.assertEqual(
                program.coverage_decision(
                    protection_active=True,
                    execution_state=program.EXECUTION_MATCH,
                    has_independent_evidence=evidence,
                ),
                program.NOT_ASSESSED,
            )


class GapRowTests(unittest.TestCase):
    """Gaps are pinned, not weakened. Closing one fails here on purpose."""

    def test_current_answers_are_pinned(self) -> None:
        for row_id, case in GAPS.items():
            if case["inputs"] is None:
                self.assertIsNone(case["current"], row_id)
                continue
            with self.subTest(row=row_id):
                self.assertEqual(
                    program.coverage_decision(**case["inputs"]),
                    case["current"],
                    f"{row_id} changed; update this table and the gap note",
                )

    def test_historical_rights_rule_is_still_unenforced(self) -> None:
        """S11's gap, asserted at the mechanism rather than the outcome.

        The rule is text in the contract the pilot reads and does not apply.
        When someone wires it in, this fails and S11 moves to CONFORMING.
        """
        self.assertIsInstance(program.HISTORICAL_RIGHTS_RULE, str)
        self.assertEqual(
            program.coverage_decision(
                protection_active=False,
                execution_state=program.EXECUTION_MISMATCH,
                has_independent_evidence=True,
            ),
            program.INELIGIBLE_PROTECTION_NOT_ACTIVE,
        )


if __name__ == "__main__":
    unittest.main()
