"""Round-six QA probes, BEAAA-34396: where the tenant boundary stops.

The brief names "tenant isolation" and "private receipt redaction" as separate
highest-risk seams. The abuse harness carries A9, and A9 is behavioural and has
a control -- but every leg of it is about `record_case` and `packet`. Nothing
asks whether the boundary reaches the other half of Stage A: the approval
receipt, the recorded outcome and the settled claim, which is where the
merchant, the amount and the money at risk actually live.

Two stores are opened on the same database file with different tenants, the way
the harness's A9 opens its two readers, and the receipt surface is asked the
same three questions A9 asks the case surface: read-back, isolation, control.
Report-only.
"""
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from stage_a_demo.core import EvidenceStore, Mandate, SyntheticMerchantAdapter  # noqa: E402
from stage_a_demo.approval import approve_mandate  # noqa: E402
from stage_a_demo.receipt import (ANSWER_OK, ApprovalReceipt, ExecutedOutcome,  # noqa: E402
                                  ProposedAction)

ROOT = pathlib.Path(tempfile.mkdtemp(prefix="qa34396-r6-")) / "evidence.sqlite3"
ACTION = ProposedAction(description="Hotel Ester, 12-15 Oct, 2 guests", merchant="bookings.example",
                        amount_cents=48_000, non_recoverable=True, cancellation_terms="non-refundable")
AS_APPROVED = ExecutedOutcome("Hotel Ester, 12-15 Oct, 2 guests", "bookings.example", 48_000,
                              evidence_ref="confirmation-from-hotel", performed=True,
                              observed_at="2026-09-20T10:00:00Z")

ROWS = []
def probe(pid, requirement, ok, detail): ROWS.append((pid, "HOLDS" if ok else "FAILS", requirement, detail))

def seed(task_id):
    """Tenant A records a receipt and an outcome that matches what was approved."""
    a = EvidenceStore(ROOT, tenant="tenant-a")
    a.record_pre_execution_receipt(ApprovalReceipt(
        "rcpt-" + task_id, task_id, ACTION, ANSWER_OK,
        "2026-09-20T09:00:00Z", "2026-09-20T09:01:00Z", "2026-09-20T09:01:05Z"))
    a.record_outcome(task_id, AS_APPROVED, executed_at="2026-09-20T09:30:00Z")
    return a

# Y1. Read. A receipt is the most private object Stage A holds.
#
# The requirement is that the foreign tenant does not get the receipt. It is not
# that the refusal takes one particular form. As first written this leg asserted
# `seen is None`, which made it a test of the shape rather than of the property:
# when the fix landed as a raised `PermissionError` (BEAAA-35578), the exception
# went uncaught and killed the whole file at this line, so Y2-Y5 never ran and
# round six reported nothing at all. A probe that cannot survive the fix it asked
# for is not evidence. Both refusals count here; what is recorded is which one
# the implementation chose, so the choice stays visible.
a = seed("task-y1")
b = EvidenceStore(ROOT, tenant="tenant-b")
try:
    seen = b.receipt_for("task-y1")
    ok = seen is None
    detail = ("tenant-b receipt_for('task-y1') -> None (refused as not-found)" if ok else
              f"tenant-b receipt_for('task-y1') returned it in full: "
              f"description={seen.proposed_action.description!r} "
              f"merchant={seen.proposed_action.merchant!r} "
              f"amount_cents={seen.proposed_action.amount_cents}")
except PermissionError as exc:
    ok = True
    detail = f"tenant-b receipt_for('task-y1') -> refused with {type(exc).__name__}"
probe("Y1", "a tenant cannot read another tenant's approval receipt", ok, detail)

# Y2. Settle. The claim carries the money at risk on top of the receipt.
a = seed("task-y2")
b = EvidenceStore(ROOT, tenant="tenant-b")
try:
    c = b.claim_for("task-y2")
    leaked = f"claim={c['claim']} covered={c['covered']} at_risk={c['amount_at_risk_cents']} " \
             f"merchant={c['approved_action'].get('merchant')!r}"
    ok = False
except (KeyError, PermissionError) as exc:
    leaked = f"refused with {type(exc).__name__}"
    ok = True
probe("Y2", "a tenant cannot settle a claim on another tenant's task", ok,
      f"tenant-b claim_for('task-y2') -> {leaked}")

# Y3. Write, and the direction that matters. Tenant A's task executed exactly as
# approved, so it is not a loss. Tenant B re-records an outcome on it.
a = seed("task-y3")
before = a.claim_for("task-y3")
b = EvidenceStore(ROOT, tenant="tenant-b")
wrote = None
try:
    b.record_outcome("task-y3", ExecutedOutcome("Something else entirely", "other.example", 1,
                                                evidence_ref="asserted-by-another-tenant",
                                                observed_at="2026-09-21T10:00:00Z"),
                     executed_at="2026-09-21T09:00:00Z")
    wrote = "accepted"
except (KeyError, PermissionError) as exc:
    wrote = f"refused with {type(exc).__name__}"
after = a.claim_for("task-y3")
probe("Y3", "a tenant cannot record an outcome against another tenant's task",
      wrote.startswith("refused") and after["covered"] == before["covered"],
      f"tenant-a settled {before['claim']}/covered={before['covered']}; tenant-b record_outcome "
      f"{wrote}; tenant-a now reads {after['claim']}/covered={after['covered']} "
      f"at_risk={after['amount_at_risk_cents']}")

# Y4. Control on the receipt surface. The owning tenant must still read its own
# receipt and claim, so "refuse every read" cannot pass Y1 or Y2.
a = seed("task-y4")
own_receipt = a.receipt_for("task-y4")
own_claim = a.claim_for("task-y4")
probe("Y4", "the owning tenant still reads its own receipt and claim",
      own_receipt is not None and own_claim.get("claim") is not None,
      f"tenant-a receipt_for -> {'present' if own_receipt else 'None'}; "
      f"claim_for -> {own_claim.get('claim')}/covered={own_claim.get('covered')}")

# Y5. Control on the case surface, re-derived rather than taken from A9: the
# boundary does exist, which is what makes Y1-Y3 a gap in it rather than its
# absence.
mandate = Mandate("m-y5", "SKU-1", 100)
a = EvidenceStore(ROOT, tenant="tenant-a")
grant = approve_mandate(a, case_id="case-y5", mandate=mandate)
a.record_case(case_id="case-y5", grant_id=grant, asserted_sku="SKU-1", asserted_amount_cents=100,
              idempotency_key="key-y5", merchant=SyntheticMerchantAdapter())
own = a.packet("case-y5")
b = EvidenceStore(ROOT, tenant="tenant-b")
try:
    foreign = b.packet("case-y5")
    foreign_state = "returned it"
except (KeyError, PermissionError) as exc:
    foreign = None
    foreign_state = f"refused with {type(exc).__name__}"
probe("Y5", "the case surface is isolated, so the boundary exists and Y1-Y3 are a gap in it",
      own is not None and foreign is None,
      f"tenant-a packet('case-y5') -> {'present' if own else 'None'}; tenant-b -> {foreign_state}")

# Y6. The plant direction, and the reason this row is about money and not only
# about privacy. Y1-Y3 all have the foreign tenant reading or writing something
# of the owner's. This one runs the other way: the foreign tenant writes an
# approved receipt of its own on a task id the owner has not used, and then the
# *owner's* process executes it. `execute_approved_task` takes description and
# amount straight off the stored receipt and has no mandate check of its own, so
# on an unscoped read this is not a disclosure -- it is the owner's adapter
# buying the foreign tenant's basket. Measured at master 69ade651 it executed
# `purchase('task-y6', 'Gift cards x200', 5000000)`.
class _RecordingMerchant(SyntheticMerchantAdapter):
    """Records purchases, so "money did not move" is observed and not inferred."""

    def __init__(self):
        self.calls = []

    def purchase(self, *, case_id, sku, amount_cents):
        self.calls.append((case_id, sku, amount_cents))
        return super().purchase(case_id=case_id, sku=sku, amount_cents=amount_cents)


LOOT = ProposedAction(description="Gift cards x200", merchant="attacker.example",
                      amount_cents=5_000_000, non_recoverable=True,
                      cancellation_terms="non-refundable")
b = EvidenceStore(ROOT, tenant="tenant-b")
b.record_pre_execution_receipt(ApprovalReceipt(
    "rcpt-task-y6", "task-y6", LOOT, ANSWER_OK,
    "2026-09-20T09:00:00Z", "2026-09-20T09:01:00Z", "2026-09-20T09:01:05Z"))
a = EvidenceStore(ROOT, tenant="tenant-a")
spy = _RecordingMerchant()
try:
    a.execute_approved_task("task-y6", merchant=spy, executed_at="2026-09-21T09:00:00Z")
    ran = "EXECUTED"
except Exception as exc:
    ran = f"refused with {type(exc).__name__}"
probe("Y6", "a receipt planted by another tenant is not executable by this one",
      not ran.startswith("EXECUTED") and not spy.calls,
      f"tenant-a execute_approved_task on tenant-b's planted receipt -> {ran}; "
      f"merchant.purchase calls={spy.calls}")

w = sum(1 for r in ROWS if r[1] == "HOLDS")
# The SHA is read from the checkout rather than written into the file. The
# original hardcoded `69ade651` kept being printed beside results from other
# commits, which makes a pasted report unciteable.
_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                      cwd=pathlib.Path(__file__).resolve().parents[1])
_at = _sha.stdout.strip() or "unknown"
print(f"BEAAA-34396 round-six probes @ {_at}  probes={len(ROWS)} holds={w} fails={len(ROWS) - w}\n")
for pid, st, req, detail in ROWS:
    print(f"{pid} {st}: {req}\n      {detail}")
