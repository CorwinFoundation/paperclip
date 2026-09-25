"""Round-seven QA probes, BEAAA-34396: where revocation stops.

The brief names "mandate replay/revocation" as a highest-risk seam. The abuse
harness carries A2 and A3, and both are behavioural -- but both are entirely on
the mandate path: `approve_mandate`, a grant id, `record_case`. Nothing asks
whether a human's change of mind reaches Path 1, which is the path the consumer
demo actually runs and the one the README calls the evidence root: a receipt is
written, then `execute_approved_task` spends it.

Round six found the tenant boundary stopping at the case surface. This asks the
same shape of question about the withdrawal lever: the mandate path has one, and
the receipt path is the other half of Stage A, where the merchant, the amount
and the money at risk live.

Z3 is a second question on the same lever. `revoke_approval` is a write, and its
UPDATE names the grant id alone -- the `approvals` table has no owner column,
where `cases` gained one for BEAAA-35357 A9. Whether that is reachable across
two readers on one file is a measurement, not a reading, so it is measured here.

Controls: Z1 re-derives the mandate-path lever rather than taking A3's word for
it, and Z4 pins that the lever is not simply "return True to everything" -- a
spent grant must refuse, and revocation must never unwind a purchase that
already happened. Report-only.
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from stage_a_demo.core import EvidenceStore, Mandate, SyntheticMerchantAdapter  # noqa: E402
from stage_a_demo.approval import approve_mandate  # noqa: E402
from stage_a_demo.receipt import (ANSWER_OK, ApprovalReceipt, ExecutedOutcome,  # noqa: E402
                                  ProposedAction)

ROOT = pathlib.Path(tempfile.mkdtemp(prefix="qa34396-r7-")) / "evidence.sqlite3"
ACTION = ProposedAction(description="Hotel Ester, 12-15 Oct, 2 guests", merchant="bookings.example",
                        amount_cents=48_000, non_recoverable=True, cancellation_terms="non-refundable")

ROWS = []
def probe(pid, requirement, ok, detail): ROWS.append((pid, "HOLDS" if ok else "FAILS", requirement, detail))

def outcome_of(fn, *a, **kw):
    """Run a call and describe what came back, refusal included."""
    try:
        return True, fn(*a, **kw)
    except Exception as exc:  # noqa: BLE001 -- the refusal type is the evidence
        return False, f"{type(exc).__name__}: {exc}"

def seed_mandate(store, case_id, limit=100):
    m = Mandate(f"m-{case_id}", "SKU-1", limit)
    return m, approve_mandate(store, case_id=case_id, mandate=m)

def seed_receipt(store, task_id):
    store.record_pre_execution_receipt(ApprovalReceipt(
        "rcpt-" + task_id, task_id, ACTION, ANSWER_OK,
        "2026-09-20T09:00:00Z", "2026-09-20T09:01:00Z", "2026-09-20T09:01:05Z"))
    return task_id

# Z1. Control, re-derived rather than taken from A3: the lever exists on the
# mandate path. Without this, Z2 could be read as "there is no revocation at
# all", which is a different and much weaker claim.
a = EvidenceStore(ROOT, tenant="tenant-a")
m, grant = seed_mandate(a, "case-z1")
withdrawn = a.revoke_approval(grant)
ran, after = outcome_of(a.record_case, case_id="case-z1", grant_id=grant, asserted_sku="SKU-1",
                        asserted_amount_cents=100, idempotency_key="key-z1",
                        merchant=SyntheticMerchantAdapter())
probe("Z1", "a human can withdraw an unspent mandate approval and stop the execution it authorised",
      withdrawn is True and not ran,
      f"revoke_approval -> {withdrawn}; record_case afterwards -> "
      f"{'EXECUTED ' + str(after.get('status')) if ran else after}")

# Z2. The same question on Path 1. The receipt is recorded and the user then
# changes their mind before anything executes. Every public lever the library
# offers for withdrawal is tried against the task and against its receipt id.
a = EvidenceStore(ROOT, tenant="tenant-a")
seed_receipt(a, "task-z2")
levers = {name: a.revoke_approval(name) for name in ("task-z2", "rcpt-task-z2")}
ran, executed = outcome_of(a.execute_approved_task, "task-z2",
                           merchant=SyntheticMerchantAdapter(), executed_at="2026-09-20T09:30:00Z")
probe("Z2", "a human who withdraws approval before execution can stop a Path 1 task",
      not ran,
      f"revoke_approval on the task path -> {levers}; execute_approved_task afterwards -> "
      f"{'EXECUTED, merchant reached, claim=' + str(executed.get('claim')) if ran else executed}")

# Z3. The lever as a write, across owners. Tenant A holds an unspent approval;
# tenant B, which cannot read tenant A's case at all (A9), calls revoke on it.
a = EvidenceStore(ROOT, tenant="tenant-a")
m3, grant3 = seed_mandate(a, "case-z3")
b = EvidenceStore(ROOT, tenant="tenant-b")
foreign = b.revoke_approval(grant3)
ran, after3 = outcome_of(a.record_case, case_id="case-z3", grant_id=grant3, asserted_sku="SKU-1",
                         asserted_amount_cents=100, idempotency_key="key-z3",
                         merchant=SyntheticMerchantAdapter())
probe("Z3", "a tenant cannot withdraw another tenant's approval",
      foreign is False and ran,
      f"tenant-b revoke_approval on tenant-a's grant -> {foreign}; tenant-a record_case afterwards -> "
      f"{'executed ' + str(after3.get('status')) if ran else 'REFUSED ' + str(after3)}")

# Z4. Control on the other side. The lever must not be "True to everything":
# a grant already spent cannot be withdrawn, and withdrawing never unwinds a
# purchase that already happened (BEAAA-35133 B).
a = EvidenceStore(ROOT, tenant="tenant-a")
m4, grant4 = seed_mandate(a, "case-z4")
a.record_case(case_id="case-z4", grant_id=grant4, asserted_sku="SKU-1", asserted_amount_cents=100,
              idempotency_key="key-z4", merchant=SyntheticMerchantAdapter())
late = a.revoke_approval(grant4)
still_there = a.packet_or_none("case-z4")
probe("Z4", "a spent approval cannot be withdrawn, and withdrawal never unwinds a completed purchase",
      late is False and still_there is not None,
      f"revoke_approval after record_case -> {late}; the recorded case reads "
      f"{still_there.get('status') if still_there else 'GONE'}")

# Z5. What Z2 costs in money. The user withdrew consent, the purchase happened
# anyway, and the outcome matches what the receipt says was approved -- so the
# claim is asked what it makes of a charge the user had tried to stop.
a = EvidenceStore(ROOT, tenant="tenant-a")
seed_receipt(a, "task-z5")
a.revoke_approval("task-z5")
# BEAAA-35553 landed the Path 1 withdrawal lever after this probe was written,
# so execute_approved_task now refuses outright and this script used to die here
# with CheckNotApproved instead of reporting. The refusal is the lever working
# and Z2 already asserts it, but it is not what Z5 asks. Z5 asks what the claim
# makes of a charge that happened anyway, and an agent that ignores a withdrawal
# does not ask permission first - so the execution is recorded through
# record_outcome, the documented entry point for a task that executed outside
# this process. Both halves are the requirement now: the door refuses, and a
# charge that got through it still settles with the withdrawal on the record.
ran, refusal = outcome_of(a.execute_approved_task, "task-z5",
                          merchant=SyntheticMerchantAdapter(),
                          executed_at="2026-09-20T09:30:00Z")
a.record_outcome("task-z5", ExecutedOutcome(
    ACTION.description, ACTION.merchant, ACTION.amount_cents,
    evidence_ref="merchant-confirmation-z5", observed_at="2026-09-20T09:30:00Z"),
    executed_at="2026-09-20T09:30:00Z")
claim = a.claim_for("task-z5")
blob = str(claim).lower()
recorded = any(w in blob for w in ("revok", "withdraw"))
probe("Z5", "the withdrawal lever refuses at the door, and a charge that happened anyway "
      "settles with the withdrawal on the record",
      (not ran) and recorded,
      f"execute_approved_task after withdrawal -> {refusal if not ran else 'EXECUTED'}; "
      f"recorded out-of-process instead -> claim={claim.get('claim')!r} "
      f"covered={claim.get('covered')} at_risk={claim.get('amount_at_risk_cents')}; "
      f"withdrawal appears in the settled claim: {recorded}")

w = sum(1 for r in ROWS if r[1] == "HOLDS")
print(f"BEAAA-34396 round-seven probes  probes={len(ROWS)} holds={w} fails={len(ROWS) - w}\n")
for pid, st, req, detail in ROWS:
    print(f"{pid} {st}: {req}\n      {detail}")
