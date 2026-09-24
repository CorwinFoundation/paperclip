"""Independent QA probes, BEAAA-34396, for the two seams the CTO carried into
acceptance: amount at risk on a cancellation obligation, and offset-bearing
timestamps. Written against the public library API, not the implementation's
own tests. Report-only."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from stage_a_demo.receipt import (ProposedAction, ExecutedOutcome, ApprovalReceipt,
                                  ANSWER_OK, review_claim, amount_at_risk_cents)

DEADLINE = "2026-10-11T12:00:00Z"
DEPOSIT = 40_000

def action(**kw):
    base = dict(description="Cancel the Ester booking before the refund deadline",
                merchant="bookings.example", amount_cents=0, deposit_cents=DEPOSIT,
                cancellation_terms="deposit refundable until 2026-10-11T12:00:00Z",
                cancellation_deadline=DEADLINE, terms_captured_at="2026-10-10T09:00:00Z",
                terms_evidence_ref="terms-ester", is_cancellation_obligation=True)
    base.update(kw)
    return ProposedAction(**base)

def receipt(act):
    return ApprovalReceipt("r-probe", "t-probe", act, ANSWER_OK,
                           "2026-10-10T09:00:00Z", "2026-10-10T09:05:00Z", "2026-10-10T09:05:05Z")

def outcome(**kw):
    base = dict(description="Cancel the Ester booking before the refund deadline",
                merchant="bookings.example", amount_cents=0, evidence_ref="no-cancellation-in-inbox",
                performed=False, observed_at="2026-10-11T12:30:00Z")
    base.update(kw)
    return ExecutedOutcome(**base)

def settle(act, out):
    r = review_claim(receipt(act), out)
    return r["claim"], r["covered"], r.get("amount_at_risk_cents", amount_at_risk_cents(act))

ROWS = []
def probe(pid, requirement, ok, detail):
    ROWS.append((pid, "HOLDS" if ok else "FAILS", requirement, detail))

# --- Seam 1: money at risk on a cancellation obligation -------------------
claim, cov, risk = settle(action(), outcome())
probe("S1-1", "a missed cancellation deadline puts the deposit at risk, not zero",
      cov is True and risk == DEPOSIT, f"claim={claim} covered={cov} at_risk={risk}")

claim, cov, risk = settle(action(non_recoverable=True), outcome())
probe("S1-2", "labelling the deposit non-recoverable must never LOWER the money at risk",
      cov is True and risk >= DEPOSIT, f"non_recoverable=True -> claim={claim} covered={cov} at_risk={risk} (refundable-label case reads {DEPOSIT})")

claim, cov, risk = settle(action(is_cancellation_obligation=False), outcome())
probe("S1-3", "an obligation with the flag unset must not silently grade as no loss",
      cov is True, f"is_cancellation_obligation=False -> claim={claim} covered={cov} at_risk={risk}")

claim, cov, risk = settle(action(deposit_cents=0, amount_cents=0), outcome())
probe("S1-4", "an obligation with no captured deposit must not settle as a covered zero-value loss",
      not (cov is True and risk == 0), f"deposit=0 -> claim={claim} covered={cov} at_risk={risk}")

# --- Seam 2: offset-bearing timestamps ------------------------------------
same_instant = {"Z": "2026-10-11T14:30:00Z", "+03:00": "2026-10-11T17:30:00+03:00",
                "-05:00": "2026-10-11T09:30:00-05:00", "+14:00": "2026-10-12T04:30:00+14:00",
                "lower z": "2026-10-11T14:30:00z"}
got = {k: settle(action(), outcome(observed_at=v)) for k, v in same_instant.items()}
probe("S2-1", "five spellings of the same instant (14:30Z, past the deadline) settle identically as a covered loss",
      len({g for g in got.values()}) == 1 and all(g[1] is True for g in got.values()),
      "; ".join(f"{k}->{g[0]}/{g[1]}" for k, g in got.items()))

claim, cov, _ = settle(action(), outcome(observed_at="2026-10-11T14:30:00+03:00"))  # 11:30Z, inside
probe("S2-2", "+03:00 stamp reading 11:30Z is inside a 12:00Z window, not a missed deadline",
      cov is False and "within_cancellation_window" in claim, f"claim={claim} covered={cov}")

claim, cov, _ = settle(action(), outcome(observed_at="2026-10-11T09:30:00-05:00"))  # 14:30Z, past
probe("S2-3", "-05:00 stamp reading 14:30Z is past a 12:00Z deadline and is a covered loss",
      cov is True and "missed_cancellation_deadline" in claim, f"claim={claim} covered={cov}")

claim, cov, _ = settle(action(), outcome(observed_at="2026-10-11T13:30:00"))
probe("S2-4", "a zone-less observation cannot be ordered and never settles a denial",
      cov is False and "no_orderable_observation" in claim, f"claim={claim} covered={cov}")

claim, cov, _ = settle(action(cancellation_deadline="2026-10-11T12:00:00"), outcome(observed_at="2026-10-11T23:00:00Z"))
probe("S2-5", "a zone-less DEADLINE with a plainly-past observation must not read as an unsettleable non-loss",
      cov is True, f"zone-less deadline -> claim={claim} covered={cov}")

claim, cov, _ = settle(action(), outcome(observed_at=DEADLINE))
probe("S2-6", "an observation exactly at the deadline instant is not yet past it",
      cov is False, f"claim={claim} covered={cov}")

claim, cov, _ = settle(action(), outcome(observed_at="2026-10-11T09:00:00Z", independent=False,
                                         evidence_ref="agent-says-not-yet"))
probe("S2-7", "an observation with no independent evidence must not settle a denial on its own timing",
      cov is False and "no_independent_evidence" in claim or "INCOMPLETE" in claim.upper(),
      f"independent=False, agent-chosen early time -> claim={claim} covered={cov}")

w = sum(1 for r in ROWS if r[1] == "HOLDS")
print(f"BEAAA-34396 seam probes @ f460358d  probes={len(ROWS)} holds={w} fails={len(ROWS)-w}\n")
for pid, st, req, detail in ROWS:
    print(f"{pid} {st}: {req}\n      {detail}")
