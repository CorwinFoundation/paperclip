"""Round-four QA probes, BEAAA-34396: the coverage window. The brief names
"historical claims after expiry and false coverage activation" as a highest-risk
seam. A10 and A12 pin that a receipt never implies coverage and that the
protection block is read live. Nothing yet asks the other question: when a claim
is settled, which activation window was in force at the moment of the event.
Driven through the same two calls EvidenceStore.claim_for makes. Report-only."""
import pathlib
import sys
from datetime import datetime, timezone
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from stage_a_demo.activation import (MappingRegistry, check_activation, surface_protection_state)
from stage_a_demo.receipt import (ProposedAction, ExecutedOutcome, ApprovalReceipt, ANSWER_OK, review_claim)

PV = "stage-a-probe-v1"
EXPIRY = "2026-10-20T00:00:00Z"
def records(expires_at=EXPIRY):
    return {"governing_legal_structure": {"record_id": "r1", "program_version": PV,
                "status": "in_good_standing", "entity_name": "BackBond", "jurisdiction": "DE"},
            "executed_backing_authority": {"record_id": "r2", "program_version": PV, "status": "executed",
                "counterparty": "carrier", "executed_at": "2026-09-01T00:00:00Z", "document_sha256": "a"*64},
            "funded_financial_security": {"record_id": "r3", "program_version": PV, "status": "funded",
                "instrument": "trust", "funded_amount_cents": 1_000_000, "funded_at": "2026-09-01T00:00:00Z"},
            "approved_versioned_terms": {"record_id": "r4", "program_version": PV, "status": "approved",
                "terms_version": "1.0", "approved_at": "2026-09-01T00:00:00Z", "expires_at": expires_at},
            "named_claims_and_payment_humans": {"record_id": "r5", "program_version": PV, "status": "named",
                "claims_reviewer": "a human", "payment_approver": "another human"},
            "approved_consumer_disclosure_set": {"record_id": "r6", "program_version": PV, "status": "approved",
                "disclosure_set_id": "d1", "approved_at": "2026-09-01T00:00:00Z"}}
def reg(expires_at=EXPIRY):
    return MappingRegistry(by_version={PV: records(expires_at)})
def at(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))

# One settled claim: a non-refundable booking that arrived wrong. Executed 2026-10-05.
ACTION = ProposedAction(description="Hotel Ester, 12-15 Oct, 2 guests", merchant="bookings.example",
                        amount_cents=48_000, non_recoverable=True, cancellation_terms="non-refundable")
RECEIPT = ApprovalReceipt("r", "t", ACTION, ANSWER_OK, "2026-10-05T09:07:00Z",
                          "2026-10-05T09:09:00Z", "2026-10-05T09:09:05Z")
OUTCOME = ExecutedOutcome("Hotel Ester, 19-22 Oct, 2 guests", "bookings.example", 48_000,
                          evidence_ref="confirmation-wrong-dates", observed_at="2026-10-05T09:41:00Z")
def claim(now_stamp, expires_at=EXPIRY):
    """What claim_for assembles: the verdict, then the read-time protection block."""
    review = review_claim(RECEIPT, OUTCOME)
    review["protection"] = surface_protection_state("receipt", PV, reg(expires_at), now=at(now_stamp))
    return review

ROWS = []
def probe(pid, requirement, ok, detail): ROWS.append((pid, "HOLDS" if ok else "FAILS", requirement, detail))

s = check_activation(PV, MappingRegistry(by_version={}), now=at("2026-10-06T00:00:00Z"))
probe("W1", "an empty registry is not active and says so", s.active is False and bool(s.statement),
      f"active={s.active} reasons={s.reasons[:2]}")

s = check_activation(PV, reg(), now=at("2026-10-25T00:00:00Z"))
probe("W2", "the gate expires: past the terms expiry it is not active, named as expired",
      s.active is False and any("expired" in r for r in s.reasons),
      f"active={s.active} reasons={[r for r in s.reasons if 'expir' in r]}")

before = check_activation(PV, reg(), now=at("2026-10-19T23:59:59Z")).active
after = check_activation(PV, reg(), now=at("2026-10-20T00:00:01Z")).active
probe("W3", "the gate is bound to a time, so the window itself is real",
      before is True and after is False, f"active just before expiry={before}, just after={after}")

# W4. Read the same claim after the window closed. The event happened inside it.
late = claim("2026-10-25T00:00:00Z")
probe("W4", "a claim settled for an event inside the window carries the protection state in force then, not only at read time",
      late["protection"].get("protection_active") is True or "protection_at_execution" in late,
      f"executed 2026-10-05 (inside), read 2026-10-25 (after expiry) -> claim={late['claim']} "
      f"covered={late['covered']} protection_active={late['protection'].get('protection_active')}; "
      f"payload keys={sorted(late)}")

# W5. The other direction: the event predates any activation, read while active.
early = claim("2026-10-06T00:00:00Z")
probe("W5", "a claim for an event outside the window is not presented beside an active protection block",
      "protection_at_execution" in early or early["covered"] is False,
      f"same claim read 2026-10-06 while active -> covered={early['covered']} "
      f"protection_active={early['protection'].get('protection_active')}; nothing in the payload "
      f"orders the event against the window")

w = sum(1 for r in ROWS if r[1] == "HOLDS")
print(f"BEAAA-34396 round-four probes @ 20e39859  probes={len(ROWS)} holds={w} fails={len(ROWS)-w}\n")
for pid, st, req, detail in ROWS:
    print(f"{pid} {st}: {req}\n      {detail}")
