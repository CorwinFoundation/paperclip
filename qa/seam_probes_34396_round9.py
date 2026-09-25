"""Round-nine QA probes, BEAAA-34396: the ruled behaviour for an un-zoned
cancellation deadline, and what it leaves behind.

The CEO ruled card `f1fb383c` on 2026-09-25T00:37Z: option `validate-at-capture`.
An un-zoned cancellation deadline is rejected at capture, and "the rejection
message [must] name the missing offset explicitly so the caller can fix it in
one attempt".

The first job here is to find out whether that ruling needs implementing at all
before anyone spends a cycle on it, and the second is the question a capture-time
refusal always raises: what happens to rows captured before the gate existed.
`claim_for` rebuilds the receipt through `_receipt_from_json`, which reconstructs
`ProposedAction`, which is where the refusal lives -- so the refusal is on the
read path too, whether or not that was intended.

Q4 writes the pre-gate row by inserting receipt JSON straight into the table,
which is the only way to produce one now; that is deliberate, not a probe using
a private route to manufacture a finding. A row in exactly that shape is what any
database written before BEAAA-35480 contains. Report-only.
"""
import json
import pathlib
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from stage_a_demo.core import EvidenceStore  # noqa: E402
from stage_a_demo.receipt import (ANSWER_OK, ApprovalReceipt, ProposedAction,  # noqa: E402
                                  UnorderableCancellationDeadline)

ROOT = pathlib.Path(tempfile.mkdtemp(prefix="qa34396-r9-")) / "evidence.sqlite3"
ROWS = []
def probe(pid, req, ok, detail): ROWS.append((pid, "HOLDS" if ok else "FAILS", req, detail))

def call(fn, *a, **kw):
    try:
        return True, fn(*a, **kw)
    except Exception as exc:  # noqa: BLE001 -- the refusal is the evidence
        return False, exc

def action(**kw):
    base = dict(description="Cancel the Ester booking before the refund deadline",
                merchant="bookings.example", amount_cents=0, deposit_cents=40_000,
                is_cancellation_obligation=True, cancellation_terms="free cancellation until the deadline",
                terms_captured_at="2026-09-20T09:00:00Z", terms_evidence_ref="terms-page-snapshot")
    base.update(kw)
    return ProposedAction(**base)

# Q1. The ruling's first clause. Is it already the behaviour at master?
built, res = call(action, cancellation_deadline="2026-10-11T12:00:00")
probe("Q1", "an un-zoned cancellation deadline is refused at capture",
      not built and isinstance(res, UnorderableCancellationDeadline),
      f"ProposedAction(cancellation_deadline='2026-10-11T12:00:00') -> "
      f"{'ACCEPTED' if built else type(res).__name__}")

# Q2. The ruling's second clause, as a readable requirement: the message has to
# be enough to fix it in one attempt, so it must name the offset.
msg = "" if built else str(res)
probe("Q2", "the rejection names the missing offset so the caller can fix it in one attempt",
      not built and "offset" in msg.lower() and "2026-10-11T12:00:00" in msg,
      f"message: {msg[:220]!r}" if not built else "no message; capture was accepted")

# Q3. The remedy the message names has to work, or the refusal is a dead end.
ok3, res3 = call(action, cancellation_deadline="2026-10-11T12:00:00",
                 cancellation_deadline_utc_offset_minutes=120,
                 cancellation_deadline_offset_evidence_ref="merchant-terms-zone-note")
inst = res3.deadline_instant.isoformat() if ok3 and res3.deadline_instant else None
probe("Q3", "the same stamp is accepted when the capture records the offset it was read in, and orders",
      ok3 and inst is not None,
      f"with offset_minutes=120 + evidence ref -> {'accepted, deadline_instant=' + str(inst) if ok3 else type(res3).__name__}")

# Q4. Control that also matters: a zoned stamp is accepted untouched.
ok4, res4 = call(action, cancellation_deadline="2026-10-11T12:00:00+02:00")
probe("Q4", "a stamp that carries its own offset is accepted",
      ok4 and res4.deadline_instant is not None,
      f"'2026-10-11T12:00:00+02:00' -> {'accepted, deadline_instant=' + res4.deadline_instant.isoformat() if ok4 else type(res4).__name__}")

# Q5. The residual the ruling creates. A row captured before the gate carries an
# un-zoned deadline in its stored JSON. The refusal lives in the constructor, and
# the read path rebuilds the receipt through it.
store = EvidenceStore(ROOT, tenant="tenant-a")
good = ApprovalReceipt("rcpt-legacy", "task-legacy",
                       action(cancellation_deadline="2026-10-11T12:00:00+02:00"), ANSWER_OK,
                       "2026-09-20T09:00:00Z", "2026-09-20T09:01:00Z", "2026-09-20T09:01:05Z")
store.record_pre_execution_receipt(good)
# Rewrite that row's stored deadline to the pre-gate, un-zoned shape.
raw = json.loads(store.db.execute(
    "SELECT receipt_json FROM approval_receipts WHERE task_id = 'task-legacy'").fetchone()[0])
raw["proposed_action"]["cancellation_deadline"] = "2026-10-11T12:00:00"
with store.db:
    store.db.execute("UPDATE approval_receipts SET receipt_json = ? WHERE task_id = 'task-legacy'",
                     (json.dumps(raw, sort_keys=True),))
read_ok, read_res = call(store.receipt_for, "task-legacy")
claim_ok, claim_res = call(store.claim_for, "task-legacy")
probe("Q5", "a receipt captured before the gate is still readable, and its claim still settles",
      read_ok and claim_ok,
      f"receipt_for -> {'ok' if read_ok else type(read_res).__name__ + ': ' + str(read_res)[:80]}; "
      f"claim_for -> {'claim=' + str(claim_res.get('claim')) if claim_ok else type(claim_res).__name__}")

# Q6. If Q5 refuses, the defect the library kept for exactly this case can no
# longer be reached by anything. Low stakes next to Q5, but it is the signal that
# the claim-side half of the old design is now dead.
obj = object.__new__(ProposedAction)
for k, v in raw["proposed_action"].items():
    object.__setattr__(obj, k, v)
legacy_defect = obj.deadline_capture_defect  # a property, not a call
reachable_from_capture = ok1_unzoned = False
probe("Q6", "the claim-side capture-defect reading still has a live path that can reach it",
      read_ok,
      f"deadline_capture_defect() on an un-zoned action reads {legacy_defect!r}, but no live path "
      f"can build or read one: capture raises (Q1) and the read path raises (Q5)")

w = sum(1 for r in ROWS if r[1] == "HOLDS")
print(f"BEAAA-34396 round-nine probes @ 0313498b  probes={len(ROWS)} holds={w} fails={len(ROWS)-w}\n")
for pid, st, req, detail in ROWS:
    print(f"{pid} {st}: {req}\n      {detail}")
