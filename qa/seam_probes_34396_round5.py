"""Round-five QA probes, BEAAA-34396: the coverage-window join, on the real path.

Round four asked which activation window was in force when the event happened
and got no answer; BEAAA-35456 answers it by pinning the window at execution
(`protection_at_execution`). Round four cannot grade that fix: it reconstructs
the payload itself from `review_claim` + `surface_protection_state`, which is
what `claim_for` assembled when it was written, so it is blind to anything
`claim_for` gained afterwards. This round drives the real calls instead --
a real on-disk activation registry, a real EvidenceStore, record then read --
and asks what a stored window has to survive to be evidence rather than a
re-derivation. Report-only.
"""
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

PV = "stage-a-demo"  # default_program_version(), so the store's own default resolves here
ROOT = tempfile.mkdtemp(prefix="qa34396-r5-")
os.environ["BACKBOND_ACTIVATION_RECORDS"] = ROOT
os.environ["BACKBOND_PROGRAM_VERSION"] = PV

from stage_a_demo.core import EvidenceStore  # noqa: E402  (after the env is set)
from stage_a_demo.receipt import (ANSWER_OK, ApprovalReceipt, ExecutedOutcome,  # noqa: E402
                                  ProposedAction)

NOW = datetime.now(timezone.utc)
def stamp(days):
    return (NOW + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

APPROVED_AT = stamp(-30)

def write_registry(expires_at, approved_at=APPROVED_AT):
    """A real records file on disk, the way the running demo reads one."""
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

def settle(store, task, executed_at):
    """Record the receipt and the outcome the way the product does, then read."""
    receipt = ApprovalReceipt("rcpt-" + task, task, ACTION, ANSWER_OK,
                              executed_at, executed_at, executed_at)
    store.record_pre_execution_receipt(receipt)
    return store.record_outcome(task, wrong_outcome(executed_at), executed_at=executed_at)

ROWS = []
def probe(pid, requirement, ok, detail): ROWS.append((pid, "HOLDS" if ok else "FAILS", requirement, detail))

def new_store():
    return EvidenceStore(":memory:")

# --------------------------------------------------------------------------
# X1. The round-four question, asked of the real path. The window closed five
# days ago; the booking executed fifteen days ago, inside it.
write_registry(expires_at=stamp(-5))
store = new_store()
c = settle(store, "t-inside", stamp(-15))
live = c.get("protection", {})
pinned = c.get("protection_at_execution", {})
probe("X1", "a loss that happened inside the window is read, after it closed, beside the window in force then",
      pinned.get("protection_active") is True and live.get("protection_active") is False,
      f"executed {stamp(-15)} (inside), read now (window closed {stamp(-5)}) -> "
      f"covered={c['covered']} live protection_active={live.get('protection_active')} "
      f"at_execution protection_active={pinned.get('protection_active')} "
      f"activation_record_id={pinned.get('activation_record_id')}")

# X2. The other direction. The window is open now; the booking predates the
# records by two weeks, so nothing was in force when it executed.
write_registry(expires_at=stamp(+60), approved_at=stamp(-10))
store = new_store()
c = settle(store, "t-before", stamp(-24))
live = c.get("protection", {})
pinned = c.get("protection_at_execution", {})
probe("X2", "a loss that predates activation is not presented beside a live protection block alone",
      live.get("protection_active") is True and pinned.get("protection_active") is False,
      f"executed {stamp(-24)} (records approved {stamp(-10)}), read now -> "
      f"live protection_active={live.get('protection_active')} "
      f"at_execution protection_active={pinned.get('protection_active')} "
      f"reasons={[r for r in (pinned.get('reasons') or [])][:3]}")

# X3. The load-bearing claim of the fix is that the block is STORED, not
# re-derived: "recomputing a past window depends on a registry that can move, a
# stored block cannot." So move the registry underneath a settled claim.
write_registry(expires_at=stamp(+60))
store = new_store()
first = settle(store, "t-moved", stamp(-2))
before = dict(first.get("protection_at_execution") or {})
pathlib.Path(ROOT, f"{PV}.json").unlink()          # the records vanish
after_gone = store.claim_for("t-moved").get("protection_at_execution", {})
write_registry(expires_at=stamp(-1))               # and come back expired
after_expired = store.claim_for("t-moved").get("protection_at_execution", {})
probe("X3", "the window pinned at execution survives a registry that moves afterwards",
      before.get("protection_active") is True
      and after_gone.get("protection_active") is True
      and after_expired.get("protection_active") is True,
      f"at execution={before.get('protection_active')}; after the records are deleted="
      f"{after_gone.get('protection_active')}; after they return expired="
      f"{after_expired.get('protection_active')}")

# X4. An execution time that cannot be ordered cannot be placed against a
# window. It must say so rather than default to either side.
write_registry(expires_at=stamp(+60))
store = new_store()
c = settle(store, "t-unorderable", "2026-09-15T09:00:00")   # no zone
pinned = c.get("protection_at_execution", {})
probe("X4", "an unorderable execution time is not placed on either side of the window",
      pinned.get("ordered") is False and pinned.get("protection_active") is None,
      f"executed_at='2026-09-15T09:00:00' -> ordered={pinned.get('ordered')} "
      f"protection_active={pinned.get('protection_active')} reason={pinned.get('reason')}")

# X5. The join must not leak into the verdict. A10/A12 restated on the real
# path: the same settled claim read inside and outside the window must give
# the identical verdict, and coverage must never come from a receipt.
write_registry(expires_at=stamp(+60))
store = new_store()
inside = settle(store, "t-verdict", stamp(-1))
VERDICT = ("claim", "covered", "outcome_state", "amount_at_risk_cents")
inside_v = {k: inside.get(k) for k in VERDICT}
write_registry(expires_at=stamp(-1))               # the window closes underneath it
outside_v = {k: store.claim_for("t-verdict").get(k) for k in VERDICT}
probe("X5", "closing the window underneath a settled claim does not move the verdict",
      inside_v == outside_v and inside_v["covered"] is True,
      f"inside={inside_v} outside={outside_v}")

# X6. Rows written before the column existed are recomputed at read time. A
# recomputed window is not evidence and must not be presented as if it were.
write_registry(expires_at=stamp(+60))
store = new_store()
settle(store, "t-legacy", stamp(-1))
fresh_flag = store.claim_for("t-legacy").get("protection_at_execution", {}).get("recorded_at_execution")
with store.db:
    store.db.execute("UPDATE approval_receipts SET protection_at_execution_json = NULL "
                     "WHERE task_id = ?", ("t-legacy",))
legacy = store.claim_for("t-legacy").get("protection_at_execution", {})
probe("X6", "a recomputed window is labelled as recomputed, and a stored one as stored",
      fresh_flag is True and legacy.get("recorded_at_execution") is False,
      f"stored row recorded_at_execution={fresh_flag}; row with the column nulled -> "
      f"recorded_at_execution={legacy.get('recorded_at_execution')} "
      f"protection_active={legacy.get('protection_active')}")

# X7. A receipt is not rewritable by design. record_outcome is a bare UPDATE,
# so ask whether the pinned window is rewritable through it: settle inside the
# window, then re-record the same task at a time outside it.
write_registry(expires_at=stamp(-5))
store = new_store()
settle(store, "t-rewrite", stamp(-15))             # inside the window
second = store.record_outcome("t-rewrite", wrong_outcome(stamp(-1)), executed_at=stamp(-1))
probe("X7", "the window pinned at execution is not rewritable by re-recording the outcome later",
      (second.get("protection_at_execution") or {}).get("protection_active") is True,
      f"first recorded at {stamp(-15)} (inside) -> True; re-recorded at {stamp(-1)} (outside) -> "
      f"protection_at_execution protection_active="
      f"{(second.get('protection_at_execution') or {}).get('protection_active')}, "
      f"executed_at={(second.get('protection_at_execution') or {}).get('executed_at')}")

w = sum(1 for r in ROWS if r[1] == "HOLDS")
print(f"BEAAA-34396 round-five probes @ c60b0319  probes={len(ROWS)} holds={w} fails={len(ROWS) - w}\n")
for pid, st, req, detail in ROWS:
    print(f"{pid} {st}: {req}\n      {detail}")
