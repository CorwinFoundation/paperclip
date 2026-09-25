"""Path 1 execution idempotency: one receipt buys one execution.

BEAAA-35579, from QA round-eight probes on BEAAA-34396. Before this, every
guard on `execute_approved_task` lived inside `record_outcome`, which runs
after `merchant.purchase`. A repeated call (W2) and two concurrent callers
(W3) both reached the merchant twice, and the settled claim recorded one
purchase (W5).

Counting merchant throughout: the assertion is on how many times the adapter
was reached, not on what the second call returned.
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from stage_a_demo.core import EvidenceStore, SyntheticMerchantAdapter
from stage_a_demo.receipt import (
    ANSWER_OK,
    ApprovalReceipt,
    ExecutedOutcome,
    ExecutionAlreadyClaimed,
    ProposedAction,
)

ESTER = ProposedAction(
    description="Hotel Ester, 3 nights, 12-15 Oct, 2 guests",
    merchant="bookings.example",
    amount_cents=48_000,
    deposit_cents=12_000,
    cancellation_terms="Deposit non-refundable after 24h; balance refundable to 7 days before arrival",
)
EXECUTED_AT = "2026-09-24T02:00:00Z"


class CountingMerchant(SyntheticMerchantAdapter):
    """Synthetic adapter that records every time the money seam was reached."""

    def __init__(self, *, delay: float = 0.0) -> None:
        self.calls = 0
        self.amount_cents = 0
        self._lock = threading.Lock()
        self._delay = delay

    def purchase(self, *, case_id, sku, amount_cents):
        # Widen the window between reading the receipt and settling the
        # outcome, which is where the second caller used to get in.
        time.sleep(self._delay)
        with self._lock:
            self.calls += 1
            self.amount_cents += amount_cents
        return super().purchase(case_id=case_id, sku=sku, amount_cents=amount_cents)


def receipt(task_id, answer=ANSWER_OK):
    return ApprovalReceipt(
        receipt_id=f"receipt-{task_id}",
        task_id=task_id,
        proposed_action=ESTER,
        answer=answer,
        checked_at="2026-09-24T01:07:00Z",
        answered_at="2026-09-24T01:09:00Z",
        recorded_at="2026-09-24T01:09:05Z",
    )


class ExecutionIdempotency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "evidence.sqlite3"
        self.addCleanup(self.tmp.cleanup)

    def store(self):
        store = EvidenceStore(self.path)
        self.addCleanup(store.close)
        return store

    def test_w4_control_one_execution_still_works(self):
        store = self.store()
        merchant = CountingMerchant()
        store.record_pre_execution_receipt(receipt("task-w4"))
        claim = store.execute_approved_task("task-w4", merchant=merchant, executed_at=EXECUTED_AT)
        self.assertEqual(merchant.calls, 1)
        self.assertEqual(merchant.amount_cents, 48_000)
        self.assertEqual(claim["executed_at"], EXECUTED_AT)

    def test_w2_repeated_call_does_not_charge_twice(self):
        store = self.store()
        merchant = CountingMerchant()
        store.record_pre_execution_receipt(receipt("task-w2"))
        first = store.execute_approved_task("task-w2", merchant=merchant, executed_at=EXECUTED_AT)
        second = store.execute_approved_task("task-w2", merchant=merchant, executed_at=EXECUTED_AT)
        self.assertEqual(merchant.calls, 1)
        self.assertEqual(merchant.amount_cents, 48_000)
        # The retry is answered with the answer the first call gave.
        self.assertEqual(second["claim"], first["claim"])
        self.assertEqual(second["executed_at"], first["executed_at"])

    def test_w5_the_record_matches_what_the_merchant_saw(self):
        store = self.store()
        merchant = CountingMerchant()
        store.record_pre_execution_receipt(receipt("task-w5"))
        store.execute_approved_task("task-w5", merchant=merchant, executed_at=EXECUTED_AT)
        store.execute_approved_task("task-w5", merchant=merchant, executed_at=EXECUTED_AT)
        claim = store.claim_for("task-w5")
        self.assertEqual(claim["outcome"]["amount_cents"], merchant.amount_cents)

    def test_w3_two_concurrent_callers_reach_the_merchant_once(self):
        # Two connections on one file, both inside the merchant at the same
        # moment if the claim does not settle first.
        opener = self.store()
        opener.record_pre_execution_receipt(receipt("task-w3"))
        merchant = CountingMerchant(delay=0.05)
        results: list[object] = []
        lock = threading.Lock()
        start = threading.Barrier(2, timeout=10)

        def run():
            store = EvidenceStore(self.path)
            start.wait()
            try:
                outcome = store.execute_approved_task(
                    "task-w3", merchant=merchant, executed_at=EXECUTED_AT
                )
            except BaseException as exc:
                outcome = exc
            finally:
                store.close()
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(merchant.calls, 1)
        self.assertEqual(merchant.amount_cents, 48_000)
        self.assertEqual(len(results), 2)
        refused = [r for r in results if isinstance(r, BaseException)]
        self.assertEqual(len(refused), 1, results)
        self.assertIsInstance(refused[0], ExecutionAlreadyClaimed)

    def test_unsettled_claim_is_refused_not_replayed(self):
        # A caller that claimed the slot and never came back leaves the task
        # unsettled. A later call must not charge again on the chance the
        # first purchase did not land.
        store = self.store()
        store.record_pre_execution_receipt(receipt("task-stuck"))

        class Exploding(CountingMerchant):
            def purchase(self, **kw):
                super().purchase(**kw)
                raise RuntimeError("merchant timed out after taking the money")

        merchant = Exploding()
        with self.assertRaises(RuntimeError):
            store.execute_approved_task("task-stuck", merchant=merchant, executed_at=EXECUTED_AT)
        with self.assertRaises(ExecutionAlreadyClaimed):
            store.execute_approved_task("task-stuck", merchant=merchant, executed_at=EXECUTED_AT)
        self.assertEqual(merchant.calls, 1)
        # Recovery is the same entry point a task executed outside this process
        # uses: record what the merchant actually says happened.
        claim = store.record_outcome(
            "task-stuck",
            ExecutedOutcome(
                description=ESTER.description,
                merchant=ESTER.merchant,
                amount_cents=ESTER.amount_cents,
                evidence_ref="merchant-task-stuck",
            ),
            executed_at=EXECUTED_AT,
        )
        self.assertEqual(claim["outcome"]["amount_cents"], 48_000)


if __name__ == "__main__":
    unittest.main()
