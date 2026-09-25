"""Round-eight QA probes, BEAAA-34396: the second charge on Path 1.

The brief names "duplicate events" and "concurrent limit reservations" as
highest-risk seams. The abuse harness carries A6 and A7 and both are
behavioural, with a counting merchant -- and both drive `record_case` with an
idempotency key. Same shape of question as rounds six and seven: Path 1 is the
other half of Stage A, the path the consumer demo runs, and it has no
idempotency key anywhere on it.

`execute_approved_task` reads the receipt, calls the merchant, and only then
calls `record_outcome`. The pinned-window guard BEAAA-35482 added lives inside
`record_outcome`, which is after the money has moved. The library's own receipt
docstring states the principle this asks about: "enforce before the side effect,
never reconcile after it".

W1 and W4 are controls: the case path must still execute exactly once on a
retried key, and a single ordinary Path 1 execution must still work, so neither
finding can be read as "every second call fails anyway". Report-only.
"""
import pathlib
import sys
import tempfile
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from stage_a_demo.core import EvidenceStore, Mandate, SyntheticMerchantAdapter  # noqa: E402
from stage_a_demo.approval import approve_mandate  # noqa: E402
from stage_a_demo.receipt import ANSWER_OK, ApprovalReceipt, ProposedAction  # noqa: E402

ROOT = pathlib.Path(tempfile.mkdtemp(prefix="qa34396-r8-")) / "evidence.sqlite3"
ACTION = ProposedAction(description="Hotel Ester, 12-15 Oct, 2 guests", merchant="bookings.example",
                        amount_cents=48_000, non_recoverable=True, cancellation_terms="non-refundable")
ROWS = []
def probe(pid, req, ok, detail): ROWS.append((pid, "HOLDS" if ok else "FAILS", req, detail))

class CountingMerchant(SyntheticMerchantAdapter):
    """Counts what actually reached the merchant, which is what the user pays."""
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
    def purchase(self, **kw):
        with self.lock:
            self.calls.append(kw)
        return super().purchase(**kw)

def receipt(task):
    return ApprovalReceipt("rcpt-" + task, task, ACTION, ANSWER_OK,
                           "2026-09-20T09:00:00Z", "2026-09-20T09:01:00Z", "2026-09-20T09:01:05Z")

def call(fn, *a, **kw):
    try:
        return True, fn(*a, **kw)
    except Exception as exc:  # noqa: BLE001 -- the refusal is the evidence
        return False, f"{type(exc).__name__}: {exc}"

# W1. Control, re-derived rather than taken from A6: the case path holds the
# line. Four calls on one idempotency key reach the merchant once.
store = EvidenceStore(ROOT, tenant="tenant-a")
merchant = CountingMerchant()
mandate = Mandate("m-w1", "SKU-1", 100)
grant = approve_mandate(store, case_id="case-w1", mandate=mandate)
kwargs = dict(case_id="case-w1", grant_id=grant, asserted_sku="SKU-1", asserted_amount_cents=100,
              idempotency_key="key-w1", merchant=merchant)
results = [call(store.record_case, **kwargs)[0] for _ in range(4)]
probe("W1", "the case path executes once on a retried idempotency key",
      len(merchant.calls) == 1,
      f"4 x record_case on one key -> merchant calls={len(merchant.calls)}, returns={results}")

# W2. The same retry on Path 1, sequentially. A caller that times out, or a user
# who taps twice, calls execute_approved_task again.
a = EvidenceStore(ROOT, tenant="tenant-a")
a.record_pre_execution_receipt(receipt("task-w2"))
m2 = CountingMerchant()
first = call(a.execute_approved_task, "task-w2", merchant=m2, executed_at="2026-09-20T09:30:00Z")
second = call(a.execute_approved_task, "task-w2", merchant=m2, executed_at="2026-09-20T09:31:00Z")
probe("W2", "a repeated Path 1 execution reaches the merchant once",
      len(m2.calls) == 1,
      f"execute_approved_task x2 -> merchant calls={len(m2.calls)} "
      f"({sum(c['amount_cents'] for c in m2.calls)} cents total); "
      f"first={'ok' if first[0] else first[1]}; second={'ok' if second[0] else second[1]}")

# W3. The same question concurrently, which is the shape A7 asks on the case
# path. Two callers, two connections on one file, one task.
a1 = EvidenceStore(ROOT, tenant="tenant-a")
a1.record_pre_execution_receipt(receipt("task-w3"))
m3 = CountingMerchant()
outcomes = {}
def run(idx):
    store = EvidenceStore(ROOT, tenant="tenant-a")
    outcomes[idx] = call(store.execute_approved_task, "task-w3", merchant=m3,
                         executed_at="2026-09-20T09:30:00Z")
threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
for t in threads: t.start()
for t in threads: t.join()
probe("W3", "two concurrent callers on one Path 1 task reach the merchant once",
      len(m3.calls) == 1,
      f"2 concurrent execute_approved_task -> merchant calls={len(m3.calls)} "
      f"({sum(c['amount_cents'] for c in m3.calls)} cents total); "
      f"results={[('ok' if v[0] else v[1][:60]) for v in outcomes.values()]}")

# W4. Control on Path 1: one ordinary execution still works and still records,
# so W2 and W3 are about the second call and not about the first.
a = EvidenceStore(ROOT, tenant="tenant-a")
a.record_pre_execution_receipt(receipt("task-w4"))
m4 = CountingMerchant()
ok4, res4 = call(a.execute_approved_task, "task-w4", merchant=m4, executed_at="2026-09-20T09:30:00Z")
probe("W4", "a single Path 1 execution still reaches the merchant and settles",
      ok4 and len(m4.calls) == 1,
      f"merchant calls={len(m4.calls)}; claim={res4.get('claim') if ok4 else res4}")

# W5. What W2 costs. The user was charged twice; the evidence root is asked what
# it knows about that.
claim = a1.claim_for("task-w2") if False else EvidenceStore(ROOT, tenant="tenant-a").claim_for("task-w2")
charged = sum(c["amount_cents"] for c in m2.calls)
blob = str(claim).lower()
probe("W5", "the record reflects every charge the merchant took on the task",
      charged <= claim.get("amount_at_risk_cents", 0) or "duplicate" in blob,
      f"merchant took {charged} cents on task-w2; the settled record reads "
      f"claim={claim.get('claim')!r} covered={claim.get('covered')} "
      f"at_risk={claim.get('amount_at_risk_cents')}, and the second charge appears in it: "
      f"{'duplicate' in blob}")

w = sum(1 for r in ROWS if r[1] == "HOLDS")
print(f"BEAAA-34396 round-eight probes @ 0313498b  probes={len(ROWS)} holds={w} fails={len(ROWS)-w}\n")
for pid, st, req, detail in ROWS:
    print(f"{pid} {st}: {req}\n      {detail}")
