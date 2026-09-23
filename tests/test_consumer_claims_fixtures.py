"""Replay the BEAAA-35012 fixtures from BEAAA-34392 against the loaded contract.

Each fixture states the coverage decision a reviewer should land on. The pilot
must reach the same one from the same facts. A fixture the pilot cannot
satisfy is a finding to report, not a test to weaken.
"""

from __future__ import annotations

import unittest

from stage_b_pilot import program

# The fixtures describe the mandate-versus-evidence comparison in their own
# vocabulary. This is the only translation layer, and it is exhaustive.
MISMATCH_STATUS_TO_EXECUTION_STATE = {
    "none": program.EXECUTION_MATCH,
    "candidate_event": program.EXECUTION_MISMATCH,
    "unresolved": program.EXECUTION_UNKNOWN,
}


class ClaimsReviewFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixtures = program.load_fixtures()

    def test_all_five_fixtures_are_present(self) -> None:
        self.assertEqual(len(self.fixtures), 5)

    def test_pilot_reaches_each_fixture_coverage_decision(self) -> None:
        for fixture in self.fixtures:
            with self.subTest(fixture=fixture["fixture_id"]):
                mismatch = fixture["execution_mismatch"]
                outcome = fixture["independent_actual_order_outcome"]
                decision = program.coverage_decision(
                    protection_active=(
                        fixture["task_protection"]["status"] == "confirmed"
                    ),
                    execution_state=MISMATCH_STATUS_TO_EXECUTION_STATE[
                        mismatch["status"]
                    ],
                    has_independent_evidence=outcome["status"] == "verified",
                    candidate_event=mismatch["candidate_event"],
                )
                self.assertEqual(decision, fixture["coverage_decision"]["status"])

    def test_no_fixture_authorises_payment(self) -> None:
        for fixture in self.fixtures:
            with self.subTest(fixture=fixture["fixture_id"]):
                self.assertEqual(
                    fixture["payment_status"]["status"], program.NO_PAYMENT_AUTHORIZED
                )

    def test_fixture_per_task_limit_matches_the_requirements(self) -> None:
        for fixture in self.fixtures:
            with self.subTest(fixture=fixture["fixture_id"]):
                self.assertEqual(
                    fixture["program"]["per_task_limit_minor"],
                    program.LIMITS.per_event_cents,
                )

    def test_deviation_from_the_mandate_never_denies(self) -> None:
        """The mistake under review cannot be the reason to refuse review."""
        deviating = [
            f
            for f in self.fixtures
            if f["execution_mismatch"]["status"] == "candidate_event"
        ]
        self.assertTrue(deviating)
        for fixture in deviating:
            with self.subTest(fixture=fixture["fixture_id"]):
                self.assertNotEqual(
                    fixture["coverage_decision"]["status"], "declined"
                )
                self.assertIsNone(fixture["coverage_decision"]["decision"])

    def test_reviewer_is_never_the_final_decider_by_machine(self) -> None:
        self.assertEqual(program.PROHIBITED_FINAL_DECIDER, "llm_only")
        for fixture in self.fixtures:
            with self.subTest(fixture=fixture["fixture_id"]):
                self.assertIsNone(fixture["coverage_decision"]["decision"])


if __name__ == "__main__":
    unittest.main()
