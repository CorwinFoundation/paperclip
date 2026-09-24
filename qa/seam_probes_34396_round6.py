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
a = seed("task-y1")
b = EvidenceStore(ROOT, tenant="tenant-b")
seen = b.receipt_for("task-y1")
probe("Y1", "a tenant cannot read another tenant's approval receipt",
      seen is None,
      "tenant-b receipt_for('task-y1') -> None" if seen is None else
      f"tenant-b receipt_for('task-y1') returned it in full: description={seen.proposed_action.description!r} "
      f"merchant={seen.proposed_action.merchant!r} amount_cents={seen.proposed_action.amount_cents}")

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

w = sum(1 for r in ROWS if r[1] == "HOLDS")
print(f"BEAAA-34396 round-six probes @ 69ade651  probes={len(ROWS)} holds={w} fails={len(ROWS) - w}\n")
for pid, st, req, detail in ROWS:
    print(f"{pid} {st}: {req}\n      {detail}")
