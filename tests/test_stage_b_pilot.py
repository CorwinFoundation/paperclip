"""Acceptance tests for the Stage B instrumented pilot."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from stage_a_demo.activation import MappingRegistry, NO_COVERAGE_STATEMENT
from stage_a_demo.core import EvidenceStore
from stage_b_pilot import MODE, program
from stage_b_pilot.activation_view import default_program_version, pilot_status
from stage_b_pilot.agent import ReferenceAgent, TaskIntent
from stage_b_pilot.claims import MandateRecord, build_packet
from stage_b_pilot.pilot import (
    MISTAKE_DEVIATION,
    MISTAKE_INTENT,
    check_mandate,
    derive_mandate,
    run_seeded_scenarios,
    run_task,
)
from stage_b_pilot.telemetry import Telemetry, UntruthfulStateError


def _active_records(program_version: str) -> MappingRegistry:
    return MappingRegistry(
        {
            program_version: {
                "governing_legal_structure": {
                    "record_id": "r1",
                    "program_version": program_version,
                    "status": "in_good_standing",
                    "entity_name": "BackBond",
                    "jurisdiction": "DE",
                },
                "executed_backing_authority": {
                    "record_id": "r2",
                    "program_version": program_version,
                    "status": "executed",
                    "counterparty": "Carrier",
                    "executed_at": "2026-09-01T00:00:00Z",
                    "document_sha256": "a" * 64,
                },
                "funded_financial_security": {
                    "record_id": "r3",
                    "program_version": program_version,
                    "status": "funded",
                    "instrument": "trust",
                    "funded_amount_cents": 100000,
                    "funded_at": "2026-09-01T00:00:00Z",
                },
                "approved_versioned_terms": {
                    "record_id": "r4",
                    "program_version": program_version,
                    "status": "approved",
                    "terms_version": "t-1",
                    "approved_at": "2026-09-01T00:00:00Z",
                    "expires_at": "2099-01-01T00:00:00Z",
                },
                "named_claims_and_payment_humans": {
                    "record_id": "r5",
                    "program_version": program_version,
                    "status": "named",
                    "claims_reviewer": "reviewer",
                    "payment_approver": "approver",
                },
                "approved_consumer_disclosure_set": {
                    "record_id": "r6",
                    "program_version": program_version,
                    "status": "approved",
                    "disclosure_set_id": "d-1",
                    "approved_at": "2026-09-01T00:00:00Z",
                },
            }
        }
    )


class SeededScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EvidenceStore(Path(self.tmp.name) / "pilot.sqlite3")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.store.close)
        self.output = run_seeded_scenarios(self.store)

    def test_clean_scenario_matches_and_raises_no_candidate(self) -> None:
        clean = self.output["scenarios"]["clean"]
        self.assertEqual(clean["receipt"]["status"], program.EXECUTION_MATCH)
        self.assertEqual(clean["mandate_deviations"], [])
        self.assertIsNone(clean["covered_event_candidate"])
        self.assertEqual(clean["observed_loss_cents"], 0)

    def test_covered_mistake_reaches_the_claims_packet(self) -> None:
        packet = self.output["scenarios"]["covered_mistake"]["covered_event_candidate"]
        self.assertIsNotNone(packet)
        for key in (
            "original_human_intent",
            "mandate",
            "independent_order_outcome",
            "duplicate_proof",
            "loss_and_refund_history",
            "program_version",
            "missing_evidence_reason_codes",
            "reviewer_explanation",
        ):
            self.assertIn(key, packet)
        self.assertEqual(packet["mandate"]["mandate_version"], "mandate-v1")
        self.assertEqual(packet["execution"]["state"], program.EXECUTION_MISMATCH)
        self.assertIsNotNone(packet["duplicate_proof"]["idempotency_key"])
        self.assertEqual(packet["duplicate_proof"]["authoritative_executions"], 1)
        self.assertEqual(
            packet["limits"]["per_event_cents"], program.LIMITS.per_event_cents
        )

    def test_execution_coverage_and_payment_stay_separate(self) -> None:
        packet = self.output["scenarios"]["covered_mistake"]["covered_event_candidate"]
        self.assertEqual(packet["execution"]["state"], program.EXECUTION_MISMATCH)
        self.assertEqual(
            packet["coverage_decision"], program.INELIGIBLE_PROTECTION_NOT_ACTIVE
        )
        self.assertEqual(packet["payment_status"], program.NO_PAYMENT_AUTHORIZED)

    def test_telemetry_counts_are_readable_and_correct(self) -> None:
        counts = self.output["telemetry"]["counts"]
        self.assertEqual(counts["tasks_delegated"], 2)
        self.assertEqual(counts["mandate_deviations"], 1)
        self.assertEqual(counts["receipts_produced"], 2)
        self.assertEqual(counts["covered_event_candidates_raised"], 1)
        self.assertEqual(
            counts["user_facing_states_shown"],
            len(self.output["telemetry"]["user_facing_states"]),
        )

    def test_no_state_renders_live_coverage(self) -> None:
        self.assertFalse(self.output["protection"]["protection_active"])
        for state in self.output["telemetry"]["user_facing_states"]:
            self.assertEqual(state["mode"], MODE)
            self.assertNotEqual(state["mode"], "LIVE")
            self.assertEqual(state["protection_statement"], NO_COVERAGE_STATEMENT)


class ProgramRuleTests(unittest.TestCase):
    def test_deviation_cannot_auto_deny_the_mistake(self) -> None:
        decision = program.coverage_decision(
            protection_active=True,
            execution_state=program.EXECUTION_MISMATCH,
            has_independent_evidence=True,
        )
        self.assertEqual(decision, program.ELIGIBLE_FOR_HUMAN_REVIEW)

    def test_assessment_eligibility_is_not_claim_entitlement(self) -> None:
        self.assertTrue(program.assessment_eligible(has_mandate=True, has_receipt=True))
        self.assertFalse(program.claim_entitlement(protection_active=False))

    def test_unknown_evidence_reserves_instead_of_denying(self) -> None:
        decision = program.coverage_decision(
            protection_active=True,
            execution_state=program.EXECUTION_UNKNOWN,
            has_independent_evidence=False,
        )
        self.assertEqual(decision, program.RESERVED_PENDING_EVIDENCE)

    def test_per_event_ceiling_caps_the_observed_loss(self) -> None:
        self.assertEqual(
            program.LIMITS.ceiling_for(10_000_000), program.LIMITS.per_event_cents
        )


class GateAndTelemetryTests(unittest.TestCase):
    def test_active_records_flip_the_decision_but_not_the_payment(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EvidenceStore(Path(tmp.name) / "active.sqlite3")
        self.addCleanup(store.close)
        version = default_program_version()
        status = pilot_status(registry=_active_records(version))
        self.assertTrue(status.active)
        telemetry = Telemetry()
        run = run_task(
            intent=MISTAKE_INTENT,
            agent=ReferenceAgent(deviation=MISTAKE_DEVIATION),
            store=store,
            telemetry=telemetry,
            status=status,
        )
        packet = run.covered_event_candidate
        self.assertIsNotNone(packet)
        self.assertEqual(
            packet.coverage_decision, program.ELIGIBLE_FOR_HUMAN_REVIEW
        )
        self.assertEqual(packet.payment_status, program.NO_PAYMENT_AUTHORIZED)

    def test_telemetry_rejects_an_untruthful_state(self) -> None:
        telemetry = Telemetry()
        with self.assertRaises(UntruthfulStateError):
            telemetry.show(
                "bad",
                mode="LIVE",
                headline="You are covered.",
                protection_active=False,
                protection_statement=NO_COVERAGE_STATEMENT,
            )
        with self.assertRaises(UntruthfulStateError):
            telemetry.show(
                "bad",
                mode=MODE,
                headline="You are covered.",
                protection_active=False,
                protection_statement="Protection is active.",
            )
        self.assertEqual(telemetry.counts()["user_facing_states_shown"], 0)

    def test_missing_evidence_reason_codes_are_recorded(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EvidenceStore(Path(tmp.name) / "incomplete.sqlite3")
        self.addCleanup(store.close)
        telemetry = Telemetry()
        run = run_task(
            intent=TaskIntent(
                intent_id="incomplete-001",
                task_type="purchase",
                description="Buy the filter coffee.",
                sku="SKU-COFFEE-500G",
                max_amount_cents=1800,
            ),
            agent=ReferenceAgent(),
            store=store,
            telemetry=telemetry,
            independent_evidence=False,
        )
        packet = run.covered_event_candidate
        self.assertIsNotNone(packet)
        self.assertIn(
            program.REASON_NO_INDEPENDENT_EVIDENCE, packet.missing_evidence_reason_codes
        )
        self.assertIn(
            program.REASON_PROTECTION_NOT_ACTIVE, packet.missing_evidence_reason_codes
        )

    def test_consumer_can_initiate_a_claim_the_detector_missed(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EvidenceStore(Path(tmp.name) / "consumer.sqlite3")
        self.addCleanup(store.close)
        telemetry = Telemetry()
        intent = TaskIntent(
            intent_id="consumer-001",
            task_type="purchase",
            description="Buy the filter coffee.",
            sku="SKU-COFFEE-500G",
            max_amount_cents=1800,
        )
        run = run_task(
            intent=intent,
            agent=ReferenceAgent(),
            store=store,
            telemetry=telemetry,
        )
        self.assertIsNone(run.covered_event_candidate)
        packet = build_packet(
            case_id=run.receipt_packet["case_id"],
            intent={"intent_id": intent.intent_id},
            mandate=derive_mandate(intent),
            receipt_packet=run.receipt_packet,
            status=pilot_status(),
            loss_cents=500,
            initiated_by="consumer",
            plan_cancelled=True,
        )
        self.assertEqual(packet.initiated_by, "consumer")
        self.assertTrue(packet.prior_rights_preserved)

    def test_unsupported_task_type_is_refused(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EvidenceStore(Path(tmp.name) / "unsupported.sqlite3")
        self.addCleanup(store.close)
        with self.assertRaises(ValueError):
            run_task(
                intent=TaskIntent(
                    intent_id="x",
                    task_type="wire_transfer",
                    description="Send money.",
                    sku="SKU-X",
                    max_amount_cents=100,
                ),
                agent=ReferenceAgent(),
                store=store,
                telemetry=Telemetry(),
            )


class MandateCheckTests(unittest.TestCase):
    def test_check_mandate_reports_both_fields(self) -> None:
        mandate = MandateRecord("m", "mandate-v1", "SKU-A", 1000, "i")
        from stage_b_pilot.agent import PlannedAction

        deviations = check_mandate(
            mandate, PlannedAction(sku="SKU-B", amount_cents=2000, rationale="r")
        )
        self.assertEqual({d["field"] for d in deviations}, {"sku", "amount_cents"})


if __name__ == "__main__":
    unittest.main()
