"""Path 1 receipts are scoped to the owner that recorded them.

BEAAA-35578, root finding BEAAA-35483. The owner check BEAAA-35357 A9 put on
`cases` stopped at the case surface: `approval_receipts` had no owner column
and `receipt_for`, `claim_for` and `record_outcome` keyed on `task_id` alone,
so a second store opened on the same file could read another owner's receipt
and write that owner's outcome.

Two stores, one file, throughout — that is the shape the defect lives in.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from stage_a_demo.core import EvidenceStore, SyntheticMerchantAdapter
from stage_a_demo.receipt import ANSWER_OK, ApprovalReceipt, ExecutedOutcome, ProposedAction

ESTER = ProposedAction(
    description="Hotel Ester, 3 nights, 12-15 Oct, 2 guests",
    merchant="bookings.example",
    amount_cents=48_000,
    deposit_cents=12_000,
    cancellation_terms="Deposit non-refundable after 24h; balance refundable to 7 days before arrival",
)
EXECUTED_AT = "2026-09-24T02:00:00Z"


class CountingMerchant(SyntheticMerchantAdapter):
    def __init__(self) -> None:
        self.calls = 0

    def purchase(self, *, case_id, sku, amount_cents):
        self.calls += 1
        return super().purchase(case_id=case_id, sku=sku, amount_cents=amount_cents)


def receipt(task_id):
    return ApprovalReceipt(
        receipt_id=f"receipt-{task_id}",
        task_id=task_id,
        proposed_action=ESTER,
        answer=ANSWER_OK,
        checked_at="2026-09-24T01:07:00Z",
        answered_at="2026-09-24T01:09:00Z",
        recorded_at="2026-09-24T01:09:05Z",
    )


OUTCOME = ExecutedOutcome(
    description=ESTER.description,
    merchant=ESTER.merchant,
    amount_cents=ESTER.amount_cents,
    evidence_ref="merchant-task-1",
)


class ReceiptTenantIsolation(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "evidence.sqlite3"
        self.acme = self.store("acme")
        self.globex = self.store("globex")
        self.acme.record_pre_execution_receipt(receipt("task-1"))

    def store(self, tenant=None):
        store = EvidenceStore(self.path, tenant=tenant)
        self.addCleanup(store.close)
        return store

    def test_a_co_tenant_cannot_read_the_receipt(self):
        self.assertIsNotNone(self.acme.receipt_for("task-1"))
        with self.assertRaises(PermissionError):
            self.globex.receipt_for("task-1")

    def test_a_co_tenant_cannot_read_the_claim(self):
        with self.assertRaises(PermissionError):
            self.globex.claim_for("task-1")

    def test_a_co_tenant_cannot_write_the_outcome(self):
        with self.assertRaises(PermissionError):
            self.globex.record_outcome("task-1", OUTCOME, executed_at=EXECUTED_AT)
        # The owner's row is untouched: no outcome, and still executable.
        self.assertIsNone(self.acme.claim_for("task-1")["executed_at"])

    def test_a_co_tenant_cannot_execute_and_never_reaches_the_merchant(self):
        merchant = CountingMerchant()
        with self.assertRaises(PermissionError):
            self.globex.execute_approved_task("task-1", merchant=merchant, executed_at=EXECUTED_AT)
        self.assertEqual(merchant.calls, 0)

    def test_a_co_tenant_cannot_overwrite_the_receipt(self):
        with self.assertRaises(PermissionError):
            self.globex.record_pre_execution_receipt(receipt("task-1"))
        self.assertEqual(self.acme.receipt_for("task-1").receipt_id, "receipt-task-1")

    def test_the_owner_still_runs_the_whole_path(self):
        merchant = CountingMerchant()
        claim = self.acme.execute_approved_task("task-1", merchant=merchant, executed_at=EXECUTED_AT)
        self.assertEqual(merchant.calls, 1)
        self.assertEqual(claim["executed_at"], EXECUTED_AT)
        self.assertEqual(self.acme.claim_for("task-1")["outcome"]["amount_cents"], 48_000)

    def test_two_owners_hold_separate_tasks_on_one_file(self):
        self.globex.record_pre_execution_receipt(receipt("task-2"))
        self.assertIsNotNone(self.globex.receipt_for("task-2"))
        with self.assertRaises(PermissionError):
            self.acme.receipt_for("task-2")
        self.assertIsNone(self.acme.receipt_for("task-absent"))

    def test_rows_written_before_the_column_existed_stay_readable(self):
        # The untenanted bootstrap store is the only reader that could have
        # written a NULL-owner row, and it keeps reading its own rows.
        plain = self.store()
        plain.record_pre_execution_receipt(receipt("task-legacy"))
        self.assertIsNotNone(plain.receipt_for("task-legacy"))
        with self.assertRaises(PermissionError):
            self.acme.receipt_for("task-legacy")
        self.assertEqual(
            plain.record_outcome("task-legacy", OUTCOME, executed_at=EXECUTED_AT)["executed_at"],
            EXECUTED_AT,
        )


if __name__ == "__main__":
    unittest.main()
