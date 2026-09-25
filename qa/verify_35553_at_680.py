"""QA verification of the BEAAA-35553 fix at PR #680 head 62ae8042 (stacked on #675).

Asks the round-seven requirements through whatever public lever exists rather
than through the one name round seven happened to call, adds the two questions
the fix itself makes necessary (is the NEW lever scoped, does the happy path
survive it), and re-runs the round-six tenant legs because this branch sits on
top of the 35483 fix. Report-only.
"""
import pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from stage_a_demo.core import EvidenceStore, Mandate, SyntheticMerchantAdapter  # noqa: E402
from stage_a_demo.approval import approve_mandate  # noqa: E402
from stage_a_demo.receipt import ANSWER_OK, ApprovalReceipt, ExecutedOutcome, ProposedAction  # noqa: E402

ROOT = pathlib.Path(tempfile.mkdtemp(prefix="qa35553-")) / "evidence.sqlite3"
ACTION = ProposedAction(description="Hotel Ester, 12-15 Oct, 2 guests", merchant="bookings.example",
                        amount_cents=48_000, non_recoverable=True, cancellation_terms="non-refundable")
ROWS = []
def probe(pid, req, ok, detail): ROWS.append((pid, "HOLDS" if ok else "FAILS", req, detail))

class CountingMerchant(SyntheticMerchantAdapter):
    def __init__(self): self.calls = []
    def purchase(self, **kw):
        self.calls.append(kw)
        return super().purchase(**kw)

def call(fn, *a, **kw):
    try: return True, fn(*a, **kw)
    except Exception as exc: return False, f"{type(exc).__name__}: {exc}"

def receipt(task): return ApprovalReceipt("rcpt-"+task, task, ACTION, ANSWER_OK,
    "2026-09-20T09:00:00Z", "2026-09-20T09:01:00Z", "2026-09-20T09:01:05Z")

def withdraw_every_way(store, task):
    """Every public lever the library offers for taking an approval back."""
    tried = {}
    for name in ("withdraw_receipt", "revoke_approval"):
        fn = getattr(store, name, None)
        if fn is None:
            tried[name] = "absent"; continue
        try:
            tried[name] = fn(task, withdrawn_at="2026-09-20T09:15:00Z") if name == "withdraw_receipt" else fn(task)
        except Exception as exc: tried[name] = f"{type(exc).__name__}"
    return tried

# V1. The round-seven Z2 requirement, asked through whatever lever exists.
a = EvidenceStore(ROOT, tenant="tenant-a"); a.record_pre_execution_receipt(receipt("task-v1"))
levers = withdraw_every_way(a, "task-v1")
m = CountingMerchant()
ran, res = call(a.execute_approved_task, "task-v1", merchant=m, executed_at="2026-09-20T09:30:00Z")
probe("V1", "a human who withdraws approval before execution can stop a Path 1 task",
      not ran and m.calls == [],
      f"levers -> {levers}; execute_approved_task -> {'EXECUTED' if ran else res}; merchant calls={len(m.calls)}")

# V2. Control the fix must not break: an approval NOT withdrawn still executes.
a = EvidenceStore(ROOT, tenant="tenant-a"); a.record_pre_execution_receipt(receipt("task-v2"))
m = CountingMerchant()
ran, res = call(a.execute_approved_task, "task-v2", merchant=m, executed_at="2026-09-20T09:30:00Z")
probe("V2", "a Path 1 approval that was never withdrawn still executes and settles",
      ran and len(m.calls) == 1,
      f"execute_approved_task -> {'claim=' + str(res.get('claim')) if ran else res}; merchant calls={len(m.calls)}")

# V3. The new lever as a write across owners. It is on approval_receipts, the
# table BEAAA-35483 is about, so it is exactly where a new lever could re-open Z3.
a = EvidenceStore(ROOT, tenant="tenant-a"); a.record_pre_execution_receipt(receipt("task-v3"))
b = EvidenceStore(ROOT, tenant="tenant-b")
foreign = withdraw_every_way(b, "task-v3")
m = CountingMerchant()
ran, res = call(a.execute_approved_task, "task-v3", merchant=m, executed_at="2026-09-20T09:30:00Z")
probe("V3", "a tenant cannot withdraw another tenant's Path 1 approval",
      foreign.get("withdraw_receipt") is False and ran and len(m.calls) == 1,
      f"tenant-b levers on tenant-a's task -> {foreign}; tenant-a execute afterwards -> "
      f"{'executed, claim=' + str(res.get('claim')) if ran else 'REFUSED ' + str(res)}")

# V4. Round-seven Z3 on the mandate path, at the fix.
a = EvidenceStore(ROOT, tenant="tenant-a")
mand = Mandate("m-v4", "SKU-1", 100)
grant = approve_mandate(a, case_id="case-v4", mandate=mand)
b = EvidenceStore(ROOT, tenant="tenant-b")
foreign_revoke = b.revoke_approval(grant)
ran, res = call(a.record_case, case_id="case-v4", grant_id=grant, asserted_sku="SKU-1",
                asserted_amount_cents=100, idempotency_key="key-v4", merchant=SyntheticMerchantAdapter())
probe("V4", "a tenant cannot withdraw another tenant's mandate approval",
      foreign_revoke is False and ran,
      f"tenant-b revoke_approval -> {foreign_revoke}; tenant-a record_case -> "
      f"{'executed ' + str(res.get('status')) if ran else 'REFUSED ' + str(res)}")

# V5. Control on the mandate lever: the owner must still be able to use it,
# so V4 cannot pass by refusing everyone.
a = EvidenceStore(ROOT, tenant="tenant-a")
mand5 = Mandate("m-v5", "SKU-1", 100)
grant5 = approve_mandate(a, case_id="case-v5", mandate=mand5)
own_revoke = a.revoke_approval(grant5)
ran5, res5 = call(a.record_case, case_id="case-v5", grant_id=grant5, asserted_sku="SKU-1",
                  asserted_amount_cents=100, idempotency_key="key-v5", merchant=SyntheticMerchantAdapter())
probe("V5", "the owning tenant can still withdraw its own mandate approval",
      own_revoke is True and not ran5,
      f"tenant-a revoke_approval on its own grant -> {own_revoke}; record_case afterwards -> "
      f"{'EXECUTED' if ran5 else res5}")

# V6. Z4 parity for the new lever: withdrawal never unwinds a completed purchase.
a = EvidenceStore(ROOT, tenant="tenant-a"); a.record_pre_execution_receipt(receipt("task-v6"))
m = CountingMerchant()
a.execute_approved_task("task-v6", merchant=m, executed_at="2026-09-20T09:30:00Z")
late = a.withdraw_receipt("task-v6", withdrawn_at="2026-09-20T10:00:00Z")
after = a.claim_for("task-v6")
probe("V6", "withdrawing after execution is refused and does not unwind the purchase",
      late is False and len(m.calls) == 1 and after.get("claim") is not None,
      f"withdraw_receipt after execution -> {late}; merchant calls={len(m.calls)}; claim reads {after.get('claim')!r}")

# V7. Z5, the money reading. The user withdrew and the charge landed anyway,
# reported by independent evidence rather than through this process.
a = EvidenceStore(ROOT, tenant="tenant-a"); a.record_pre_execution_receipt(receipt("task-v7"))
a.withdraw_receipt("task-v7", withdrawn_at="2026-09-20T09:15:00Z")
claim = a.record_outcome("task-v7", ExecutedOutcome(
    "Hotel Ester, 12-15 Oct, 2 guests", "bookings.example", 48_000,
    evidence_ref="confirmation-from-hotel", performed=True, observed_at="2026-09-20T10:00:00Z"),
    executed_at="2026-09-20T09:30:00Z")
blob = str(claim).lower()
probe("V7", "a charge that lands after a withdrawal settles with the withdrawal on the record",
      ("withdraw" in blob or "revok" in blob) and claim.get("claim") != "not_covered_user_approved_this",
      f"claim={claim.get('claim')!r} covered={claim.get('covered')} at_risk={claim.get('amount_at_risk_cents')} "
      f"withdrawn_at on the claim: {claim.get('withdrawn_at')!r}")

# V8. Round-six regression: this branch stacks on the 35483 fix, so the receipt
# surface should now be owner-scoped for reads too.
a = EvidenceStore(ROOT, tenant="tenant-a"); a.record_pre_execution_receipt(receipt("task-v8"))
a.record_outcome("task-v8", ExecutedOutcome("Hotel Ester, 12-15 Oct, 2 guests", "bookings.example",
                 48_000, evidence_ref="confirmation-from-hotel", performed=True,
                 observed_at="2026-09-20T10:00:00Z"), executed_at="2026-09-20T09:30:00Z")
b = EvidenceStore(ROOT, tenant="tenant-b")
read_ok, seen = call(b.receipt_for, "task-v8")
claim_ok, seen_claim = call(b.claim_for, "task-v8")
own = a.receipt_for("task-v8")
probe("V8", "round six: a tenant still cannot read another tenant's receipt or claim, and the owner still can",
      (not read_ok or seen is None) and (not claim_ok) and own is not None,
      f"tenant-b receipt_for -> {'refused: ' + str(seen) if not read_ok else ('None' if seen is None else 'RETURNED IT')}; "
      f"tenant-b claim_for -> {'refused: ' + str(seen_claim)[:60] if not claim_ok else 'RETURNED IT'}; "
      f"tenant-a receipt_for -> {'present' if own else 'None'}")

w = sum(1 for r in ROWS if r[1] == "HOLDS")
print(f"BEAAA-35553 fix verification @ 62ae8042 (PR #680 on #675)  probes={len(ROWS)} holds={w} fails={len(ROWS)-w}\n")
for pid, st, req, detail in ROWS:
    print(f"{pid} {st}: {req}\n      {detail}")
