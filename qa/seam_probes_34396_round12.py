"""Round-twelve QA probes, BEAAA-34396: which instant the pinned window is ordered against.

BEAAA-35636 made every claim carry the protection state as of the event, and
round four's W4/W5 now hold. This round asks the question that fix leaves open.

Two different instants reach the payload by two different routes:

  * ``review_claim`` sets ``event_at`` from ``event_instant(receipt, outcome)``,
    which prefers the independent evidence's ``observed_at``.
  * ``EvidenceStore._protection_in_force_at`` pins the window against the
    ``executed_at`` the execution side passed to ``record_outcome``.

Every existing probe passes one instant for both, so nothing has asked what the
payload says when they disagree. The execution side chooses ``executed_at``; the
independent record chooses ``observed_at``. If the first can sit inside the
window while the second sits outside it, a claim carries an active pinned window
for an event the independent evidence places outside that window - the W5 shape
reached through the write path instead of the read path.

Report-only. Drives the real store, a real on-disk registry, record then read.
"""
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

PV = "stage-a-demo"
ROOT = tempfile.mkdtemp(prefix="qa34396-r12-")
os.environ["BACKBOND_ACTIVATION_RECORDS"] = ROOT
os.environ["BACKBOND_PROGRAM_VERSION"] = PV

from stage_a_demo.core import EvidenceStore  # noqa: E402
from stage_a_demo.receipt import (ANSWER_OK, ApprovalReceipt, ExecutedOutcome,  # noqa: E402
                                  ProposedAction, review_claim)

NOW = datetime.now(timezone.utc)


def stamp(days):
    return (NOW + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


APPROVED_AT = stamp(-30)


def write_registry(expires_at, approved_at=APPROVED_AT):
    records = {
        "governing_legal_structure": {"record_id": "r1", "program_version": PV,
            "status": "in_good_standing", "entity_name": "BackBond", "jurisdiction": "DE"},
        "executed_backing_authority": {"record_id": "r2", "program_version": PV, "status": "executed",
            "counterparty": "carrier", "executed_at": approved_at, "document_sha256": "a" * 64},
        "funded_financial_security": {"record_id": "r3", "program_version": PV, "status": "funded",
            "instrument": "trust", "funded_amount_cents": 1_000_000, "funded_at": approved_at},
        "approved_versioned_terms": {"record_id": "r4", "program_version": PV, "status": "approved",
            "terms_version": "1.0", "approved_at": approved_at, "expires_at": expires_at},
        "named_claims_and_payment_humans": {"record_id": "r5", "program_version": PV, "status": "named",
            "claims_reviewer": "a human", "payment_approver": "another human"},
        "approved_consumer_disclosure_set": {"record_id": "r6", "program_version": PV, "status": "approved",
            "disclosure_set_id": "d1", "approved_at": approved_at},
    }
    pathlib.Path(ROOT, f"{PV}.json").write_text(json.dumps(records))


ACTION = ProposedAction(description="Hotel Ester, 12-15 Oct, 2 guests", merchant="bookings.example",
                        amount_cents=48_000, non_recoverable=True, cancellation_terms="non-refundable")


def wrong_outcome(observed_at):
    return ExecutedOutcome("Hotel Ester, 19-22 Oct, 2 guests", "bookings.example", 48_000,
                           evidence_ref="confirmation-wrong-dates", observed_at=observed_at)


def settle(store, task, executed_at, observed_at=None):
    """Record then read, with the two instants separately controllable.

    Every earlier round passes one value for both. Splitting them is the whole
    point of this round.
    """
    receipt = ApprovalReceipt("rcpt-" + task, task, ACTION, ANSWER_OK,
                              executed_at, executed_at, executed_at)
    store.record_pre_execution_receipt(receipt)
    return store.record_outcome(
        task, wrong_outcome(observed_at if observed_at is not None else executed_at),
        executed_at=executed_at)


ROWS = []


def probe(pid, requirement, ok, detail):
    ROWS.append((pid, "HOLDS" if ok else "FAILS", requirement, detail))


def new_store():
    return EvidenceStore(":memory:")


RECEIPT = ApprovalReceipt("r", "t", ACTION, ANSWER_OK,
                          stamp(-15), stamp(-15), stamp(-15))

# --------------------------------------------------------------------------
# V1. The window closed five days ago. The execution side says the purchase ran
# fifteen days ago, inside it. The independent record says the event was
# observed one day ago, outside it. Which instant is the pinned window ordered
# against, and does the payload admit the two disagree?
write_registry(expires_at=stamp(-5))
store = new_store()
c = settle(store, "t-split-late", executed_at=stamp(-15), observed_at=stamp(-1))
pinned = c.get("protection_at_execution") or {}
probe("V1",
      "a pinned window is not presented as active for an event the independent evidence "
      "places outside it",
      pinned.get("protection_active") is not True,
      f"executed_at={stamp(-15)} (inside), observed_at={stamp(-1)} (window closed {stamp(-5)}) -> "
      f"claim event_at={c.get('event_at')} "
      f"at_execution protection_active={pinned.get('protection_active')} "
      f"at_execution event_at={pinned.get('event_at')} ordered={pinned.get('ordered')}")

# V2. The same divergence, stated as self-consistency rather than as coverage:
# one payload carries two instants for one event. If they can differ with
# nothing naming the difference, the verdict and the window are two unrelated
# facts again - the defect BEAAA-35636 set out to remove, one layer down.
probe("V2",
      "one claim payload names one event instant, or says that its two sources disagree",
      c.get("event_at") == pinned.get("event_at")
      or bool(pinned.get("reason") or pinned.get("divergence")),
      f"claim event_at={c.get('event_at')} vs at_execution event_at={pinned.get('event_at')}; "
      f"reason={pinned.get('reason')!r}")

# V3. The other direction: the execution side reports a time outside the window
# while the independent evidence places the event inside it. An honest user's
# covered loss must not be lost to a clock the execution side controls.
write_registry(expires_at=stamp(-5))
store = new_store()
c3 = settle(store, "t-split-early", executed_at=stamp(-1), observed_at=stamp(-15))
p3 = c3.get("protection_at_execution") or {}
probe("V3",
      "a loss the independent evidence places inside the window is not read as unprotected "
      "because the execution side reported a later clock",
      p3.get("protection_active") is True or bool(p3.get("reason")),
      f"executed_at={stamp(-1)} (outside), observed_at={stamp(-15)} (inside) -> "
      f"claim event_at={c3.get('event_at')} at_execution protection_active="
      f"{p3.get('protection_active')} at_execution event_at={p3.get('event_at')} "
      f"reason={p3.get('reason')!r}")

# V4. The trust boundary in review_claim itself. A caller hands in a block that
# asserts ordered=True and protection_active=True, with an executed_at far
# outside any plausible window. Nothing in _protection_at_event compares the
# instant to the window it names, so ask whether the assertion is carried.
supplied = {"ordered": True, "protection_active": True, "executed_at": "1999-01-01T00:00:00Z",
            "activation_record_id": "r4", "surface": "receipt"}
c4 = review_claim(RECEIPT, wrong_outcome(stamp(-15)), protection_at_execution=supplied)
p4 = c4.get("protection_at_execution") or {}
probe("V4",
      "a caller-asserted active window is not carried onto the claim unchecked",
      p4.get("protection_active") is not True or p4.get("event_at") == c4.get("event_at"),
      f"supplied ordered=True active=True executed_at=1999-01-01 -> carried "
      f"protection_active={p4.get('protection_active')} event_at={p4.get('event_at')} "
      f"resolved={p4.get('resolved')}; claim event_at={c4.get('event_at')}")

# V5. The fail-closed leg. A caller that holds no registry supplies nothing, and
# the block must still be present and must never say active.
c5 = review_claim(RECEIPT, wrong_outcome(stamp(-15)))
p5 = c5.get("protection_at_execution") or {}
probe("V5",
      "an unsupplied protection state is present, explicitly unresolved, and never active",
      p5.get("protection_active") is None and p5.get("resolved") is False and bool(p5.get("reason")),
      f"no block supplied -> present={'protection_at_execution' in c5} "
      f"resolved={p5.get('resolved')} protection_active={p5.get('protection_active')} "
      f"reason={p5.get('reason')!r}")

# V6. The hole BEAAA-35636's own second commit closed, verified independently
# rather than taken from its commit message: an unordered block that still
# claims active must be dropped, not restated.
c6 = review_claim(RECEIPT, wrong_outcome(stamp(-15)),
                  protection_at_execution={"ordered": False, "protection_active": True})
p6 = c6.get("protection_at_execution") or {}
probe("V6",
      "an unordered block that claims active is dropped rather than carried",
      p6.get("protection_active") is None,
      f"supplied ordered=False active=True -> carried protection_active="
      f"{p6.get('protection_active')} resolved={p6.get('resolved')} reason={p6.get('reason')!r}")

w = sum(1 for r in ROWS if r[1] == "HOLDS")
print(f"BEAAA-34396 round-twelve probes  probes={len(ROWS)} holds={w} fails={len(ROWS) - w}\n")
for pid, st, req, detail in ROWS:
    print(f"{pid} {st}: {req}\n      {detail}")
