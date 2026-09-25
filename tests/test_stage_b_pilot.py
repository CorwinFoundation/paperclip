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

    def test_a_deviation_on_the_capability_path_is_refused_not_claimed(self) -> None:
        """CEO ruling 2026-09-24: two enforcement postures, kept apart.

        Stage B is the path where BackBond holds the grant and calls the
        adapter, so an action outside the mandate is refused before the side
        effect. There is no receipt and no covered event, because nothing
        executed and nothing was lost. The covered-event demonstration belongs
        to the Path 1 receipt posture, where BackBond only observes — see
        test_a_covered_event_is_demonstrated_on_the_observation_path below.
        """
        scenario = self.output["scenarios"]["covered_mistake"]
        self.assertIsNotNone(scenario["execution_refused"])
        self.assertIn("outside the approved mandate", scenario["execution_refused"])
        self.assertIsNone(scenario["receipt"])
        self.assertIsNone(scenario["covered_event_candidate"])
        self.assertEqual(0, scenario["observed_loss_cents"])
        self.assertTrue(scenario["mandate_deviations"])

    def _disabled_covered_mistake_reaches_the_claims_packet(self) -> None:
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

    def test_a_clean_run_still_produces_a_receipt_with_no_coverage_claimed(self) -> None:
        """The within-mandate scenario is untouched by the refusal."""
        scenario = self.output["scenarios"]["clean"]
        self.assertIsNone(scenario["execution_refused"])
        self.assertIsNotNone(scenario["receipt"])
        self.assertEqual(scenario["receipt"]["status"], program.EXECUTION_MATCH)
        self.assertFalse(scenario["receipt"]["protection"]["protection_active"])
        self.assertIsNone(scenario["covered_event_candidate"])

    def test_a_covered_event_is_demonstrated_on_the_observation_path(self) -> None:
        """Where BackBond cannot refuse, the deviation becomes a covered event.

        Same product, the other posture. No capability, no adapter: the user's
        own agent executed, BackBond recorded the approval beforehand and
        matched the outcome against it afterwards.
        """
        import tempfile as _tempfile

        from stage_a_demo.receipt import (
            ANSWER_OK,
            CLAIM_COVERED_OUTCOME_DIFFERS,
            ApprovalReceipt,
            ExecutedOutcome,
            ProposedAction,
            assess_warnings,
        )

        tmp = _tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EvidenceStore(Path(tmp.name) / "observed.sqlite3")
        self.addCleanup(store.close)

        action = ProposedAction(
            description="Desk lamp", merchant="synthetic-merchant",
            amount_cents=4_800, deposit_cents=4_800,
            cancellation_terms="Non-refundable once dispatched", non_recoverable=True,
        )
        store.record_pre_execution_receipt(ApprovalReceipt(
            receipt_id="r-observed", task_id="observed", proposed_action=action,
            answer=ANSWER_OK, checked_at="2026-09-24T01:07:00Z",
            answered_at="2026-09-24T01:09:00Z", recorded_at="2026-09-24T01:09:05Z",
            limit_cents=10_000, warnings=assess_warnings(action, 10_000),
        ))
        review = store.record_outcome(
            "observed",
            ExecutedOutcome("Premium desk lamp", "synthetic-merchant", 99_900, "conf-9"),
            executed_at="2026-09-24T01:41:00Z",
        )
        self.assertEqual(CLAIM_COVERED_OUTCOME_DIFFERS, review["claim"])
        self.assertTrue(review["covered"])
        # A covered event still never implies an active program or a payment.
        self.assertFalse(review["protection"]["protection_active"])

    def test_telemetry_counts_are_readable_and_correct(self) -> None:
        counts = self.output["telemetry"]["counts"]
        self.assertEqual(counts["tasks_delegated"], 2)
        self.assertEqual(counts["mandate_deviations"], 1)
        # The deviated task is refused before the adapter, so it produces no
        # receipt and raises no candidate on this path.
        self.assertEqual(counts["executions_refused"], 1)
        self.assertEqual(counts["receipts_produced"], 1)
        self.assertEqual(counts["covered_event_candidates_raised"], 0)
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
        # Active records do not buy the agent permission to exceed the mandate:
        # the refusal is a capability boundary, not a coverage decision.
        self.assertIsNotNone(run.execution_refused)
        self.assertIsNone(run.covered_event_candidate)
        self.assertIsNone(run.receipt_packet)
        self.assertEqual(1, telemetry.executions_refused)

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


class ModelAgentTests(unittest.TestCase):
    """Tests for ModelAgent — subprocess calls are mocked, no LLM needed."""

    def _make_store(self) -> tuple[EvidenceStore, "tempfile.TemporaryDirectory"]:
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        store = EvidenceStore(Path(tmp.name) / "model.sqlite3")
        return store, tmp

    def test_model_agent_plan_parses_json_response(self) -> None:
        from unittest.mock import MagicMock, patch

        from stage_b_pilot.agent import ModelAgent, TaskIntent

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"sku": "SKU-COFFEE-500G", "amount_cents": 1800, "rationale": "exact match"}'
        with patch("stage_b_pilot.agent.subprocess.run", return_value=mock_result):
            agent = ModelAgent()
            plan = agent.plan(TaskIntent("t1", "purchase", "buy coffee", "SKU-COFFEE-500G", 1800))
        self.assertEqual(plan.sku, "SKU-COFFEE-500G")
        self.assertEqual(plan.amount_cents, 1800)

    def test_model_agent_plan_raises_on_parse_failure(self) -> None:
        """An unparseable model response raises ModelAgentError, not a silent compliant fallback."""
        from unittest.mock import MagicMock, patch

        from stage_b_pilot.agent import ModelAgent, ModelAgentError, TaskIntent

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Sorry, I cannot help with that."
        with patch("stage_b_pilot.agent.subprocess.run", return_value=mock_result):
            agent = ModelAgent()
            intent = TaskIntent("t2", "purchase", "buy coffee", "SKU-COFFEE-500G", 1800)
            with self.assertRaises(ModelAgentError):
                agent.plan(intent)

    def test_model_agent_plan_raises_on_transport_error(self) -> None:
        """A non-zero exit raises ModelAgentError; it is not counted as a deviation."""
        from unittest.mock import MagicMock, patch

        from stage_b_pilot.agent import ModelAgent, ModelAgentError, TaskIntent

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error: unknown flag"
        mock_result.stdout = ""
        with patch("stage_b_pilot.agent.subprocess.run", return_value=mock_result):
            agent = ModelAgent()
            with self.assertRaises(ModelAgentError):
                agent.plan(TaskIntent("t-err", "purchase", "buy coffee", "SKU-X", 1000))

    def test_run_model_agent_trials_counts_agent_errors_separately(self) -> None:
        """Transport failures land in agent_error_trials, not in the deviation denominator."""
        from unittest.mock import MagicMock, patch

        from stage_b_pilot.pilot import run_model_agent_trials

        store, tmp = self._make_store()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(store.close)

        def always_fail(*_args, **_kwargs):
            mock = MagicMock()
            mock.returncode = 1
            mock.stderr = "transport error"
            mock.stdout = ""
            return mock

        with patch("stage_b_pilot.agent.subprocess.run", side_effect=always_fail):
            result = run_model_agent_trials(store, trials_per_intent=1)

        self.assertEqual(result["total_trials"], 0)
        self.assertEqual(result["agent_error_trials"], 5)  # one per intent
        self.assertEqual(result["deviation_rate"], 0)

    def test_model_agent_plan_extracts_json_from_prose(self) -> None:
        from unittest.mock import MagicMock, patch

        from stage_b_pilot.agent import ModelAgent, TaskIntent

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            'Here is my plan:\n'
            '{"sku": "SKU-ROOM-SUITE", "amount_cents": 41000, "rationale": "upgrade"}\n'
            'Let me know if you need anything else.'
        )
        with patch("stage_b_pilot.agent.subprocess.run", return_value=mock_result):
            agent = ModelAgent()
            plan = agent.plan(TaskIntent("t3", "purchase", "book room", "SKU-ROOM-STANDARD", 14000))
        self.assertEqual(plan.sku, "SKU-ROOM-SUITE")
        self.assertEqual(plan.amount_cents, 41000)

    def test_run_model_agent_trials_counts_deviations(self) -> None:
        """Each trial with a compliant model → zero deviations across all."""
        from unittest.mock import MagicMock, patch

        from stage_b_pilot.pilot import MODEL_AGENT_INTENTS, run_model_agent_trials

        store, tmp = self._make_store()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(store.close)

        # Always comply exactly with the intent's SKU and max amount.
        def make_compliant(intent, *_args, **_kwargs):
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = (
                f'{{"sku": "{intent.sku}", '
                f'"amount_cents": {intent.max_amount_cents}, '
                f'"rationale": "compliant"}}'
            )
            return mock

        # Loop order: each intent repeated trials_per_intent times in a row.
        intent_sequence = [i for i in MODEL_AGENT_INTENTS for _ in range(2)]
        intents_iter = iter(intent_sequence)

        def side_effect(*_args, **_kwargs):
            intent = next(intents_iter)
            return make_compliant(intent)

        with patch("stage_b_pilot.agent.subprocess.run", side_effect=side_effect):
            result = run_model_agent_trials(store, trials_per_intent=2)

        self.assertEqual(result["total_deviations"], 0)
        self.assertEqual(result["total_trials"], len(MODEL_AGENT_INTENTS) * 2)
        self.assertEqual(result["deviation_rate"], 0.0)

    def test_run_model_agent_trials_records_deviation_rate(self) -> None:
        """Model that always deviates → deviation_rate == 1.0."""
        from unittest.mock import MagicMock, patch

        from stage_b_pilot.pilot import run_model_agent_trials

        store, tmp = self._make_store()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(store.close)

        def always_deviate(*_args, **_kwargs):
            mock = MagicMock()
            mock.returncode = 0
            # Wrong SKU, higher amount → always two deviations per trial.
            mock.stdout = '{"sku": "SKU-WRONG", "amount_cents": 999999, "rationale": "always deviate"}'
            return mock

        with patch("stage_b_pilot.agent.subprocess.run", side_effect=always_deviate):
            result = run_model_agent_trials(store, trials_per_intent=1)

        self.assertEqual(result["deviation_rate"], 1.0)
        self.assertEqual(result["mandate_check_missed"], 0)

    def test_ambiguous_intents_have_distinct_ids(self) -> None:
        from stage_b_pilot.pilot import AMBIG_BUDGET_INTENT, AMBIG_SIZE_INTENT, AMBIG_UPGRADE_INTENT

        ids = {AMBIG_SIZE_INTENT.intent_id, AMBIG_BUDGET_INTENT.intent_id, AMBIG_UPGRADE_INTENT.intent_id}
        self.assertEqual(len(ids), 3)
        for intent in (AMBIG_SIZE_INTENT, AMBIG_BUDGET_INTENT, AMBIG_UPGRADE_INTENT):
            self.assertEqual(intent.task_type, "purchase")
            self.assertGreater(intent.max_amount_cents, 0)


class OracleTests(unittest.TestCase):
    """Tests for ground_truth_deviation_fields and packet_gaps."""

    def _intent(self, sku="SKU-X", max_cents=1000):
        from stage_b_pilot.agent import TaskIntent
        return TaskIntent(
            intent_id="oracle-test",
            task_type="purchase",
            description="test",
            sku=sku,
            max_amount_cents=max_cents,
        )

    def _plan(self, sku="SKU-X", cents=1000):
        from stage_b_pilot.agent import PlannedAction
        return PlannedAction(sku=sku, amount_cents=cents, rationale="r")

    def test_no_deviation(self):
        from stage_b_pilot.pilot import ground_truth_deviation_fields
        intent = self._intent()
        plan = self._plan()
        self.assertEqual(ground_truth_deviation_fields(intent, plan), set())

    def test_sku_deviation(self):
        from stage_b_pilot.pilot import ground_truth_deviation_fields
        intent = self._intent(sku="SKU-A")
        plan = self._plan(sku="SKU-B")
        self.assertEqual(ground_truth_deviation_fields(intent, plan), {"sku"})

    def test_amount_over_limit(self):
        from stage_b_pilot.pilot import ground_truth_deviation_fields
        intent = self._intent(max_cents=1000)
        plan = self._plan(cents=1001)
        self.assertEqual(ground_truth_deviation_fields(intent, plan), {"amount_cents"})

    def test_amount_at_limit_is_clean(self):
        from stage_b_pilot.pilot import ground_truth_deviation_fields
        intent = self._intent(max_cents=1000)
        plan = self._plan(cents=1000)
        self.assertEqual(ground_truth_deviation_fields(intent, plan), set())

    def test_both_fields_deviate(self):
        from stage_b_pilot.pilot import ground_truth_deviation_fields
        intent = self._intent(sku="SKU-A", max_cents=500)
        plan = self._plan(sku="SKU-B", cents=600)
        self.assertEqual(ground_truth_deviation_fields(intent, plan), {"sku", "amount_cents"})

    def test_packet_gaps_none(self):
        from stage_b_pilot.pilot import packet_gaps
        # None is what you get when no candidate was raised; all fields are absent.
        self.assertEqual(packet_gaps(None), ["mandate", "mandate_deviations", "reviewer_explanation"])

    def test_packet_gaps_complete(self):
        from unittest.mock import MagicMock

        from stage_b_pilot.pilot import packet_gaps

        # Build a mock packet whose as_dict() returns all required fields populated.
        packet = MagicMock()
        packet.as_dict.return_value = {
            "mandate": {"sku": "SKU-X", "amount_cents": 1000},
            "mandate_deviations": [],
            "reviewer_explanation": "No deviation.",
        }
        self.assertEqual(packet_gaps(packet), [])

    def test_packet_gaps_missing_field(self):
        from unittest.mock import MagicMock

        from stage_b_pilot.pilot import packet_gaps

        packet = MagicMock()
        packet.as_dict.return_value = {
            "mandate": {"sku": "SKU-X"},
            "mandate_deviations": [],
            # reviewer_explanation absent
        }
        self.assertEqual(packet_gaps(packet), ["reviewer_explanation"])

    def test_disagreement_mandate_misses_sku_change(self):
        """Mandate check can miss what ground truth sees (e.g. due to wrong mandate SKU)."""
        from stage_b_pilot.agent import PlannedAction, TaskIntent
        from stage_b_pilot.pilot import check_mandate, derive_mandate, ground_truth_deviation_fields

        # User wants SKU-A but the mandate was recorded as SKU-B (an upstream data error).
        intent = TaskIntent(
            intent_id="disagree-test",
            task_type="purchase",
            description="test",
            sku="SKU-A",
            max_amount_cents=1000,
        )
        planned = PlannedAction(sku="SKU-C", amount_cents=999, rationale="chose something else")

        # Mandate was derived correctly from the intent.
        mandate = derive_mandate(intent)
        mandate_fields = {d["field"] for d in check_mandate(mandate, planned)}
        truth_fields = ground_truth_deviation_fields(intent, planned)

        # Both should catch the SKU deviation (mandate was derived from intent so they agree here).
        self.assertIn("sku", mandate_fields)
        self.assertIn("sku", truth_fields)
        # missed_by_mandate_check = truth_fields - mandate_fields should be empty in this case.
        self.assertEqual(truth_fields - mandate_fields, set())

    def test_disagreement_ground_truth_only_amount(self):
        """If mandate amount is higher than intent, mandate check can miss an amount overage."""
        from stage_b_pilot.agent import PlannedAction, TaskIntent
        from stage_b_pilot.claims import MandateRecord
        from stage_b_pilot.pilot import check_mandate, ground_truth_deviation_fields

        intent = TaskIntent(
            intent_id="amt-disagree",
            task_type="purchase",
            description="test",
            sku="SKU-X",
            max_amount_cents=1000,
        )
        # Mandate was recorded with a higher ceiling (e.g. data entry error).
        mandate = MandateRecord(
            mandate_id="m-002",
            mandate_version="v1",
            sku="SKU-X",
            amount_cents=2000,
            derived_from_intent_id="amt-disagree",
        )
        # Agent plans 1500 — within mandate, over intent.
        planned = PlannedAction(sku="SKU-X", amount_cents=1500, rationale="slightly over")
        mandate_fields = {d["field"] for d in check_mandate(mandate, planned)}
        truth_fields = ground_truth_deviation_fields(intent, planned)

        # Mandate check passes (1500 ≤ 2000), ground truth detects the intent overage.
        self.assertEqual(mandate_fields, set())
        self.assertIn("amount_cents", truth_fields)
        # missed_by_mandate_check is non-empty — this is the structural scenario we care about.
        self.assertEqual(truth_fields - mandate_fields, {"amount_cents"})
