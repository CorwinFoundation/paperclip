"""Round-three QA probes, BEAAA-34396: the `and matched` gate and the
read-as-written deadline, at master 751f4875. Round two's two failures are
closed; this checks the closures did not open a third thing, and pins the
residual the UTC reading leaves behind. Report-only."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from stage_a_demo.receipt import (ProposedAction, ExecutedOutcome, ApprovalReceipt, ANSWER_OK,
                                  PERFORMANCE_COUNTERPARTY_CONFIRMED, review_claim,
                                  UnorderableCancellationDeadline)

DESC = "Cancel the Ester booking before the refund deadline"
def action(**kw):
    base = dict(description=DESC, merchant="bookings.example", amount_cents=0, deposit_cents=40_000,
                cancellation_terms="deposit refundable until the deadline",
                cancellation_deadline="2026-10-11T12:00:00Z", terms_captured_at="2026-10-10T09:00:00Z",
                terms_evidence_ref="terms-ester", is_cancellation_obligation=True)
    base.update(kw); return ProposedAction(**base)
def settle(act, out):
    return review_claim(ApprovalReceipt("r", "t", act, ANSWER_OK, "2026-10-10T09:00:00Z",
                                        "2026-10-10T09:05:00Z", "2026-10-10T09:05:05Z"), out)
def outcome(**kw):
    base = dict(description=DESC, merchant="bookings.example", amount_cents=0,
                evidence_ref="no-cancellation-in-inbox", performed=False, observed_at="2026-10-11T12:30:00Z")
    base.update(kw); return ExecutedOutcome(**base)

ROWS = []
def probe(pid, requirement, ok, detail): ROWS.append((pid, "HOLDS" if ok else "FAILS", requirement, detail))

# T1. The omission path is now gated on `matched`. An omission whose evidence row
# does not echo the approved wording must still reach a covered verdict.
r = settle(action(), outcome(description="Ester reservation, no cancellation found"))
probe("T1", "a missed deadline whose evidence row is worded differently is still a covered loss",
      r["covered"] is True, f"claim={r['claim']} covered={r['covered']} at_risk={r['amount_at_risk_cents']}")

# T2. The gate must not give a self-reporting agent a way out. Claiming performance
# on an outcome that does not match must never settle as no loss.
r = settle(action(), ExecutedOutcome("Cancelled something else entirely", "bookings.example", 0,
                                     evidence_ref="agent-says-done", performed=True,
                                     observed_at="2026-10-11T12:30:00Z"))
probe("T2", "a self-reported performance on a mismatching outcome never settles as no loss",
      r["covered"] is True, f"claim={r['claim']} covered={r['covered']}")

r = settle(action(), outcome(performed=True, evidence_ref="agent-says-done"))
probe("T3", "a matching self-reported performance still settles incomplete, not closed",
      r["covered"] is False and "self_reported" in r["claim"], f"claim={r['claim']} covered={r['covered']}")

r = settle(action(), outcome(performed=True, evidence_ref="cancellation-confirmed-by-hotel",
                             performed_source=PERFORMANCE_COUNTERPARTY_CONFIRMED))
probe("T4", "a counterparty-confirmed performance closes the obligation without a claim",
      r["covered"] is False and "missed" not in r["claim"], f"claim={r['claim']} covered={r['covered']}")

# T5. Closed at capture by BEAAA-35480. An un-zoned deadline cannot be ordered in
# any reading that is right across UTC+14..UTC-12, so ProposedAction refuses to hold
# one. A merchant's local-time terms stay legitimate: capture the offset it was read
# in, with its own evidence ref, and the deadline orders like any zoned stamp.
UNZONED = "2026-10-11T12:00:00"
try:
    action(cancellation_deadline=UNZONED)
    refused = False
except UnorderableCancellationDeadline:
    refused = True
resolved = action(cancellation_deadline=UNZONED, cancellation_deadline_utc_offset_minutes=-480,
                  cancellation_deadline_offset_evidence_ref="terms-ester-local-time")
late = settle(resolved, outcome(observed_at="2026-10-11T20:30:00Z"))
early = settle(resolved, outcome(observed_at="2026-10-11T19:30:00Z"))
probe("T5", "an un-zoned deadline never reaches the claim path, and one captured with its offset orders correctly",
      refused and late["covered"] is True and early["covered"] is False and not late["record_defects"],
      f"capture refused the un-zoned stamp={refused}; {UNZONED} at -08:00 = "
      f"{resolved.deadline_instant.isoformat()}; 30min after -> {late['claim']}/{late['covered']}; "
      f"30min before -> {early['claim']}/{early['covered']}; defects={late['record_defects']}")

w = sum(1 for r in ROWS if r[1] == "HOLDS")
print(f"BEAAA-34396 round-three probes (re-pointed for BEAAA-35480)  probes={len(ROWS)} holds={w} fails={len(ROWS)-w}\n")
for pid, st, req, detail in ROWS:
    print(f"{pid} {st}: {req}\n      {detail}")
