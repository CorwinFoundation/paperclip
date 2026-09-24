"""Round-two QA probes, BEAAA-34396: adversarial checks on the BEAAA-35456
fixes themselves. Round one asked whether three denial-direction defects were
closed; this asks what the closures cost in the other direction. Report-only."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from stage_a_demo.receipt import (ProposedAction, ExecutedOutcome, ApprovalReceipt,
                                  ANSWER_OK, review_claim, earliest_possible_instant,
                                  observation_instant)

DEPOSIT = 40_000
DESC = "Cancel the Ester booking before the refund deadline"

def action(**kw):
    base = dict(description=DESC, merchant="bookings.example", amount_cents=0,
                deposit_cents=DEPOSIT, cancellation_terms="deposit refundable until the deadline",
                cancellation_deadline="2026-10-11T12:00:00Z", terms_captured_at="2026-10-10T09:00:00Z",
                terms_evidence_ref="terms-ester", is_cancellation_obligation=True)
    base.update(kw); return ProposedAction(**base)

def receipt(act, answer=ANSWER_OK):
    return ApprovalReceipt("r-probe", "t-probe", act, answer,
                           "2026-10-10T09:00:00Z", "2026-10-10T09:05:00Z", "2026-10-10T09:05:05Z")

def outcome(**kw):
    base = dict(description=DESC, merchant="bookings.example", amount_cents=0,
                evidence_ref="no-cancellation-in-inbox", performed=False,
                observed_at="2026-10-11T12:30:00Z")
    base.update(kw); return ExecutedOutcome(**base)

def settle(act, out, answer=ANSWER_OK):
    r = review_claim(receipt(act, answer), out)
    return r

ROWS = []
def probe(pid, requirement, ok, detail):
    ROWS.append((pid, "HOLDS" if ok else "FAILS", requirement, detail))

UNZONED = "2026-10-11T12:00:00"

# R1. The un-zoned deadline is read at UTC+14, i.e. 14h earlier than its Zulu
# reading. Any observation inside that 14h band settles as a covered loss while
# the deposit is, on the deadline's most likely reading, still refundable.
r = settle(action(cancellation_deadline=UNZONED), outcome(observed_at="2026-10-11T00:00:00Z"))
probe("R1", "a capture defect alone must not settle a covered loss while the money is still recoverable",
      r["covered"] is False,
      f"un-zoned deadline {UNZONED}, observed 12h BEFORE its Zulu reading -> claim={r['claim']} "
      f"covered={r['covered']} defects={r['record_defects']} (earliest reading "
      f"{earliest_possible_instant(UNZONED).isoformat()})")

r = settle(action(cancellation_deadline=UNZONED), outcome(observed_at="2026-10-11T23:00:00Z"))
probe("R2", "an observation past the un-zoned deadline under every reading is a covered loss",
      r["covered"] is True, f"claim={r['claim']} covered={r['covered']} defects={r['record_defects']}")

r = settle(action(cancellation_deadline=UNZONED), outcome(observed_at="2026-10-10T21:00:00Z"))
probe("R3", "an observation before even the earliest reading of the deadline is not a loss",
      r["covered"] is False, f"claim={r['claim']} covered={r['covered']}")

# R4. The inference now routes any zero-cost action with a deadline and a
# deposit through the obligation path, which is entered BEFORE the
# approved-versus-executed comparison.
mismatch = ExecutedOutcome("Hotel Ester, 19-22 Oct, 2 guests", "bookings.example", 0,
                           evidence_ref="confirmation-wrong-dates", performed=True,
                           observed_at="2026-10-11T09:41:00Z")
r = settle(action(is_cancellation_obligation=False, description="Hold Hotel Ester, 12-15 Oct, 2 guests",
                  cancellation_terms="pay at property, deposit held"), mismatch)
probe("R4", "an executed outcome differing from what the user approved is a covered loss whatever the action's shape",
      r["covered"] is True,
      f"zero-cost hold w/ deadline+deposit, wrong dates delivered -> claim={r['claim']} covered={r['covered']}")

# R5. Regression: the ordinary non-recoverable purchase path must be untouched.
buy = action(description="Hotel Ester, 12-15 Oct, 2 guests", amount_cents=48_000, deposit_cents=0,
             non_recoverable=True, cancellation_deadline=None, is_cancellation_obligation=False)
r = settle(buy, ExecutedOutcome("Hotel Ester, 19-22 Oct, 2 guests", "bookings.example", 48_000,
                                evidence_ref="confirmation-wrong-dates", observed_at="2026-10-01T09:41:00Z"))
probe("R5", "a non-recoverable purchase that arrives wrong is still a covered loss at its full amount",
      r["covered"] is True and r["amount_at_risk_cents"] == 48_000,
      f"claim={r['claim']} covered={r['covered']} at_risk={r['amount_at_risk_cents']}")

# R6. An unparseable deadline must not be shifted into an orderable instant.
r = settle(action(cancellation_deadline="before the end of the week"), outcome(observed_at="2026-10-11T23:00:00Z"))
probe("R6", "a deadline that is prose, not a stamp, settles as incomplete rather than as a guessed instant",
      r["covered"] is False and "incomplete" in r["claim"],
      f"claim={r['claim']} covered={r['covered']} earliest={earliest_possible_instant('before the end of the week')}")

# R7. The un-zoned shift must not apply to the observation side.
r = settle(action(), outcome(observed_at=UNZONED))
probe("R7", "an un-zoned observation is still unorderable and settles nothing",
      r["covered"] is False and "no_orderable_observation" in r["claim"],
      f"claim={r['claim']} covered={r['covered']}")

w = sum(1 for r in ROWS if r[1] == "HOLDS")
print(f"BEAAA-34396 round-two probes @ ca0a64e8  probes={len(ROWS)} holds={w} fails={len(ROWS)-w}\n")
for pid, st, req, detail in ROWS:
    print(f"{pid} {st}: {req}\n      {detail}")
