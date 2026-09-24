"""Round-three QA probes, BEAAA-34396: the `and matched` gate and the
read-as-written deadline, at master 751f4875. Round two's two failures are
closed; this checks the closures did not open a third thing, and pins the
residual the UTC reading leaves behind. Report-only."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from stage_a_demo.receipt import (ProposedAction, ExecutedOutcome, ApprovalReceipt, ANSWER_OK,
                                  PERFORMANCE_COUNTERPARTY_CONFIRMED, review_claim)

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

# T5. Residual. A deadline captured without a zone is read at UTC. Inside the band the
# stamp actually spans, the verdict is a guess either way; the only trace is a defect flag.
UNZONED = "2026-10-11T12:00:00"
late = settle(action(cancellation_deadline=UNZONED), outcome(observed_at="2026-10-11T12:30:00Z"))
early = settle(action(cancellation_deadline=UNZONED), outcome(observed_at="2026-10-11T11:30:00Z"))
probe("T5", "an un-zoned deadline decides money on a guessed zone, flagged only in record_defects",
      False,
      f"30min after the UTC reading -> {late['claim']}/{late['covered']}; 30min before -> {early['claim']}/"
      f"{early['covered']}; both carry {late['record_defects']}. True offset unknown across UTC+14..UTC-12, "
      f"so either verdict can be wrong. Close it at capture, not at claim.")

w = sum(1 for r in ROWS if r[1] == "HOLDS")
print(f"BEAAA-34396 round-three probes @ 751f4875  probes={len(ROWS)} holds={w} fails={len(ROWS)-w}\n")
for pid, st, req, detail in ROWS:
    print(f"{pid} {st}: {req}\n      {detail}")
