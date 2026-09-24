from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import MODE
from .receipt import (
    ApprovalReceipt,
    CheckNotApproved,
    ExecutedOutcome,
    ProposedAction,
    TermsNotCaptured,
    observation_instant,
    review_claim,
)
from .activation import (
    default_program_version,
    default_registry,
    surface_protection_state,
    unrecorded_protection_state,
)


@dataclass(frozen=True)
class Mandate:
    mandate_id: str
    sku: str
    amount_cents: int


APPROVAL_ORIGINS = ("approval-step", "fixture-explorer")

# Lifecycle of one approval grant. A grant leaves ``UNSPENT`` exactly once, and
# only the transition to ``CONSUMED`` authorises a purchase (BEAAA-35133 B).
APPROVAL_UNSPENT = 0
APPROVAL_CONSUMED = 1
APPROVAL_REVOKED = 2

_VERIFIER_CAPABILITY = object()


class MandateExceeded(PermissionError):
    """An assertion outside the mandate reached the capability-backed path.

    Raised before the grant is consumed and before the merchant adapter is
    called, so an over-mandate attempt spends nothing and records nothing
    (BEAAA-34396 A4).

    This refusal belongs to the path where BackBond *holds the capability*. It
    is not the Path 1 posture: there BackBond only observes, holds no
    capability and calls no adapter, so "over your limit" is a warning written
    on the approval receipt and never a veto. The two postures are deliberately
    separate and separately tested — see
    ``tests/test_beaaa34389_two_enforcement_postures.py``. Trading this refusal
    down into a warning would fail A4 whatever it cites.
    """


def _mandate_overreach(
    mandate: Mandate, asserted_sku: str, asserted_amount_cents: int
) -> list[dict[str, Any]]:
    """What about this assertion falls outside the approved mandate.

    A different SKU is outside it. So is a larger amount. A *smaller* amount is
    within what the human approved — the agent spent less than it was allowed
    to — so it is recorded as a reviewable difference rather than refused.
    """
    outside: list[dict[str, Any]] = []
    if asserted_sku != mandate.sku:
        outside.append({"field": "sku", "mandate": mandate.sku, "assertion": asserted_sku})
    if asserted_amount_cents > mandate.amount_cents:
        outside.append(
            {"field": "amount_cents", "mandate": mandate.amount_cents, "assertion": asserted_amount_cents}
        )
    return outside


class ApprovalVerifier:
    """Execution-side capability. Holds only one-way digests of human approvals.

    BEAAA-35331: a caller can no longer construct one, hand one in, or obtain
    one from the issuer. The trusted verifier is rebuilt inside
    :meth:`EvidenceStore.record_case` from the approval row the human-side step
    recorded, so no verifier and no token crosses the call boundary into the
    execution path. Its state is ``sha256(token)`` per approved mandate, which
    is not invertible. BEAAA-34389 defect 1 remainder.
    """

    def __init__(self, grants: dict[str, str], *, capability: object | None = None) -> None:
        if capability is not _VERIFIER_CAPABILITY:
            raise TypeError(
                "ApprovalVerifier cannot be constructed by a caller; the trusted verifier is "
                "rebuilt internally from the recorded approval"
            )
        if not isinstance(grants, dict):
            raise TypeError("ApprovalVerifier requires an explicit grant mapping")
        self._grants = dict(grants)

    def verify(self, mandate: Mandate, token: str) -> bool:
        expected = self._grants.get(_mandate_fingerprint(mandate))
        if expected is None:
            return False
        return secrets.compare_digest(expected, hashlib.sha256(token.encode()).hexdigest())


def _verifier_for_recorded_approval(mandate: Mandate, approval_token: str) -> ApprovalVerifier:
    """Rebuild the verifying half from an approval this server already recorded.

    Module-private on purpose: the public classmethod it replaces let a caller
    build a verifier around a token of its own choosing (BEAAA-35331 SA5).

    The underscore is a convention, not a boundary (BEAAA-35354 SA10). A caller
    in this process can import this and build a verifier around any token it
    likes. That is harmless because no route accepts a verifier from a caller:
    ``record_case`` rebuilds its own from the approval row it just consumed, so
    a verifier built outside has nothing to be handed to.
    """
    return ApprovalVerifier(
        {_mandate_fingerprint(mandate): hashlib.sha256(approval_token.encode()).hexdigest()},
        capability=_VERIFIER_CAPABILITY,
    )


class HumanApprovalIssuer:
    """Human-side minting capability. Start-up wiring only.

    The secret is injected from outside the execution path. There is no literal
    default: constructing this without a secret is an error, not a fallback.
    ``stage_a_demo.approval`` is the only supported construction site.
    """

    def __init__(self, secret: str) -> None:
        if not isinstance(secret, str) or not secret:
            raise ValueError(
                "HumanApprovalIssuer requires a non-empty injected approval secret; there is no default"
            )
        self._secret = secret
        # Which grants this authority has already spent. Lives with the
        # minting half, so the execution path holds no handle to it
        # (BEAAA-35354 SA9).
        self._spent: set[str] = set()

    def approve(self, mandate: Mandate, grant_id: str) -> str:
        """Mint one approval token, bound to the grant that will carry it.

        Binding to ``grant_id`` is what stops a genuine token being replayed
        under a second approvals row written straight to the database
        (BEAAA-35331 SA8b): the digest of a grant id the issuer never saw
        cannot be produced without the secret. Returns no verifier; the
        execution path rebuilds that itself from the recorded approval.
        """
        return _approval_digest(self._secret, mandate, grant_id)

    def attests(self, mandate: Mandate, grant_id: str, approval_token: str) -> bool:
        """True when this issuer, and not some other, minted that token for that grant."""
        return secrets.compare_digest(_approval_digest(self._secret, mandate, grant_id), approval_token)

    def spend(self, mandate: Mandate, grant_id: str, approval_token: str) -> bool:
        """Spend a grant against this authority's own ledger. Single use.

        BEAAA-35354 SA9: before this, single use rested on the ``consumed``
        column of the approvals row, which the execution path can write through
        the handle it already has. One ``UPDATE approvals SET consumed = 0``
        revived a genuine grant for unlimited reuse. The authoritative
        spent-grant record now sits here, beside the secret, on the human side
        of the boundary the execution path cannot reach, so reviving the row
        does not revive the grant.

        False when the token is not this authority's, or when this authority
        has already spent that grant.
        """
        if not self.attests(mandate, grant_id, approval_token):
            return False
        if grant_id in self._spent:
            return False
        self._spent.add(grant_id)
        return True


def _process_approval_authority_attests(mandate: Mandate, grant_id: str, approval_token: str) -> bool:
    """Ask the process-level approval authority whether it minted this token.

    Resolved from process configuration, never from a caller argument: this is
    what stops the execution side recording an approval it minted itself with
    an issuer of its own (BEAAA-35331 SA7).
    """
    from .approval import _process_issuer  # human-side module, start-up wiring

    return _process_issuer().attests(mandate, grant_id, approval_token)


def _process_approval_authority_spends(mandate: Mandate, grant_id: str, approval_token: str) -> bool:
    """Ask the process-level approval authority to spend this grant, once.

    Same resolution path as :func:`_process_approval_authority_attests`, and
    the same reason: the answer comes from process configuration, never from a
    caller argument or from a row the execution path can write
    (BEAAA-35354 SA9).
    """
    from .approval import _process_issuer  # human-side module, start-up wiring

    return _process_issuer().spend(mandate, grant_id, approval_token)


def _mandate_fingerprint(mandate: Mandate) -> str:
    body = json.dumps(asdict(mandate), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"mandate:{body}".encode()).hexdigest()


def _approval_digest(secret: str, mandate: Mandate, grant_id: str) -> str:
    body = json.dumps(asdict(mandate), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{secret}:{grant_id}:{body}".encode()).hexdigest()


class SyntheticMerchantAdapter:
    """Explicit future-adapter seam; never performs a real purchase."""

    def purchase(self, *, case_id: str, sku: str, amount_cents: int) -> dict[str, Any]:
        return {
            "merchant": "synthetic-merchant",
            "merchant_event_id": f"merchant-{case_id}",
            "sku": sku,
            "amount_cents": amount_cents,
            "outcome": "accepted",
            "synthetic": True,
        }


class EvidenceStore:
    """Restart-safe local ledger and evidence-reader/claims-review seam."""

    def __init__(self, path: str | Path, *, tenant: str | None = None) -> None:
        self.path = str(path)
        # BEAAA-35357 A9: the reader is scoped to one owner at construction.
        # There is no per-call tenant argument anywhere on this class, so a
        # caller cannot name a different owner on the way past.
        self.tenant = tenant
        self.db = sqlite3.connect(self.path, timeout=30.0)
        self.db.row_factory = sqlite3.Row
        # Concurrent demo requests are two connections against one file:
        # wait for the writer instead of failing the losing request.
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
              case_id TEXT PRIMARY KEY, mode TEXT NOT NULL, mandate_json TEXT NOT NULL,
              approval_json TEXT NOT NULL, assertion_json TEXT NOT NULL,
              merchant_json TEXT, status TEXT NOT NULL, mismatch_json TEXT NOT NULL,
              protection_json TEXT
            );
            CREATE TABLE IF NOT EXISTS approvals (
              grant_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, mandate_json TEXT NOT NULL,
              approval_token TEXT NOT NULL, origin TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS approval_receipts (
              task_id TEXT PRIMARY KEY, receipt_json TEXT NOT NULL, recorded_at TEXT NOT NULL,
              outcome_json TEXT, executed_at TEXT, protection_at_execution_json TEXT
            );
            CREATE TABLE IF NOT EXISTS executions (
              id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT UNIQUE NOT NULL,
              case_id TEXT NOT NULL, ledger_allocation TEXT UNIQUE NOT NULL,
              receipt_json TEXT NOT NULL
            );
            """
        )
        self.db.commit()
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        cols = {row[1] for row in self.db.execute("PRAGMA table_info(cases)")}
        if "protection_json" not in cols:
            self.db.execute("ALTER TABLE cases ADD COLUMN protection_json TEXT")
            self.db.commit()
        if "tenant" not in cols:
            self.db.execute("ALTER TABLE cases ADD COLUMN tenant TEXT")
            self.db.commit()
        receipt_cols = {row[1] for row in self.db.execute("PRAGMA table_info(approval_receipts)")}
        if "protection_at_execution_json" not in receipt_cols:
            self.db.execute("ALTER TABLE approval_receipts ADD COLUMN protection_at_execution_json TEXT")
            self.db.commit()

    def record_case(
        self,
        *,
        case_id: str,
        grant_id: str,
        asserted_sku: str,
        asserted_amount_cents: int,
        idempotency_key: str,
        merchant: SyntheticMerchantAdapter,
        independent_evidence: bool = True,
    ) -> dict[str, Any]:
        """Execute one approved case.

        BEAAA-35331: the caller supplies a ``grant_id`` and nothing else that is
        authorisation-shaped. This method consumes the approval row itself,
        re-validates its origin at consume time, rebuilds the verifier from the
        recorded token, and checks that token against the process-level approval
        authority. No verifier, token, secret or signing key crosses this
        boundary.

        BEAAA-35354 A6: the idempotent retry is answered before the grant is
        consumed. A client whose first request timed out re-sends it with the
        same key and gets its receipt; consuming first turned that ordinary
        retry into a refusal, because the grant it is retrying is the grant the
        first attempt already spent.
        """
        replay = self._replay_for_idempotency_key(idempotency_key, case_id)
        if replay is not None:
            return replay

        # Enforce the mandate before the side effect, never reconcile after it.
        # This runs ahead of _consume_approval on purpose: a refused attempt
        # must leave the grant unspent, so the human's single approval is still
        # there to be used on an assertion that is actually within it.
        previewed = self._approval_preview(grant_id, case_id)
        if previewed is not None:
            outside = _mandate_overreach(previewed, asserted_sku, asserted_amount_cents)
            if outside:
                raise MandateExceeded(
                    "assertion falls outside the approved mandate: "
                    + "; ".join(
                        f"{d['field']} approved {d['mandate']!r}, asserted {d['assertion']!r}" for d in outside
                    )
                )

        approval = self._consume_approval(grant_id, case_id)
        if approval is None:
            if self._approval_state(grant_id, case_id) == APPROVAL_REVOKED:
                raise PermissionError("human approval grant was revoked")
            raise PermissionError("no unspent human approval grant for this case")
        if approval["origin"] not in APPROVAL_ORIGINS:
            raise PermissionError(f"approval origin is not recognised: {approval['origin']}")
        mandate = approval["mandate"]
        approval_token = approval["approval_token"]
        if not _verifier_for_recorded_approval(mandate, approval_token).verify(mandate, approval_token):
            raise PermissionError("human approval token is invalid")
        if not _process_approval_authority_attests(mandate, grant_id, approval_token):
            raise PermissionError("approval was not minted by the human-side approval authority")

        # Reserve and commit before the merchant side effect so replay cannot
        # reach the merchant a second time.
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO executions (idempotency_key, case_id, ledger_allocation, receipt_json) VALUES (?, ?, ?, ?)",
                    (idempotency_key, case_id, f"allocation-{idempotency_key}", json.dumps({"state": "reserved"})),
                )
        except sqlite3.IntegrityError:
            # A concurrent request reserved the same key first.
            replay = self._replay_for_idempotency_key(idempotency_key, case_id)
            if replay is None:
                raise
            return replay

        # Anything outside the mandate was already refused above, so what is
        # left is variance within it — the agent spent less than it was allowed
        # to. That is recorded for review, not refused.
        mismatches: list[dict[str, Any]] = []
        if asserted_amount_cents != mandate.amount_cents:
            mismatches.append({"field": "amount_cents", "mandate": mandate.amount_cents, "assertion": asserted_amount_cents})

        # The activation state in force when this case is recorded. Persisted
        # with the case so the receipt never restates today's program status as
        # though it had been true at purchase time (BEAAA-35133 A).
        # BEAAA-35357 A5: the case ledger is append-only. A second record under
        # an existing case_id is refused here, before the merchant is called, so
        # neither the evidence nor the charge happens twice under one case.
        if self.db.execute("SELECT 1 FROM cases WHERE case_id = ?", (case_id,)).fetchone() is not None:
            raise PermissionError(
                f"case {case_id} is already recorded; the case ledger is append-only"
            )

        protection = surface_protection_state(
            "receipt", default_program_version(), default_registry()
        )

        merchant_evidence = None
        if independent_evidence:
            merchant_evidence = merchant.purchase(
                case_id=case_id, sku=asserted_sku, amount_cents=asserted_amount_cents
            )
        status = "UNKNOWN/INCOMPLETE" if merchant_evidence is None else ("MISMATCH" if mismatches else "MATCH")
        assertion = {"sku": asserted_sku, "amount_cents": asserted_amount_cents, "source": "agent-assertion"}
        approval = {"source": "human-approval", "token_digest": hashlib.sha256(approval_token.encode()).hexdigest()}
        # BEAAA-35453 A14: the completion instant is recorded here, on the
        # execution that produced it. A shareable note that names a date
        # reads it from this row, so no date exists for a case that never ran.
        receipt = {"mode": MODE, "private": True, "case_id": case_id, "status": status,
                   "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

        with self.db:
            self.db.execute(
                "INSERT INTO cases (case_id, tenant, mode, mandate_json, approval_json,"
                " assertion_json, merchant_json, status, mismatch_json, protection_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (case_id, self.tenant, MODE, json.dumps(asdict(mandate), sort_keys=True), json.dumps(approval, sort_keys=True),
                 json.dumps(assertion, sort_keys=True), json.dumps(merchant_evidence, sort_keys=True) if merchant_evidence else None,
                 status, json.dumps(mismatches, sort_keys=True), json.dumps(protection, sort_keys=True)),
            )
            self.db.execute(
                "UPDATE executions SET receipt_json = ? WHERE idempotency_key = ?",
                (json.dumps(receipt, sort_keys=True), idempotency_key),
            )
        return self.packet(case_id)

    def _record_approval(
        self,
        *,
        grant_id: str,
        case_id: str,
        mandate: Mandate,
        approval_token: str,
        origin: str,
        issuer: HumanApprovalIssuer,
    ) -> str:
        """Store one human approval grant. Reached only through
        ``stage_a_demo.approval.approve_mandate``; the execution path has no
        public entry point to the approvals table (BEAAA-35331 SA7)."""
        if not isinstance(issuer, HumanApprovalIssuer):
            raise PermissionError("recording an approval requires the human-side issuer capability")
        if origin not in APPROVAL_ORIGINS:
            raise PermissionError(f"approval origin is not recognised: {origin}")
        if not issuer.attests(mandate, grant_id, approval_token):
            raise PermissionError("approval token was not minted by the issuer presented")
        if not _process_approval_authority_attests(mandate, grant_id, approval_token):
            raise PermissionError("approval was not minted by the human-side approval authority")
        with self.db:
            self.db.execute(
                "INSERT INTO approvals (grant_id, case_id, mandate_json, approval_token, origin) VALUES (?, ?, ?, ?, ?)",
                (grant_id, case_id, json.dumps(asdict(mandate), sort_keys=True), approval_token, origin),
            )
        return grant_id

    def _approval_preview(self, grant_id: str, case_id: str) -> Mandate | None:
        """The mandate on an unspent grant, read without spending it.

        Needed so an over-mandate assertion can be refused *before* the grant is
        consumed (BEAAA-34396 A4). Read-only: it flips no flag and touches no
        ledger, and every authority check still runs at consume time, so this
        adds no route to authorise anything.
        """
        row = self.db.execute(
            "SELECT mandate_json FROM approvals WHERE grant_id = ? AND case_id = ? AND consumed = ?",
            (grant_id, case_id, APPROVAL_UNSPENT),
        ).fetchone()
        if row is None:
            return None
        return Mandate(**json.loads(row["mandate_json"]))

    def _consume_approval(self, grant_id: str, case_id: str) -> dict[str, Any] | None:
        """Spend a grant once. Returns None when there is no unspent grant.

        Two independent records have to agree that the grant is unspent: the
        ``consumed`` column on the approvals row, which survives a restart, and
        the process approval authority's own spent-grant ledger, which the
        execution path has no handle to. Flipping the column back with plain
        SQL no longer revives the grant, because the authority that minted the
        token still remembers spending it (BEAAA-35354 SA9). Either record
        saying spent is enough to refuse; the column is kept so a grant spent
        before a restart stays spent when the secret is injected and tokens
        outlive the process.
        """
        with self.db:
            spent = self.db.execute(
                "UPDATE approvals SET consumed = ? WHERE grant_id = ? AND case_id = ? AND consumed = ?",
                (APPROVAL_CONSUMED, grant_id, case_id, APPROVAL_UNSPENT),
            )
            if spent.rowcount != 1:
                return None
            row = self.db.execute(
                "SELECT mandate_json, approval_token, origin FROM approvals WHERE grant_id = ?", (grant_id,)
            ).fetchone()
        mandate = Mandate(**json.loads(row["mandate_json"]))
        if not _process_approval_authority_spends(mandate, grant_id, row["approval_token"]):
            return None
        return {
            "mandate": mandate,
            "approval_token": row["approval_token"],
            "origin": row["origin"],
        }

    def _replay_for_idempotency_key(self, idempotency_key: str, case_id: str) -> dict[str, Any] | None:
        """The receipt an earlier request already reserved under this key.

        ``None`` when the key is new, which is the ordinary first-execution
        path. A key already bound to a different case is a conflict, not a
        replay: this read runs ahead of every approval check (BEAAA-35354 A6),
        so it must never hand back a receipt for a case the caller did not name.
        """
        existing = self.db.execute(
            "SELECT case_id FROM executions WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if existing is None:
            return None
        if existing["case_id"] != case_id:
            raise PermissionError("idempotency key is already bound to a different case")
        return self._await_packet(existing["case_id"])

    def _approval_state(self, grant_id: str, case_id: str) -> int | None:
        row = self.db.execute(
            "SELECT consumed FROM approvals WHERE grant_id = ? AND case_id = ?", (grant_id, case_id)
        ).fetchone()
        return None if row is None else int(row["consumed"])

    def revoke_approval(self, grant_id: str) -> bool:
        """Withdraw an approval that has not been spent yet.

        The path for a human who changes their mind. False when there is nothing
        to withdraw: no such grant on this ledger, already spent, or already
        revoked. Revocation never unwinds a purchase that already happened
        (BEAAA-35133 B).
        """
        with self.db:
            cur = self.db.execute(
                "UPDATE approvals SET consumed = ? WHERE grant_id = ? AND consumed = ?",
                (APPROVAL_REVOKED, grant_id, APPROVAL_UNSPENT),
            )
        return cur.rowcount == 1

    # ------------------------------------------------------------------
    # Path 1: the approval receipt is the evidence root (Addendum 2).
    #
    # BackBond records the user's answer to the agent's pre-execution check and
    # matches the outcome against the action the user approved. It does not
    # judge the action against a mandate of its own, because the mandate lives
    # in the user's chat with their agent and moves there.
    # ------------------------------------------------------------------

    def record_pre_execution_receipt(self, receipt: ApprovalReceipt) -> str:
        """Write the receipt before anything executes. Returns the task id.

        The ordering is the requirement, not a detail: enforce before the side
        effect, never reconcile after it. A row already present for this task is
        not overwritten — a receipt that could be rewritten after the fact is
        not evidence.

        Terms that cannot decide money are refused here rather than interpreted
        at claim time. A cancellation deadline with no zone denotes a different
        instant in every offset, so a claim on it either pays on money that may
        still be refundable or denies a loss that is real, depending on which
        way the ambiguity is leaned. Neither is a settlement; both are a guess
        about a field BackBond captured itself. This is the "validate at
        capture, not at claim" gate (BEAAA-35456, BEAAA-34396 R1): the agent is
        sent back to re-read the terms while nothing has executed yet.
        """
        if not isinstance(receipt, ApprovalReceipt):
            raise TypeError("record_pre_execution_receipt takes an ApprovalReceipt")
        defects = receipt.proposed_action.capture_defects()
        if defects:
            raise TermsNotCaptured(
                f"task {receipt.task_id}: the captured terms cannot decide money "
                f"({', '.join(defects)}); re-read the terms before execution"
            )
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO approval_receipts (task_id, receipt_json, recorded_at) VALUES (?, ?, ?)",
                    (receipt.task_id, receipt.to_json(), receipt.recorded_at),
                )
        except sqlite3.IntegrityError:
            raise PermissionError(f"task {receipt.task_id} already has a receipt; receipts are not rewritten")
        return receipt.task_id

    def receipt_for(self, task_id: str) -> ApprovalReceipt | None:
        row = self.db.execute(
            "SELECT receipt_json FROM approval_receipts WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        return _receipt_from_json(row["receipt_json"])

    def execute_approved_task(
        self, task_id: str, *, merchant: SyntheticMerchantAdapter, executed_at: str
    ) -> dict[str, Any]:
        """Run the task the user approved, and only that.

        Refuses when there is no receipt, or the receipt does not carry an OK.
        A STOP, a skipped check and an unanswered check all refuse here, and all
        three stay on the record for the claim. Absence is never approval.
        """
        receipt = self.receipt_for(task_id)
        if receipt is None:
            raise CheckNotApproved(f"no pre-execution receipt for task {task_id}")
        if not receipt.approved:
            raise CheckNotApproved(
                f"task {task_id} was not approved: the recorded answer is {receipt.answer}"
            )
        action = receipt.proposed_action
        evidence = merchant.purchase(
            case_id=task_id, sku=action.description, amount_cents=action.amount_cents
        )
        outcome = ExecutedOutcome(
            description=action.description,
            merchant=action.merchant,
            amount_cents=action.amount_cents,
            evidence_ref=str(evidence.get("merchant_event_id", "")),
        )
        return self.record_outcome(task_id, outcome, executed_at=executed_at)

    def record_outcome(
        self, task_id: str, outcome: ExecutedOutcome | None, *, executed_at: str
    ) -> dict[str, Any]:
        """Record what independent evidence says happened, and review the claim.

        Called for tasks this process executed and for tasks that executed
        without an approval, which is exactly the case a claim has to be able to
        reconstruct.
        """
        if self.receipt_for(task_id) is None:
            raise CheckNotApproved(f"no pre-execution receipt for task {task_id}")
        pinned = self.db.execute(
            "SELECT protection_at_execution_json FROM approval_receipts WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if pinned is not None and pinned["protection_at_execution_json"]:
            # Same objection the receipt guard answers, applied to the block
            # that decides which window a loss belongs to. A stored window that
            # a later call can move to a different executed_at is not evidence
            # of when the loss happened (BEAAA-35482 X7).
            raise PermissionError(
                f"task {task_id} already has a coverage window pinned at execution; "
                "the window a loss is judged against is not rewritten"
            )
        # Pin the coverage window as it stood at execution, while we are still
        # standing at that moment. Recomputing it later depends on a registry
        # that can move; a stored block cannot.
        at_execution = self._protection_in_force_at(executed_at)
        with self.db:
            self.db.execute(
                "UPDATE approval_receipts SET outcome_json = ?, executed_at = ?, "
                "protection_at_execution_json = ? WHERE task_id = ?",
                (json.dumps(asdict(outcome), sort_keys=True) if outcome else None, executed_at,
                 json.dumps(at_execution, sort_keys=True), task_id),
            )
        return self.claim_for(task_id)

    @staticmethod
    def _protection_in_force_at(executed_at: str | None) -> dict[str, Any]:
        """The protection state at the moment of the event, not at read time.

        The activation gate is time-bound, so "was this covered" and "is the
        programme active now" are different questions with different answers.
        Reading only the second one presents a loss that happened while covered
        beside an expired block, and a loss that predates activation beside a
        live one (BEAAA-34396 W4/W5). This answers the first.

        It never touches the verdict. Whether the claim is covered still comes
        from the receipt and the outcome alone, and a receipt still never
        implies coverage (harness A10/A12).
        """
        instant = observation_instant(executed_at)
        if instant is None:
            # An execution time we cannot order cannot be placed against a
            # window. Said plainly rather than defaulted to either side.
            return {"surface": "receipt", "ordered": False,
                    "reason": "executed_at_not_orderable", "executed_at": executed_at,
                    "protection_active": None, "activation_record_id": None}
        block = surface_protection_state(
            "receipt", default_program_version(), default_registry(), now=instant
        )
        block["ordered"] = True
        block["executed_at"] = executed_at
        return block

    def claim_for(self, task_id: str) -> dict[str, Any]:
        """Reconstruct the claim for one task from its stored receipt alone."""
        row = self.db.execute(
            "SELECT receipt_json, outcome_json, executed_at, protection_at_execution_json "
            "FROM approval_receipts WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        receipt = _receipt_from_json(row["receipt_json"])
        outcome = None
        if row["outcome_json"]:
            outcome = ExecutedOutcome(**json.loads(row["outcome_json"]))
        review = review_claim(receipt, outcome)
        review["executed_at"] = row["executed_at"]
        review["mode"] = MODE
        # Protection status still comes only from the activation gate; a receipt
        # never implies coverage (BEAAA-35133 A, harness A10/A12).
        review["protection"] = surface_protection_state(
            "receipt", default_program_version(), default_registry()
        )
        # The window in force when the event happened. Written once, at
        # execution, so it is evidence rather than a re-derivation against a
        # registry that has moved since. Rows recorded before this column
        # existed are recomputed at read time and say so.
        if row["protection_at_execution_json"]:
            at_execution = json.loads(row["protection_at_execution_json"])
            at_execution["recorded_at_execution"] = True
        elif row["executed_at"]:
            at_execution = self._protection_in_force_at(row["executed_at"])
            at_execution["recorded_at_execution"] = False
        else:
            at_execution = None
        review["protection_at_execution"] = at_execution
        return review

    def _enforce_owner(self, row: sqlite3.Row, case_id: str) -> None:
        """Refuse a read of a case recorded under another owner.

        BEAAA-35357 A9: co-tenant readers on one file were separated only by
        filesystem permissions. The owner now travels with the row and the
        reader carries its own, so a second store opened on the same file
        cannot read a case it does not own.
        """
        recorded = row["tenant"] if "tenant" in row.keys() else None
        if recorded != self.tenant:
            raise PermissionError(
                f"case {case_id} belongs to another owner; this reader is scoped to {self.tenant!r}"
            )

    def approval_count(self, case_id: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM approvals WHERE case_id = ?", (case_id,)).fetchone()[0]

    def case_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM cases").fetchone()[0]

    def packet_or_none(self, case_id: str) -> dict[str, Any] | None:
        try:
            return self.packet(case_id)
        except KeyError:
            return None

    def _await_packet(self, case_id: str, timeout: float = 15.0) -> dict[str, Any]:
        """Wait for the reserving request to commit its case row."""
        deadline = time.monotonic() + timeout
        while True:
            packet = self.packet_or_none(case_id)
            if packet is not None:
                return packet
            if time.monotonic() >= deadline:
                raise TimeoutError(f"execution for {case_id} was reserved but never committed")
            time.sleep(0.02)

    def packet(self, case_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(case_id)
        self._enforce_owner(row, case_id)
        execution = self.db.execute(
            "SELECT idempotency_key, ledger_allocation, receipt_json FROM executions WHERE case_id = ? ORDER BY id LIMIT 1",
            (case_id,),
        ).fetchone()
        recorded_state = row["protection_json"] if "protection_json" in row.keys() else None
        recorded_protection = (
            json.loads(recorded_state) if recorded_state else unrecorded_protection_state("receipt")
        )
        sources = {
            "mandate": json.loads(row["mandate_json"]),
            "approval": json.loads(row["approval_json"]),
            "agent_assertion": json.loads(row["assertion_json"]),
            "independent_merchant_evidence": json.loads(row["merchant_json"]) if row["merchant_json"] else None,
        }
        return {
            "mode": row["mode"],
            "case_id": case_id,
            "status": row["status"],
            "reviewable_mismatches": json.loads(row["mismatch_json"]),
            "sources": sources,
            "execution": dict(execution) if execution else None,
            "claims_review": {"source_links": [key for key, value in sources.items() if value is not None]},
            # Protection status comes only from the activation gate, as it stood
            # when this case was recorded. It is never recomputed at read time
            # and never inferred from this receipt, its status, or its evidence.
            "protection": recorded_protection,
        }

    def execution_count(self, idempotency_key: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM executions WHERE idempotency_key = ?", (idempotency_key,)).fetchone()[0]

    def close(self) -> None:
        self.db.close()


def _receipt_from_json(raw: str) -> ApprovalReceipt:
    """Rebuild a receipt from its stored bytes. The store is the only writer."""
    payload = json.loads(raw)
    payload.pop("amount_at_risk_cents", None)
    payload["proposed_action"] = ProposedAction(**payload["proposed_action"])
    return ApprovalReceipt(**payload)
