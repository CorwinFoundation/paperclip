"""NOTE (BEAAA-35331): this report-only harness is written against the
pre-BEAAA-35331 ``record_case(approval_token=..., approval_verifier=...)``
signature and no longer runs end to end. The abuse cases it described are
now asserted as capability tests in
``tests/test_beaaa34389_defect1_running_app.py``. Left in place as the
record of what was measured, not as a runnable instrument.

Do not point QA at ``qa_probe_9d1d851e.py``: it is written against the old
``issuer.approve() -> (token, verifier)`` signature and stops at line 44 with
``ValueError: too many values to unpack``. ``qa_probe_beaaa35331.py`` at the
repo root is the implementer's convenience port of the same SA1-SA8 cases to
the ``grant_id`` boundary; it is not independent evidence. Rebuilding this
harness against the new boundary is QA's, per the BEAAA-35331 ruling of
2026-09-24T12:27Z.
"""

"""Independent abuse-case harness for the Stage A purchase-protection seams (BEAAA-34396).

Report-only. Every probe drives the real stage_a_demo code from the outside and
prints what the code actually does against what the Stage A acceptance scope
requires. Nothing here asserts; the harness always exits 0 so it can run as a
scan next to the suite without gating a branch.

Run:  python tools/stage_a_abuse_harness.py
"""

from __future__ import annotations

import inspect
import json
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stage_a_demo.core import (  # noqa: E402
    ApprovalVerifier,
    EvidenceStore,
    HumanApprovalIssuer,
    Mandate,
    SyntheticMerchantAdapter,
)

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


_HUMAN_APPROVAL_SECRET = "abuse-harness-human-side-secret"


def _human() -> HumanApprovalIssuer:
    """The human side of the seam, with an explicit key. There is no default."""
    return HumanApprovalIssuer(_HUMAN_APPROVAL_SECRET)


def _store(root: Path) -> EvidenceStore:
    return EvidenceStore(root / "evidence.sqlite3")


@probe("A1 agent-self-approval", "an execution-side caller cannot mint a token the verifier accepts")
def a1(root: Path):
    mandate = Mandate("m-a1", "SKU-1", 100)
    # The human approves once. The execution path receives only this verifier.
    human_token, human_verifier = _human().approve(mandate)

    # The agent never receives the human's issuer. It cannot build a default one
    # any more, so it has to choose its own key material.
    try:
        HumanApprovalIssuer()
        default_construction = "constructed with no key"
    except TypeError:
        default_construction = "refused: no default key"
    forged = HumanApprovalIssuer("agent-chosen-secret").approve(mandate)[0]
    accepted_by_human_verifier = human_verifier.verify(mandate, forged)

    store = _store(root)
    try:
        packet = store.record_case(
            case_id="a1", mandate=mandate, approval_token=forged, asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="a1", approval_verifier=human_verifier,
            merchant=SyntheticMerchantAdapter(),
        )
        executed = packet["status"]
    except PermissionError as exc:
        executed = f"refused: {exc}"
    finally:
        store.close()
    holds = (
        default_construction.startswith("refused")
        and not accepted_by_human_verifier
        and str(executed).startswith("refused")
    )
    return holds, (
        f"HumanApprovalIssuer() with no key -> {default_construction};"
        f" agent-minted token accepted by the human's verifier={accepted_by_human_verifier};"
        f" record_case -> {executed}"
        f" (the verifier holds sha256 of the human's token, never the key, so holding it"
        f" confers no ability to mint)"
    )


@probe("A2 mandate replay", "one human approval authorises at most one execution")
def a2(root: Path):
    mandate = Mandate("m-a2", "SKU-1", 100)
    token, verifier = _human().approve(mandate)
    merchant = CountingMerchant()
    store = _store(root)
    try:
        for n in range(5):  # same approved mandate, fresh idempotency key each time
            store.record_case(
                case_id=f"a2-{n}", mandate=mandate, approval_token=token, asserted_sku="SKU-1",
                asserted_amount_cents=100, idempotency_key=f"a2-{n}",
                approval_verifier=verifier, merchant=merchant,
            )
    finally:
        store.close()
    return len(merchant.calls) <= 1, (
        f"one approval of mandate m-a2 drove {len(merchant.calls)} merchant executions"
        f" ({sum(c['amount_cents'] for c in merchant.calls)} cents total against a 100 cent"
        f" mandate); the token carries no nonce, expiry or single-use record"
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
    token, verifier = _human().approve(mandate)
    merchant = CountingMerchant()
    store = _store(root)
    try:
        packet = store.record_case(
            case_id="a4", mandate=mandate, approval_token=token, asserted_sku="SKU-PREMIUM",
            asserted_amount_cents=999_900, idempotency_key="a4",
            approval_verifier=verifier, merchant=merchant,
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
        t1, v1 = _human().approve(first)
        before = store.record_case(
            case_id="a5", mandate=first, approval_token=t1, asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="a5-one",
            approval_verifier=v1, merchant=SyntheticMerchantAdapter(),
        )
        # Same case_id, different mandate, fresh idempotency key.
        second = Mandate("m-a5-rewritten", "SKU-COVER-UP", 1)
        t2, v2 = _human().approve(second)
        store.record_case(
            case_id="a5", mandate=second, approval_token=t2, asserted_sku="SKU-COVER-UP",
            asserted_amount_cents=1, idempotency_key="a5-two",
            approval_verifier=v2, merchant=SyntheticMerchantAdapter(),
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
    token, verifier = _human().approve(mandate)
    merchant = CountingMerchant()
    store = _store(root)
    kwargs = dict(
        case_id="a6", mandate=mandate, approval_token=token, asserted_sku="SKU-1",
        asserted_amount_cents=100, idempotency_key="a6", approval_verifier=verifier,
        merchant=merchant,
    )
    try:
        for _ in range(4):
            store.record_case(**kwargs)
        count = store.execution_count("a6")
    finally:
        store.close()
    return count == 1 and len(merchant.calls) == 1, (
        f"4 retries -> execution_count={count}, merchant calls={len(merchant.calls)}"
    )


@probe("A7 concurrent reservations", "racing callers on one key reserve the limit once")
def a7(root: Path):
    mandate = Mandate("m-a7", "SKU-1", 100)
    token, verifier = _human().approve(mandate)
    merchant = CountingMerchant()
    path = root / "evidence.sqlite3"
    EvidenceStore(path).close()  # create schema once, then race separate connections
    errors: list[str] = []
    barrier = threading.Barrier(6)

    def worker(n: int) -> None:
        store = EvidenceStore(path)
        barrier.wait()
        try:
            store.record_case(
                case_id="a7", mandate=mandate, approval_token=token, asserted_sku="SKU-1",
                asserted_amount_cents=100, idempotency_key="a7", approval_verifier=verifier,
                merchant=merchant,
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
    return count == 1 and len(merchant.calls) <= 1 and not errors, (
        f"6 concurrent callers on key a7 -> execution rows={count},"
        f" merchant calls={len(merchant.calls)}, raised={sorted(set(errors)) or 'none'}"
    )


@probe("A8 missing evidence fails closed", "no independent evidence must never read as verified")
def a8(root: Path):
    mandate = Mandate("m-a8", "SKU-1", 100)
    token, verifier = _human().approve(mandate)
    store = _store(root)
    try:
        packet = store.record_case(
            case_id="a8", mandate=mandate, approval_token=token, asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="a8", approval_verifier=verifier,
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
    token, verifier = _human().approve(mandate)
    store = _store(root)
    try:
        store.record_case(
            case_id="tenant-a-case", mandate=mandate, approval_token=token, asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="a9", approval_verifier=verifier,
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
    token, verifier = _human().approve(mandate)
    store = _store(root)
    try:
        packet = store.record_case(
            case_id="a10", mandate=mandate, approval_token=token, asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="a10", approval_verifier=verifier,
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
    token, verifier = _human().approve(mandate)
    store = _store(root)
    try:
        packet = store.record_case(
            case_id="a11", mandate=mandate, approval_token=token, asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="a11", approval_verifier=verifier,
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
    """The hole that becomes load-bearing once SA7 rebuilds the verifier internally.

    BEAAA-35331 removes ``approval_verifier`` from ``record_case`` and rebuilds it
    inside from the consumed ``approvals`` row. That relocates the circularity
    rather than breaking it: ``ApprovalVerifier.for_recorded_approval`` derives
    the verifier from the stored token, so the row becomes the authority. This
    probe drives the shape the execution path already has — ``ui.execute_case``
    opens its own ``EvidenceStore`` (ui.py), and that class exposes
    ``record_approval`` — and mints its own grant with no issuer and no secret.
    """
    writer_reachable = hasattr(EvidenceStore, "record_approval")
    agent_mandate = Mandate("m-sa8", "SKU-1", 100)
    merchant = CountingMerchant()
    store = _store(root)
    try:
        # No issuer is constructed anywhere in this probe. The token is a literal
        # the agent chose, and ``origin`` is copied from ui.APPROVAL_ORIGINS.
        store.record_approval(
            grant_id="grant-forged", case_id="sa8", mandate=agent_mandate,
            approval_token="agent-chose-this", origin="approval-step",
        )
        row = store.consume_approval("grant-forged", "sa8")
        consumed = row is not None
        # Exactly how the prescribed shape will rebuild it, but inline so the
        # probe runs against today's signature too.
        rebuilt = ApprovalVerifier.for_recorded_approval(row["mandate"], row["approval_token"])
        try:
            packet = store.record_case(
                case_id="sa8", mandate=row["mandate"], approval_token=row["approval_token"],
                asserted_sku="SKU-1", asserted_amount_cents=999900, idempotency_key="sa8",
                approval_verifier=rebuilt, merchant=merchant,
            )
            executed = packet["status"]
        except PermissionError as exc:
            executed = f"refused: {exc}"
    finally:
        store.close()
    reached_merchant = [c["amount_cents"] for c in merchant.calls]
    return not reached_merchant, (
        f"EvidenceStore exposes record_approval to the handle the execution path opens"
        f"={writer_reachable}; self-authored grant consumed={consumed};"
        f" origin 'approval-step' accepted with no issuer; record_case -> {executed};"
        f" merchant amounts={reached_merchant} against a {agent_mandate.amount_cents} cent"
        f" self-authored mandate (the verifier rebuilt from the stored row accepts the"
        f" token that row was written with, so a DB write is an approval)"
    )


PROBES = [a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, sa8]


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
