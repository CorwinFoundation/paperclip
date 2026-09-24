"""Independent abuse-case harness for the Stage A purchase-protection seams (BEAAA-34396).

Report-only. Every probe drives the real stage_a_demo code from the outside and
prints what the code actually does against what the Stage A acceptance scope
requires. Nothing here asserts; the harness always exits 0 so it can run as a
scan next to the suite without gating a branch.

Run:  python tools/stage_a_abuse_harness.py
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import sqlite3
import sys
import tempfile
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stage_a_demo import activation as act  # noqa: E402
from stage_a_demo import approval as approval_mod  # noqa: E402
from stage_a_demo.approval import approve_mandate  # noqa: E402
from stage_a_demo import path1 as path1_mod  # noqa: E402
from stage_a_demo.receipt import (  # noqa: E402
    ANSWER_OK,
    ANSWER_SKIPPED,
    ApprovalReceipt,
    ExecutedOutcome,
    assess_warnings,
    review_claim,
)
from stage_a_demo.core import (
    MandateExceeded,  # noqa: E402
    APPROVAL_ORIGINS,
    ApprovalVerifier,
    EvidenceStore,
    HumanApprovalIssuer,
    Mandate,
    SyntheticMerchantAdapter,
)

_ACTIVATION_RECORDS = {
    "governing_legal_structure": {
        "record_id": "legal-1", "program_version": "", "status": "in_good_standing",
        "entity_name": "BackBond Program Co", "jurisdiction": "Delaware, US"},
    "executed_backing_authority": {
        "record_id": "backing-1", "program_version": "", "status": "executed",
        "counterparty": "Backing Counterparty", "executed_at": "2026-09-01T00:00:00Z",
        "document_sha256": "a" * 64},
    "funded_financial_security": {
        "record_id": "funding-1", "program_version": "", "status": "funded",
        "instrument": "segregated account", "funded_amount_cents": 5_000_000,
        "funded_at": "2026-09-02T00:00:00Z"},
    "approved_versioned_terms": {
        "record_id": "terms-1", "program_version": "", "status": "approved",
        "terms_version": "terms-1.0.0", "approved_at": "2026-09-03T00:00:00Z",
        "expires_at": "2027-09-23T12:00:00Z"},
    "named_claims_and_payment_humans": {
        "record_id": "humans-1", "program_version": "", "status": "named",
        "claims_reviewer": "Named Reviewer", "payment_approver": "Named Approver"},
    "approved_consumer_disclosure_set": {
        "record_id": "disclosures-1", "program_version": "", "status": "approved",
        "disclosure_set_id": "disclosures-1.0.0", "approved_at": "2026-09-04T00:00:00Z"},
}
"""A complete activation-record set, mirroring the one in
tests/test_beaaa35012_activation_gate.py. Inlined rather than imported: the
harness drives stage_a_demo from outside and must not depend on the suite."""


RESULTS: list[dict] = []


def probe(case: str, requirement: str):
    def wrap(fn):
        def run():
            tmp = tempfile.TemporaryDirectory()
            try:
                held, observed = fn(Path(tmp.name))
            except Exception as exc:  # a crashing probe is a finding, not a stop
                held, observed = False, f"probe raised {type(exc).__name__}: {exc}"
            finally:
                tmp.cleanup()
            RESULTS.append(
                {"case": case, "requirement": requirement, "holds": held, "observed": observed}
            )

        run.case = case
        return run

    return wrap


class CountingMerchant(SyntheticMerchantAdapter):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def purchase(self, **kwargs):
        self.calls.append(dict(kwargs))
        return super().purchase(**kwargs)


def _approved(store: EvidenceStore, case_id: str, mandate: Mandate) -> str:
    """Drive the human approval step and return the only thing that crosses.

    Ported at BEAAA-35331 (`7ccdb0ae`): the execution path no longer receives a
    token or a verifier, so every probe below asks the human-side module for a
    grant id the same way the running app does.
    """
    return approve_mandate(store, case_id=case_id, mandate=mandate)


def _stored_token(store: EvidenceStore, grant_id: str) -> str:
    """Read the recorded token back out of the DB, for leak checks only."""
    row = store.db.execute(
        "SELECT approval_token FROM approvals WHERE grant_id = ?", (grant_id,)
    ).fetchone()
    return row["approval_token"]


def _store(root: Path) -> EvidenceStore:
    return EvidenceStore(root / "evidence.sqlite3")


@probe("A1 agent-self-approval", "an execution-side caller cannot mint an approval the server accepts")
def a1(root: Path):
    mandate = Mandate("m-a1", "SKU-1", 100)
    # No default key, and no key at all, are both refused.
    try:
        HumanApprovalIssuer()
        default_construction = "constructed with no key"
    except TypeError:
        default_construction = "refused: no default key"

    # The agent picks its own key and mints a token for the mandate it wants.
    agent_issuer = HumanApprovalIssuer("agent-chosen-secret")
    grant_id = "grant-agent-minted"
    forged = agent_issuer.approve(mandate, grant_id)

    store = _store(root)
    merchant = CountingMerchant()
    try:
        # Route 1: the private writer, presenting the agent's own issuer.
        try:
            store._record_approval(
                grant_id=grant_id, case_id="a1", mandate=mandate, approval_token=forged,
                origin=APPROVAL_ORIGINS[0], issuer=agent_issuer,
            )
            writer = "WROTE an approvals row"
        except Exception as exc:
            writer = f"refused: {type(exc).__name__}"

        # Route 2: straight SQL, with an origin from the allowlist so the cheap
        # check cannot mask the result.
        with store.db:
            store.db.execute(
                "INSERT OR REPLACE INTO approvals (grant_id, case_id, mandate_json,"
                " approval_token, origin) VALUES (?,?,?,?,?)",
                (grant_id, "a1", json.dumps(asdict(mandate), sort_keys=True), forged,
                 APPROVAL_ORIGINS[0]),
            )
        try:
            packet = store.record_case(
                case_id="a1", grant_id=grant_id, asserted_sku="SKU-1",
                asserted_amount_cents=100, idempotency_key="a1", merchant=merchant,
            )
            executed = f"status={packet['status']}"
        except PermissionError as exc:
            executed = f"refused: {exc}"
    finally:
        store.close()
    holds = (
        default_construction.startswith("refused")
        and writer.startswith("refused")
        and str(executed).startswith("refused")
        and not merchant.calls
    )
    return holds, (
        f"HumanApprovalIssuer() with no key -> {default_construction};"
        f" _record_approval with the agent's own issuer -> {writer};"
        f" raw-SQL row at origin={APPROVAL_ORIGINS[0]!r} then record_case -> {executed};"
        f" merchant calls={len(merchant.calls)}."
        f" The token has to be sha256(process secret:grant_id:mandate), so an agent-minted"
        f" one fails attestation whatever route writes the row"
    )


@probe("A2 mandate replay", "one human approval authorises at most one execution")
def a2(root: Path):
    mandate = Mandate("m-a2", "SKU-1", 100)
    merchant = CountingMerchant()
    store = _store(root)
    refusals: list[str] = []
    try:
        grant = _approved(store, "a2", mandate)
        for n in range(5):  # one approval, fresh idempotency key each time
            try:
                store.record_case(
                    case_id="a2", grant_id=grant, asserted_sku="SKU-1",
                    asserted_amount_cents=100, idempotency_key=f"a2-{n}", merchant=merchant,
                )
            except PermissionError as exc:
                refusals.append(str(exc))
    finally:
        store.close()
    return len(merchant.calls) <= 1, (
        f"one approval of mandate m-a2 drove {len(merchant.calls)} merchant execution(s)"
        f" ({sum(c['amount_cents'] for c in merchant.calls)} cents against a 100 cent mandate);"
        f" attempts 2-5 refused with {sorted(set(refusals)) or 'nothing'}."
        f" Single use now comes from consuming the grant row inside record_case"
    )


@probe("A3 revocation", "a human can revoke an approval and stop later executions")
def a3(root: Path):
    """Exercised, not matched by name.

    Until BEAAA-35133 this probe passed or failed on whether any callable had
    "revoke" in its name, and its message asserted the negative regardless. A
    name is not a behaviour: `revoke_approval` could exist and do nothing. So
    revoke a real grant and see whether the merchant is reachable afterwards.
    """
    surfaces = {
        name
        for obj in (HumanApprovalIssuer, ApprovalVerifier, EvidenceStore)
        for name, _ in inspect.getmembers(obj, callable)
        if not name.startswith("_")
    }
    named = sorted(s for s in surfaces if "revoke" in s or "revocation" in s or "expire" in s)
    merchant = CountingMerchant()
    store = _store(root)
    try:
        mandate = Mandate("m-a3", "SKU-1", 100)
        grant = _approved(store, "a3", mandate)
        revoked = store.revoke_approval(grant) if hasattr(store, "revoke_approval") else None
        try:
            store.record_case(
                case_id="a3", grant_id=grant, asserted_sku="SKU-1",
                asserted_amount_cents=100, idempotency_key="a3-after-revoke",
                merchant=merchant,
            )
            after = "EXECUTED"
        except PermissionError as exc:
            after = f"refused: {exc}"

        # Revoking a grant that was already spent must not unwind the purchase.
        spent = _approved(store, "a3b", mandate)
        store.record_case(
            case_id="a3b", grant_id=spent, asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="a3b", merchant=merchant,
        )
        revoke_after_spend = store.revoke_approval(spent)
        still_there = store.packet_or_none("a3b") is not None
    finally:
        store.close()
    holds = bool(named) and after.startswith("refused") and still_there
    return holds, (
        f"revocation surface={named or 'none'}; revoke_approval on an unspent grant"
        f" returned {revoked}, and executing it afterwards -> {after};"
        f" merchant calls={len(merchant.calls)} (the one legitimate purchase);"
        f" revoking an already-spent grant returned {revoke_after_spend} and the recorded"
        f" case survives={still_there}, so revocation stops future executions without"
        f" unwinding a purchase that already happened"
    )


@probe("A4 over-mandate execution", "an assertion outside the mandate must not reach the merchant")
def a4(root: Path):
    """The mandate must be enforced before the side effect, never reconciled after.

    Scope note (BEAAA-34396, CEO ruling 2026-09-24 on Addendum 2): this probe
    covers the *capability-backed* path, where BackBond holds the grant and calls
    the merchant adapter. There, an assertion outside the mandate is REFUSED —
    the grant is not consumed and the adapter is not called. The Founder's
    Addendum 2 approval-receipt flow (Path 1) is a different path: BackBond only
    observes, holds no capability and calls no adapter, so "over your limit" is a
    warning on the receipt and never a veto. That does not soften this row. An
    increment that trades this refusal down to a warning on the capability path
    fails A4, whatever it cites — the predicate below requires a refusal, zero
    merchant calls and no recorded case, and a warning-only outcome satisfies
    none of the three.
    """
    mandate = Mandate("m-a4", "SKU-1", 100)
    merchant = CountingMerchant()
    store = _store(root)
    try:
        grant = _approved(store, "a4", mandate)
        refusal = None
        packet = None
        try:
            packet = store.record_case(
                case_id="a4", grant_id=grant, asserted_sku="SKU-PREMIUM",
                asserted_amount_cents=999_900, idempotency_key="a4", merchant=merchant,
            )
        except MandateExceeded as exc:
            refusal = str(exc)
        recorded = store.packet_or_none("a4")
        # A refusal that spends the human's one approval is not a clean refusal:
        # the grant has to survive for an assertion that is within the mandate.
        grant_still_unspent = store._approval_preview(grant, "a4") is not None
    finally:
        store.close()
    charged = merchant.calls[0]["amount_cents"] if merchant.calls else None
    holds = bool(refusal) and not merchant.calls and recorded is None and grant_still_unspent
    if holds:
        return True, (
            f"agent asserted 999900 cents against a 100 cent mandate; refused before the side"
            f" effect with {refusal!r}; merchant calls=0; no case recorded; the grant is still"
            f" unspent. Enforced before the side effect, not reconciled after it."
        )
    return False, (
        f"agent asserted 999900 cents against a 100 cent mandate; refusal={refusal!r};"
        f" merchant was called {len(merchant.calls)} time(s) at amount_cents={charged};"
        f" recorded case={'none' if recorded is None else recorded['status']};"
        f" grant still unspent={grant_still_unspent}."
        f" Expected a refusal, zero merchant calls, no recorded case and an unspent grant."
    )


@probe("A5 evidence tampering", "a recorded case is append-only; its sources cannot be rewritten")
def a5(root: Path):
    store = _store(root)
    rewrite_merchant = CountingMerchant()
    refusal = None
    try:
        first = Mandate("m-a5-real", "SKU-1", 100)
        before = store.record_case(
            case_id="a5", grant_id=_approved(store, "a5", first), asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="a5-one",
            merchant=SyntheticMerchantAdapter(),
        )
        # Same case_id, different mandate, its own human approval, fresh key.
        second = Mandate("m-a5-rewritten", "SKU-COVER-UP", 1)
        try:
            store.record_case(
                case_id="a5", grant_id=_approved(store, "a5", second), asserted_sku="SKU-COVER-UP",
                asserted_amount_cents=1, idempotency_key="a5-two",
                merchant=rewrite_merchant,
            )
        except PermissionError as exc:
            # BEAAA-35357: the ledger is append-only, so the rewrite is refused
            # rather than recorded. A refusal is the requirement holding; the
            # probe still measures that the first case survived it intact.
            refusal = f"PermissionError: {exc}"
        after = store.packet("a5")
    finally:
        store.close()
    rewritten = before["sources"]["mandate"] != after["sources"]["mandate"]
    stale_exec = after["execution"]["idempotency_key"] == "a5-one"
    raw_calls = getattr(rewrite_merchant, "calls", None)
    rewrite_calls = len(raw_calls) if isinstance(raw_calls, list) else raw_calls
    paired = (not rewritten) and stale_exec
    return (not rewritten) and refusal is not None, (
        f"second record under case_id a5 -> refused: {refusal or 'NOT REFUSED'};"
        f" merchant calls on the rewrite attempt={rewrite_calls};"
        f" mandate stayed {after['sources']['mandate']['mandate_id']} (rewritten={rewritten});"
        f" packet reports execution {after['execution']['idempotency_key']}"
        f" (matches the surviving case={paired}). The ledger is append-only and the"
        f" refusal lands before the merchant, so neither the evidence nor the charge"
        f" happens twice under one case"
    )


@probe("A6 duplicate events", "a retried idempotency key executes exactly once")
def a6(root: Path):
    mandate = Mandate("m-a6", "SKU-1", 100)
    merchant = CountingMerchant()
    store = _store(root)
    retries: list[str] = []
    try:
        grant = _approved(store, "a6", mandate)
        kwargs = dict(
            case_id="a6", grant_id=grant, asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="a6", merchant=merchant,
        )
        for _ in range(4):
            try:
                store.record_case(**kwargs)
                retries.append("packet")
            except PermissionError as exc:
                retries.append(f"{type(exc).__name__}: {exc}")
        count = store.execution_count("a6")
    finally:
        store.close()
    # A retry has to come back with the receipt, not just fail to reach the
    # merchant twice: the probe passed on the merchant count alone while the
    # retry was answering PermissionError (BEAAA-35354 A6).
    returned_receipt = set(retries) == {"packet"}
    reading = (
        "every retry returned the idempotent packet, so a client whose request"
        " timed out gets its receipt back (BEAAA-35354 A6)"
        if returned_receipt
        else "exactly-once holds at the merchant, but the retry does not return the"
        " idempotent packet: record_case consumes the grant before it looks at the"
        " idempotency key, so a client retrying a timed-out request gets a"
        " PermissionError rather than its receipt"
    )
    return count == 1 and len(merchant.calls) == 1 and returned_receipt, (
        f"4 retries of one idempotency key -> execution_count={count},"
        f" merchant calls={len(merchant.calls)}, outcomes={retries}. {reading}"
    )


@probe("A7 concurrent reservations", "racing callers on one key reserve the limit once")
def a7(root: Path):
    mandate = Mandate("m-a7", "SKU-1", 100)
    merchant = CountingMerchant()
    path = root / "evidence.sqlite3"
    seed = EvidenceStore(path)  # create schema and one approval, then race connections
    try:
        grant = _approved(seed, "a7", mandate)
    finally:
        seed.close()
    errors: list[str] = []
    barrier = threading.Barrier(6)

    def worker(n: int) -> None:
        store = EvidenceStore(path)
        barrier.wait()
        try:
            store.record_case(
                case_id="a7", grant_id=grant, asserted_sku="SKU-1",
                asserted_amount_cents=100, idempotency_key="a7", merchant=merchant,
            )
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            store.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    store = EvidenceStore(path)
    try:
        count = store.execution_count("a7")
    finally:
        store.close()
    unexpected = [e for e in errors if not e.startswith("PermissionError")]
    return count == 1 and len(merchant.calls) <= 1 and not unexpected, (
        f"6 concurrent callers on key a7 -> execution rows={count},"
        f" merchant calls={len(merchant.calls)}, raised={sorted(set(errors)) or 'none'}."
        f" Predicate changed at BEAAA-35331: a losing racer is now REFUSED because the"
        f" single-use grant is already spent, which is a correct outcome, so"
        f" PermissionError no longer fails this probe. Anything else still does"
        f" (unexpected={sorted(set(unexpected)) or 'none'})"
    )


@probe("A8 missing evidence fails closed", "no independent evidence must never read as verified")
def a8(root: Path):
    mandate = Mandate("m-a8", "SKU-1", 100)
    store = _store(root)
    try:
        packet = store.record_case(
            case_id="a8", grant_id=_approved(store, "a8", mandate), asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="a8",
            merchant=SyntheticMerchantAdapter(), independent_evidence=False,
        )
    finally:
        store.close()
    receipt = json.loads(packet["execution"]["receipt_json"])
    holds = (
        packet["status"] == "UNKNOWN/INCOMPLETE"
        and packet["sources"]["independent_merchant_evidence"] is None
        and receipt["status"] == "UNKNOWN/INCOMPLETE"
    )
    return holds, (
        f"status={packet['status']}, receipt.status={receipt['status']},"
        f" merchant evidence={packet['sources']['independent_merchant_evidence']},"
        f" claims_review links={packet['claims_review']['source_links']}"
    )


@probe("A9 tenant isolation", "one tenant's case cannot be read through another tenant's reader")
def a9(root: Path):
    """Behavioural, in three legs. BEAAA-34396 QA re-write.

    The predicate this replaced was ``bool(tenant_aware)`` over the parameter
    names of ``record_case``/``packet``. It never passed a tenant and its second
    reader was opened on the same default tenant, so *adding a parameter named
    ``tenant`` flipped the row to HOLDS with no behaviour tested* — and at
    83650aa1 it did exactly that while ``record_case(tenant=...)`` charged the
    merchant and then raised ``KeyError`` on its own return path. A probe that
    reads a signature mirrors the implementation; it cannot carry this row.

    Leg 1 (read-back): a case written as tenant-a must come back to tenant-a.
    Leg 2 (isolation):  a reader holding tenant-b must not get that case.
    Leg 3 (control):    tenant-a must still get it, so "refuse every read"
                        cannot pass leg 2.

    Whichever seam the implementation offers is exercised: a per-call ``tenant``
    argument if ``record_case`` takes one, and a ``tenant`` constructor argument
    if ``EvidenceStore`` takes one. A missing seam fails as a missing seam.
    """
    mandate = Mandate("m-a9", "SKU-1", 100)
    ctor_tenant = "tenant" in inspect.signature(EvidenceStore.__init__).parameters
    call_tenant = "tenant" in inspect.signature(EvidenceStore.record_case).parameters
    read_tenant = "tenant" in inspect.signature(EvidenceStore.packet).parameters
    notes: list[str] = []

    path = root / "evidence.sqlite3"
    writer = EvidenceStore(path, tenant="tenant-a") if ctor_tenant else EvidenceStore(path)
    read_back = None
    write_error = None
    try:
        kwargs = {"tenant": "tenant-a"} if call_tenant else {}
        try:
            returned = writer.record_case(
                case_id="tenant-a-case", grant_id=_approved(writer, "tenant-a-case", mandate),
                asserted_sku="SKU-1", asserted_amount_cents=100, idempotency_key="a9",
                merchant=SyntheticMerchantAdapter(), **kwargs,
            )
            read_back = returned.get("case_id") if isinstance(returned, dict) else None
        except TypeError as exc:
            write_error = f"seam absent: {exc}"
        except Exception as exc:  # noqa: BLE001 - any failure to read back its own write
            write_error = f"{type(exc).__name__}: {exc}"
            notes.append(
                "the write reached the database but the call could not return its own packet"
                if writer.db.execute(
                    "SELECT 1 FROM cases WHERE case_id = 'tenant-a-case'"
                ).fetchone() else "nothing was written"
            )
    finally:
        writer.close()

    # Leg 2 and leg 3: two independent readers on the same file, different tenants.
    def _read(tenant: str | None):
        reader = EvidenceStore(path, tenant=tenant) if (ctor_tenant and tenant) else EvidenceStore(path)
        try:
            if read_tenant and tenant:
                return reader.packet("tenant-a-case", tenant=tenant)["case_id"], None
            return reader.packet("tenant-a-case")["case_id"], None
        except Exception as exc:  # noqa: BLE001 - a refusal is the pass here
            return None, f"{type(exc).__name__}"
        finally:
            reader.close()

    foreign_read, foreign_refusal = _read("tenant-b")
    own_read, own_refusal = _read("tenant-a")
    untenanted_read, _ = _read(None)

    # Leg 4: a per-call tenant that differs from the store's own tenant. This is
    # the leg the signature check could not see. At 83650aa1 record_case wrote the
    # row under the per-call tenant and then returned self.packet(case_id) with no
    # tenant, so the call charged the merchant and raised KeyError on its own
    # return path. Refusing up front is acceptable; charging and then failing is not.
    leg4 = True
    leg4_note = "n/a, record_case takes no tenant argument"
    if call_tenant:
        divergent = EvidenceStore(path)
        merchant = CountingMerchant()
        try:
            divergent.record_case(
                case_id="tenant-c-case", grant_id=_approved(divergent, "tenant-c-case", mandate),
                asserted_sku="SKU-1", asserted_amount_cents=100, idempotency_key="a9-c",
                merchant=merchant, tenant="tenant-c",
            )
            leg4_note = "returned its own packet"
        except Exception as exc:  # noqa: BLE001
            charged = len(merchant.calls)
            leg4 = charged == 0
            leg4_note = (
                f"{type(exc).__name__}: {exc} after {charged} merchant call(s)"
                + ("" if leg4 else " - the side effect happened and the caller got no receipt")
            )
        finally:
            divergent.close()

    leg1 = read_back == "tenant-a-case"
    leg2 = foreign_read is None
    leg3 = own_read == "tenant-a-case"
    holds = leg1 and leg2 and leg3 and leg4
    if untenanted_read is not None and (ctor_tenant or call_tenant or read_tenant):
        notes.append(
            f"a reader naming no tenant still read {untenanted_read}, so the default tenant is a"
            " readable path around the seam"
        )
    return holds, (
        f"seams: ctor_tenant={ctor_tenant} record_case_tenant={call_tenant} packet_tenant={read_tenant}."
        f" leg1 write-then-read-back-as-tenant-a={leg1}"
        f"{' (' + write_error + ')' if write_error else ''};"
        f" leg2 tenant-b reader refused={leg2} ({foreign_refusal or 'READ ' + str(foreign_read)});"
        f" leg3 tenant-a control read={leg3} ({own_refusal or own_read});"
        f" leg4 per-call tenant unlike the store's tenant={leg4} ({leg4_note})."
        + (" " + "; ".join(notes) + "." if notes else "")
        + " Co-tenant readers share one sqlite file;"
        " the reader now carries its own owner and the owner travels with the case"
        " row (BEAAA-35357), so a co-tenant reader on the same file is refused"
        " rather than separated by filesystem permissions alone."
    )


@probe("A10 coverage not inferred from a receipt", "protection state comes only from the activation gate")
def a10(root: Path):
    mandate = Mandate("m-a10", "SKU-1", 100)
    store = _store(root)
    try:
        packet = store.record_case(
            case_id="a10", grant_id=_approved(store, "a10", mandate), asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="a10",
            merchant=SyntheticMerchantAdapter(),
        )
    finally:
        store.close()
    protection = packet["protection"]
    receipt = json.loads(packet["execution"]["receipt_json"])
    holds = protection.get("protection_active") is False and bool(protection.get("statement"))
    return holds, (
        f"MATCH receipt (private={receipt.get('private')}) reports protection={protection}"
    )


@probe("A11 private receipt redaction", "a private receipt carries no shareable approval secret")
def a11(root: Path):
    mandate = Mandate("m-a11", "SKU-1", 100)
    store = _store(root)
    try:
        grant = _approved(store, "a11", mandate)
        token = _stored_token(store, grant)
        packet = store.record_case(
            case_id="a11", grant_id=grant, asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="a11",
            merchant=SyntheticMerchantAdapter(),
        )
        raw = json.dumps(packet, sort_keys=True)
        con = sqlite3.connect(store.path)
        con.row_factory = sqlite3.Row
        row_dump = json.dumps(
            [dict(r) for r in con.execute("SELECT * FROM cases")], sort_keys=True
        )
        con.close()
    finally:
        store.close()
    token_in_packet = token in raw
    token_in_db = token in row_dump
    return not token_in_packet and not token_in_db, (
        f"raw approval token present in packet={token_in_packet}, in cases table={token_in_db};"
        f" stored approval keys={sorted(packet['sources']['approval'])}"
    )


@probe(
    "SA8 forged approval grant",
    "a grant the execution path authored itself must not authorise an execution",
)
def sa8(root: Path):
    """The path that became load-bearing once SA7 rebuilt the verifier internally.

    Filed from `9d1d851e`, where it executed 999,900 cents off a row the
    execution path wrote itself. Re-derived here against the BEAAA-35331 shape,
    and deliberately using an origin FROM the allowlist: the origin check is the
    cheap one, and letting it fire first would hide whether the attestation check
    works at all.
    """
    writer_public = hasattr(EvidenceStore, "record_approval")
    agent_mandate = Mandate("m-sa8", "SKU-1", 100)
    merchant = CountingMerchant()
    store = _store(root)
    try:
        with store.db:
            store.db.execute(
                "INSERT INTO approvals (grant_id, case_id, mandate_json, approval_token, origin)"
                " VALUES (?,?,?,?,?)",
                ("grant-forged", "sa8", json.dumps(asdict(agent_mandate), sort_keys=True),
                 hashlib.sha256(b"agent-chose-this").hexdigest(), APPROVAL_ORIGINS[0]),
            )
        try:
            packet = store.record_case(
                case_id="sa8", grant_id="grant-forged", asserted_sku="SKU-1",
                asserted_amount_cents=999_900, idempotency_key="sa8", merchant=merchant,
            )
            executed = f"status={packet['status']}"
        except PermissionError as exc:
            executed = f"refused: {exc}"
    finally:
        store.close()
    return not merchant.calls, (
        f"public record_approval still on EvidenceStore={writer_public};"
        f" raw-SQL grant at origin={APPROVAL_ORIGINS[0]!r} -> record_case {executed};"
        f" merchant amounts={[c['amount_cents'] for c in merchant.calls]}."
        f" Closed by BEAAA-35331: the row is no longer the authority, the process-level"
        f" issuer is, and its digest over (secret, grant_id, mandate) is not forgeable"
    )


@probe(
    "SA9 grant un-consume replay",
    "a spent approval stays spent even against a caller that can write the DB",
)
def sa9(root: Path):
    """Whether single use survives a caller that can rewrite the flag.

    ``record_case`` marks ``approvals.consumed = 1`` in the same database the
    execution path writes its own cases to. SA8 is closed because forging a
    token needs the process secret; resetting a flag needs no secret at all, so
    this probe resets it and executes again. The reading is in the output, not
    in this docstring.
    """
    mandate = Mandate("m-sa9", "SKU-1", 100)
    merchant = CountingMerchant()
    store = _store(root)
    try:
        grant = _approved(store, "sa9", mandate)
        first = store.record_case(
            case_id="sa9", grant_id=grant, asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="sa9-1", merchant=merchant,
        )
        with store.db:
            store.db.execute("UPDATE approvals SET consumed = 0 WHERE grant_id = ?", (grant,))
        try:
            again = store.record_case(
                case_id="sa9", grant_id=grant, asserted_sku="SKU-1",
                asserted_amount_cents=100, idempotency_key="sa9-2", merchant=merchant,
            )
            replayed = f"status={again['status']}"
        except PermissionError as exc:
            replayed = f"refused: {exc}"
    finally:
        store.close()
    return len(merchant.calls) <= 1, (
        f"first execution status={first['status']};"
        f" after UPDATE approvals SET consumed=0 -> {replayed};"
        f" merchant calls={len(merchant.calls)} amounts={[c['amount_cents'] for c in merchant.calls]}."
        f" Closed by BEAAA-35354: the process-level authority spends the grant in its own"
        f" memory as well as in the row, so rewriting the flag is no longer sufficient."
        f" The DB flag still carries single use across a restart, and the issuer's memory"
        f" carries it against a caller that can write the DB; neither alone is enough"
    )


@probe(
    "SA10 signing capability isolation",
    "the approval authority cannot be read or substituted from the execution path",
)
def sa10(root: Path):
    """What an in-process demo can and cannot prove.

    The key is not DERIVABLE: random per process, no literal anywhere, bound to
    the grant id. It is still READABLE, because the human-side module and the
    execution path are the same Python process. Recorded so the seam is not
    described as isolation it does not yet have.
    """
    issuer = approval_mod._process_issuer()
    secret = getattr(issuer, "_secret", None)
    mandate = Mandate("m-sa10", "SKU-1", 100)
    keep = approval_mod._PROCESS_ISSUER
    substituted = False
    try:
        approval_mod._PROCESS_ISSUER = HumanApprovalIssuer("agent-substituted-authority")
        store = _store(root)
        merchant = CountingMerchant()
        try:
            grant = approve_mandate(
                store, case_id="sa10", mandate=mandate,
                issuer=HumanApprovalIssuer("agent-substituted-authority"),
            )
            store.record_case(
                case_id="sa10", grant_id=grant, asserted_sku="SKU-1",
                asserted_amount_cents=100, idempotency_key="sa10", merchant=merchant,
            )
            substituted = len(merchant.calls) == 1
        except Exception:
            substituted = False
        finally:
            store.close()
    finally:
        approval_mod._PROCESS_ISSUER = keep
    return not substituted and secret is None, (
        f"_process_issuer()._secret readable in-process={secret is not None}"
        f" (len={len(secret or '')}); rebinding approval._PROCESS_ISSUER to an agent-chosen"
        f" authority and approving through it executed={substituted}."
        f" This is the boundary limit of the demo, not a regression in BEAAA-35331:"
        f" Python offers no in-process capability boundary, so the approval authority is"
        f" only enforceable once the human side sits behind a process boundary"
    )


@probe(
    "A12 receipt protection is bound to execution time",
    "a receipt cannot start claiming coverage that did not exist when it executed",
)
def a12(root: Path):
    """The read-time protection binding, measured rather than reasoned about.

    ``EvidenceStore.packet`` computes the protection block when the packet is
    READ, from whatever the activation registry says at that moment, and the
    ``cases`` table stores no activation record, terms version or program
    version. So a case executed with no coverage in existence reads back as
    covered once records appear later. BEAAA-35133 defect A, re-measured here at
    a SHA four commits past the one it was filed against.

    Not a re-grade of the activation gate: ``check_activation`` is asked for its
    verdict and believed. What is under test is whether the receipt is bound to
    the verdict that was true when the merchant was called.
    """
    records_dir = root / "activation-records"
    keep = os.environ.get(act.ACTIVATION_RECORDS_ENV)
    os.environ[act.ACTIVATION_RECORDS_ENV] = str(records_dir)  # absent -> not active
    try:
        mandate = Mandate("m-a12", "SKU-1", 100)
        store = _store(root)
        try:
            at_execution = store.record_case(
                case_id="a12", grant_id=_approved(store, "a12", mandate),
                asserted_sku="SKU-1", asserted_amount_cents=100, idempotency_key="a12",
                merchant=SyntheticMerchantAdapter(),
            )["protection"]
        finally:
            store.close()

        # Coverage is activated afterwards. The recorded case is never touched.
        program_version = act.default_program_version()
        records = json.loads(json.dumps(_ACTIVATION_RECORDS))
        for record in records.values():
            record["program_version"] = program_version
        records_dir.mkdir(parents=True, exist_ok=True)
        (records_dir / f"{program_version}.json").write_text(json.dumps(records))
        gate_now = act.check_activation(program_version, act.FileRegistry(str(records_dir))).active

        store = EvidenceStore(root / "evidence.sqlite3")
        try:
            on_read = store.packet("a12")["protection"]
            columns = [
                d[0] for d in store.db.execute("SELECT * FROM cases LIMIT 0").description
            ]
            # Positive control. Without it, a packet that simply always reported
            # "no coverage" would pass this probe while carrying no binding at
            # all, which is the false pass this harness exists to catch.
            live = store.record_case(
                case_id="a12-live", grant_id=_approved(store, "a12-live", Mandate("m-a12b", "SKU-1", 100)),
                asserted_sku="SKU-1", asserted_amount_cents=100, idempotency_key="a12-live",
                merchant=SyntheticMerchantAdapter(),
            )["protection"]
        finally:
            store.close()
    finally:
        if keep is None:
            os.environ.pop(act.ACTIVATION_RECORDS_ENV, None)
        else:
            os.environ[act.ACTIVATION_RECORDS_ENV] = keep

    retro = at_execution["protection_active"] is False and on_read["protection_active"] is True
    control = live["protection_active"] is True and bool(live["activation_record_id"])
    bound = [c for c in columns if "protection" in c or "activation" in c or "terms" in c]
    return (not retro) and control, (
        f"at execution protection_active={at_execution['protection_active']};"
        f" activation gate after records appear={gate_now};"
        f" same case re-read protection_active={on_read['protection_active']}"
        f" record={on_read['activation_record_id']} terms={on_read['terms_version']};"
        f" retroactive_claim={retro}."
        f" Positive control, a case executed WHILE coverage is live:"
        f" protection_active={live['protection_active']}"
        f" record={live['activation_record_id']} terms={live['terms_version']}"
        f" -> control_holds={control}, so a packet that simply always said"
        f" 'no coverage' could not pass this probe."
        f" Binding columns on cases: {bound or 'none'}."
        f" Fixture note: every record is retargeted at {program_version!r}; keeping the"
        f" test module's own program version makes the gate refuse on version mismatch"
        f" and the probe report a false negative"
    )


# --------------------------------------------------------------------------
# Path 1: the approval-receipt flow (Founder ruling section 2, BEAAA-34389).
#
# Path 1 holds no capability and calls no adapter, so nothing here can refuse an
# action. What it can get wrong is money: whether a claim reads as a covered
# loss. Every field these probes feed is supplied by the caller - the party with
# the incentive - so each one asks whether an unverified assertion can move the
# coverage verdict. A1 and SA8 settled that question for approvals; these ask it
# of outcomes.
# --------------------------------------------------------------------------

_P1_OBLIGATION = path1_mod.CANCELLATION_OBLIGATION


def _p1_instant(stamp: str) -> datetime | None:
    """The real instant a stamp denotes, or None if it cannot be ordered.

    A stamp with no zone is not orderable against a Zulu deadline: it denotes a
    different instant in every offset. Neither is one that does not parse.
    """
    try:
        parsed = datetime.fromisoformat(stamp.replace("z", "Z").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _p1_review(*, answer: str = ANSWER_OK, performed: bool = False,
               observed_at: str = "", evidence_ref: str = "no-cancellation-in-inbox",
               action=None) -> dict:
    action = action or _P1_OBLIGATION
    receipt = ApprovalReceipt(
        receipt_id="receipt-p1", task_id="p1", proposed_action=action, answer=answer,
        checked_at="2026-10-10T09:00:00Z",
        answered_at=None if answer == ANSWER_SKIPPED else "2026-10-10T09:05:00Z",
        recorded_at="2026-10-10T09:05:05Z", limit_cents=path1_mod.DEMO_LIMIT_CENTS,
        warnings=assess_warnings(action, path1_mod.DEMO_LIMIT_CENTS),
    )
    outcome = ExecutedOutcome(
        action.description, action.merchant, 0, evidence_ref=evidence_ref,
        performed=performed, observed_at=observed_at,
    )
    return review_claim(receipt, outcome)


@probe("P1-1 cancellation deadline is compared as time",
       "whether the money is still recoverable must follow the instant, not the spelling")
def p1_1(root: Path):
    """``observed_at > cancellation_deadline`` is a string compare (receipt.py).

    That is only correct for Zulu-suffixed stamps of equal length. ISO-8601
    permits a numeric offset and a lowercase zone designator, and ``observed_at``
    is caller-supplied, so the spelling decides the claim instead of the instant.
    """
    deadline = _P1_OBLIGATION.cancellation_deadline
    deadline_at = _p1_instant(deadline)
    # The same question either side of the deadline, spelled five legal ways.
    stamps = [
        "2026-10-11T12:30:00Z",        # plainly past it
        "2026-10-11T09:00:00-08:00",   # 17:00Z, five hours past it
        "2026-10-11T13:00:00+14:00",   # 2026-10-10T23:00Z, thirteen hours short of it
        "2026-10-11T12:00:00z",        # the deadline itself, lowercase zone
        "2026-10-14T00:00:00",         # no zone at all: not orderable
    ]
    wrong, checked = [], []
    for stamp in stamps:
        verdict = _p1_review(observed_at=stamp)
        instant = _p1_instant(stamp)
        if instant is None:
            # Unorderable input must not decide money in either direction.
            expected = "UNKNOWN/INCOMPLETE"
            ok = verdict["outcome_state"] == expected
        else:
            expected = instant > deadline_at
            ok = verdict["covered"] is expected
        checked.append(f"{stamp} -> covered={verdict['covered']} expected={expected}")
        if not ok:
            wrong.append(stamp)
    return not wrong, (
        f"deadline {deadline}; " + "; ".join(checked)
        + (f". Wrong on {len(wrong)}/{len(stamps)}: {wrong}. An offset-bearing stamp reads a real"
           " loss as still inside the window, and one inside the window as a missed deadline, so"
           " the spelling of a caller-supplied timestamp decides coverage."
           if wrong else ". Every spelling agreed with the instant it denotes.")
    )


@probe("P1-2 an omission cannot be cleared by the acting party",
       "a self-reported 'I did it' with no independent evidence must not erase a loss")
def p1_2(root: Path):
    """``performed`` is a caller-supplied bool and ``evidence_ref`` a free string.

    Setting ``performed=True`` skips the omission branch entirely; what is left
    compares description, merchant and amount, and a cancellation obligation is
    amount 0, so the caller's own echo of the approved action is a MATCH.
    """
    verdict = _p1_review(observed_at="2026-10-11T12:30:00Z", performed=True,
                         evidence_ref="agent-says-done")
    honest = _p1_review(observed_at="2026-10-11T12:30:00Z", performed=False)
    # Either the loss stands, or the claim reads incomplete for want of evidence
    # that the cancellation happened. What it must not do is settle as "the user
    # approved this" on the acting party's word.
    holds = bool(verdict["covered"]) or verdict["outcome_state"] == "UNKNOWN/INCOMPLETE"
    return holds, (
        f"same missed deadline, performed=True with a fabricated evidence_ref ->"
        f" covered={verdict['covered']} claim={verdict['claim']}"
        f" state={verdict['outcome_state']}; performed=False -> covered={honest['covered']}"
        f" claim={honest['claim']}."
        + ("" if holds else " The party that failed to act sets the flag that decides whether it"
           " failed to act, and no evidence is required to back the positive claim - the A1/SA8"
           " self-assertion defect, on the outcome side.")
    )


@probe("P1-3 coverage needs money that cannot be got back",
       "an omission still inside the cancellation window is not yet a loss, approved or not")
def p1_3(root: Path):
    """The SKIPPED/UNANSWERED branch returns before the deadline is consulted.

    Section 2 of the ruling defines the covered event as money the user cannot
    get back. A missing approval is a serious record defect, but on its own it
    does not make a refundable deposit unrecoverable.
    """
    inside = _p1_review(answer=ANSWER_SKIPPED, observed_at="2026-10-10T18:00:00Z")
    past = _p1_review(answer=ANSWER_SKIPPED, observed_at="2026-10-11T12:30:00Z")
    holds = not inside["covered"] and bool(past["covered"])
    return holds, (
        f"unanswered check, omission still inside the window -> covered={inside['covered']}"
        f" claim={inside['claim']}; the same omission past the deadline ->"
        f" covered={past['covered']} claim={past['claim']}."
        + ("" if holds else " A deposit that is still fully refundable reads as a covered loss, so"
           " coverage attaches to the missing approval rather than to unrecoverable money. The"
           " past-deadline control on the right is what stops this row passing by always-covered.")
    )


@probe("P1-4 a missing observation fails closed",
       "no timestamp is not evidence that the money is still recoverable")
def p1_4(root: Path):
    """``observed_at`` defaults to the empty string, which is falsy.

    The deadline test collapses to False and the claim settles as "not yet a
    loss" - a decided verdict reached from no observation at all. A8 already
    requires the opposite on the capability path: absent evidence reads
    UNKNOWN/INCOMPLETE, never as a settled state.
    """
    verdict = _p1_review(observed_at="")
    holds = verdict["outcome_state"] == "UNKNOWN/INCOMPLETE"
    return holds, (
        f"observed_at='' -> state={verdict['outcome_state']} claim={verdict['claim']}"
        f" covered={verdict['covered']}."
        + ("" if holds else " Nothing was observed and the claim still settled on a side. Same"
           " requirement as A8, other path.")
    )


PROBES = [a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, a12, sa8, sa9, sa10, p1_1, p1_2, p1_3, p1_4]


def main() -> int:
    for p in PROBES:
        p()
    held = [r for r in RESULTS if r["holds"]]
    print("Stage A abuse-case harness (report-only)")
    print(f"probes={len(RESULTS)} requirement_holds={len(held)} requirement_fails={len(RESULTS) - len(held)}")
    print("-" * 78)
    for r in RESULTS:
        print(f"[{'HOLDS' if r['holds'] else 'FAILS'}] {r['case']}")
        print(f"    requirement: {r['requirement']}")
        print(f"    observed:    {r['observed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
