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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stage_a_demo import activation as act  # noqa: E402
from stage_a_demo import approval as approval_mod  # noqa: E402
from stage_a_demo.approval import approve_mandate  # noqa: E402
from stage_a_demo.core import (  # noqa: E402
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
    surfaces = {
        name
        for obj in (HumanApprovalIssuer, ApprovalVerifier, EvidenceStore)
        for name, _ in inspect.getmembers(obj, callable)
        if not name.startswith("_")
    }
    revocation = {s for s in surfaces if "revoke" in s or "revocation" in s or "expire" in s}
    fields = set(inspect.signature(EvidenceStore.record_case).parameters)
    return bool(revocation), (
        f"no revocation surface on the Stage A seams (callables: {sorted(surfaces)});"
        f" record_case takes no expiry/nonce parameter (params: {sorted(fields - {'self'})})"
    )


@probe("A4 over-mandate execution", "an assertion outside the mandate must not reach the merchant")
def a4(root: Path):
    mandate = Mandate("m-a4", "SKU-1", 100)
    merchant = CountingMerchant()
    store = _store(root)
    try:
        grant = _approved(store, "a4", mandate)
        packet = store.record_case(
            case_id="a4", grant_id=grant, asserted_sku="SKU-PREMIUM",
            asserted_amount_cents=999_900, idempotency_key="a4", merchant=merchant,
        )
    finally:
        store.close()
    charged = merchant.calls[0]["amount_cents"] if merchant.calls else None
    return not merchant.calls, (
        f"agent asserted 999900 cents against a 100 cent mandate; merchant was called"
        f" {len(merchant.calls)} time(s) at amount_cents={charged}; packet status="
        f"{packet['status']} with {len(packet['reviewable_mismatches'])} reviewable mismatch(es)."
        f" The mandate is reconciled after the side effect, never enforced before it."
    )


@probe("A5 evidence tampering", "a recorded case is append-only; its sources cannot be rewritten")
def a5(root: Path):
    store = _store(root)
    try:
        first = Mandate("m-a5-real", "SKU-1", 100)
        before = store.record_case(
            case_id="a5", grant_id=_approved(store, "a5", first), asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="a5-one",
            merchant=SyntheticMerchantAdapter(),
        )
        # Same case_id, different mandate, its own human approval, fresh key.
        second = Mandate("m-a5-rewritten", "SKU-COVER-UP", 1)
        store.record_case(
            case_id="a5", grant_id=_approved(store, "a5", second), asserted_sku="SKU-COVER-UP",
            asserted_amount_cents=1, idempotency_key="a5-two",
            merchant=SyntheticMerchantAdapter(),
        )
        after = store.packet("a5")
    finally:
        store.close()
    rewritten = before["sources"]["mandate"] != after["sources"]["mandate"]
    stale_exec = after["execution"]["idempotency_key"] == "a5-one"
    return not rewritten, (
        f"case a5 mandate went {before['sources']['mandate']['mandate_id']} ->"
        f" {after['sources']['mandate']['mandate_id']} (rewritten={rewritten}) via"
        f" INSERT OR REPLACE; packet still reports execution"
        f" {after['execution']['idempotency_key']} (stale={stale_exec}), so the packet pairs"
        f" one case's evidence with another case's receipt"
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
    return count == 1 and len(merchant.calls) == 1, (
        f"4 retries of one idempotency key -> execution_count={count},"
        f" merchant calls={len(merchant.calls)}, outcomes={retries}."
        f" Exactly-once holds at the merchant, but the retry no longer returns the"
        f" idempotent packet: record_case consumes the grant before it looks at the"
        f" idempotency key, so a client retrying a timed-out request gets a"
        f" PermissionError rather than its receipt"
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
    params = set(inspect.signature(EvidenceStore.record_case).parameters) | set(
        inspect.signature(EvidenceStore.packet).parameters
    )
    tenant_aware = {p for p in params if "tenant" in p or "account" in p or "owner" in p}
    mandate = Mandate("m-a9", "SKU-1", 100)
    store = _store(root)
    try:
        store.record_case(
            case_id="tenant-a-case", grant_id=_approved(store, "tenant-a-case", mandate),
            asserted_sku="SKU-1", asserted_amount_cents=100, idempotency_key="a9",
            merchant=SyntheticMerchantAdapter(),
        )
        # A second reader with no relationship to the writer reads the same file.
        other = EvidenceStore(store.path)
        try:
            leaked = other.packet("tenant-a-case")["case_id"]
        finally:
            other.close()
    finally:
        store.close()
    return bool(tenant_aware), (
        f"no tenant/account/owner parameter on the evidence seams (params:"
        f" {sorted(params - {'self'})}); any process able to open the sqlite file read"
        f" case {leaked} in full. Isolation is filesystem permissions only."
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
    """Single use now rests on one mutable integer.

    ``record_case`` consumes the grant by setting ``approvals.consumed = 1``, in
    the same database the execution path writes its own cases to. SA8 is closed
    because forging a token needs the process secret. Resetting a flag needs no
    secret at all.
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
        f" The token stays valid forever and single use is enforced by a flag the"
        f" execution path can rewrite through the handle it already holds"
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
        finally:
            store.close()
    finally:
        if keep is None:
            os.environ.pop(act.ACTIVATION_RECORDS_ENV, None)
        else:
            os.environ[act.ACTIVATION_RECORDS_ENV] = keep

    retro = at_execution["protection_active"] is False and on_read["protection_active"] is True
    bound_columns = [c for c in columns if "activation" in c or "terms" in c or "program" in c]
    return not retro, (
        f"at execution protection_active={at_execution['protection_active']};"
        f" activation gate after records appear={gate_now};"
        f" same case re-read protection_active={on_read['protection_active']}"
        f" record={on_read['activation_record_id']} terms={on_read['terms_version']};"
        f" retroactive_claim={retro}. The cases table carries no binding column"
        f" (activation/terms/program: {bound_columns or 'none'}), so protection is"
        f" recomputed at read time from whatever the registry says then."
        f" The fixture retargets every record at {program_version!r}: keeping the test"
        f" module's own program version makes the gate refuse on version and the probe"
        f" report a false negative"
    )


PROBES = [a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, a12, sa8, sa9, sa10]


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
