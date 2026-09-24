"""Two enforcement postures, separately expressed and separately tested.

CEO ruling 2026-09-24, on top of Founder Addendum 2. The Founder cites harness
finding A4 as the principle behind the approval receipt — enforce before the
side effect, never reconcile after it. That is an endorsement of A4, not a
relaxation of it, so implementing the receipt must not trade A4's refusal down
into a warning.

The line is capability, not policy:

* BackBond **holds the capability** — it has the grant and it calls the merchant
  adapter. An assertion outside the mandate is refused before the grant is
  consumed and before the adapter is reached.
* BackBond **only observes** — Path 1, no capability, no adapter, the user's own
  agent executes. BackBond cannot refuse anything it does not perform, so
  "over your limit" and "this is non-recoverable" are warnings written on the
  receipt and the user can go straight past them.

These are not in conflict, and neither is parameterised into the other: they are
different call paths with different types. This file pins both, and pins the
fact that they stay apart.
"""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from stage_a_demo import core
from stage_a_demo.approval import approve_mandate, build_issuer
from stage_a_demo.core import (
    EvidenceStore,
    Mandate,
    MandateExceeded,
    SyntheticMerchantAdapter,
)
from stage_a_demo.receipt import (
    ANSWER_OK,
    CLAIM_NOT_COVERED_APPROVED,
    WARNING_NON_RECOVERABLE,
    WARNING_OVER_LIMIT,
    ApprovalReceipt,
    ProposedAction,
    assess_warnings,
)


class CountingMerchant(SyntheticMerchantAdapter):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def purchase(self, **kwargs):
        self.calls.append(kwargs)
        return super().purchase(**kwargs)


class CapabilityPathRefusesTests(unittest.TestCase):
    """Where BackBond can stop the side effect, it does. This is A4."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EvidenceStore(Path(self.tmp.name) / "evidence.sqlite3")
        self.issuer = build_issuer()
        self.merchant = CountingMerchant()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def approve(self, case_id, mandate):
        return approve_mandate(self.store, case_id=case_id, mandate=mandate, issuer=self.issuer)

    def test_an_over_mandate_amount_is_refused_before_the_adapter(self):
        mandate = Mandate("m-1", "SKU-1", 100)
        grant = self.approve("c1", mandate)
        with self.assertRaises(MandateExceeded):
            self.store.record_case(
                case_id="c1", grant_id=grant, asserted_sku="SKU-1",
                asserted_amount_cents=999_900, idempotency_key="i1", merchant=self.merchant)
        self.assertEqual([], self.merchant.calls)
        self.assertIsNone(self.store.packet_or_none("c1"))

    def test_a_different_sku_is_refused_before_the_adapter(self):
        mandate = Mandate("m-2", "SKU-1", 100)
        grant = self.approve("c2", mandate)
        with self.assertRaises(MandateExceeded):
            self.store.record_case(
                case_id="c2", grant_id=grant, asserted_sku="SKU-PREMIUM",
                asserted_amount_cents=100, idempotency_key="i2", merchant=self.merchant)
        self.assertEqual([], self.merchant.calls)
        self.assertIsNone(self.store.packet_or_none("c2"))

    def test_a_refusal_leaves_the_human_approval_unspent(self):
        """The user approved once. A refused attempt must not burn that."""
        mandate = Mandate("m-3", "SKU-1", 100)
        grant = self.approve("c3", mandate)
        with self.assertRaises(MandateExceeded):
            self.store.record_case(
                case_id="c3", grant_id=grant, asserted_sku="SKU-1",
                asserted_amount_cents=500, idempotency_key="i3", merchant=self.merchant)
        packet = self.store.record_case(
            case_id="c3", grant_id=grant, asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="i3b", merchant=self.merchant)
        self.assertEqual("MATCH", packet["status"])
        self.assertEqual(1, len(self.merchant.calls))

    def test_spending_less_than_approved_is_recorded_not_refused(self):
        """Within the mandate is not outside it. Cheaper is reviewable, not refused."""
        mandate = Mandate("m-4", "SKU-1", 1_000)
        grant = self.approve("c4", mandate)
        packet = self.store.record_case(
            case_id="c4", grant_id=grant, asserted_sku="SKU-1",
            asserted_amount_cents=900, idempotency_key="i4", merchant=self.merchant)
        self.assertEqual("MISMATCH", packet["status"])
        self.assertEqual(1, len(self.merchant.calls))


class ObservationPathWarnsTests(unittest.TestCase):
    """Where BackBond cannot stop the side effect, it does not pretend to."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EvidenceStore(Path(self.tmp.name) / "evidence.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_over_limit_and_non_recoverable_are_warnings_and_the_task_still_runs(self):
        action = ProposedAction(
            description="Hotel Ester, 3 nights", merchant="bookings.example",
            amount_cents=48_000, deposit_cents=12_000,
            cancellation_terms="Deposit non-refundable after 24h")
        warnings = assess_warnings(action, limit_cents=40_000)
        self.assertEqual([WARNING_NON_RECOVERABLE, WARNING_OVER_LIMIT], warnings)
        self.store.record_pre_execution_receipt(ApprovalReceipt(
            receipt_id="r-1", task_id="t-1", proposed_action=action, answer=ANSWER_OK,
            checked_at="2026-09-24T01:07:00Z", answered_at="2026-09-24T01:09:00Z",
            recorded_at="2026-09-24T01:09:05Z", limit_cents=40_000, warnings=warnings))
        review = self.store.execute_approved_task(
            "t-1", merchant=SyntheticMerchantAdapter(), executed_at="2026-09-24T01:10:00Z")
        # The user tapped straight past both warnings and the task ran.
        self.assertEqual(CLAIM_NOT_COVERED_APPROVED, review["claim"])
        self.assertEqual([WARNING_NON_RECOVERABLE, WARNING_OVER_LIMIT], review["warnings"])


class ThePosturesStayApartTests(unittest.TestCase):
    def test_the_refusal_is_not_parameterised_into_a_warning(self):
        """No flag turns the capability-path refusal into an advisory.

        If someone later adds one, this fails, which is the point.
        """
        source = inspect.getsource(core.EvidenceStore.record_case)
        self.assertIn("raise MandateExceeded", source)
        params = inspect.signature(core.EvidenceStore.record_case).parameters
        for softener in ("warn_only", "enforce", "advisory", "strict", "warnings_only"):
            self.assertNotIn(softener, params)

    def test_the_two_paths_do_not_share_an_entry_point(self):
        capability = set(inspect.signature(core.EvidenceStore.record_case).parameters)
        observation = set(inspect.signature(core.EvidenceStore.execute_approved_task).parameters)
        # The capability path is driven by a grant; the observation path by a
        # receipt. Neither takes the other's authorisation object.
        self.assertIn("grant_id", capability)
        self.assertNotIn("grant_id", observation)
        self.assertNotIn("task_id", capability)
        self.assertIn("task_id", observation)

    def test_the_observation_path_never_raises_the_capability_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp) / "e.sqlite3")
            try:
                action = ProposedAction("Anything", "m.example", 10_000_000, 0, "")
                store.record_pre_execution_receipt(ApprovalReceipt(
                    receipt_id="r", task_id="t", proposed_action=action, answer=ANSWER_OK,
                    checked_at="2026-09-24T01:07:00Z", answered_at="2026-09-24T01:09:00Z",
                    recorded_at="2026-09-24T01:09:05Z", limit_cents=1,
                    warnings=assess_warnings(action, 1)))
                review = store.execute_approved_task(
                    "t", merchant=SyntheticMerchantAdapter(), executed_at="2026-09-24T01:10:00Z")
                self.assertEqual(CLAIM_NOT_COVERED_APPROVED, review["claim"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
