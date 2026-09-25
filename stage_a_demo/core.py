from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import MODE
from .receipt import (
    CLAIM_COVERED_EXECUTED_AGAINST_WITHDRAWAL,
    ApprovalReceipt,
    action_from_stored_terms,
    CheckNotApproved,
    ExecutedOutcome,
    ExecutionAlreadyClaimed,
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


def processing_instant() -> str:
    """One reading of BackBond's own clock, microsecond-precise.

    Microseconds, not seconds: the instants in one trace are written within the
    same second, so a second-granularity stamp records them as simultaneous and
    the trace stops being orderable (BEAAA-35631).
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


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


class CaseAlreadyRecorded(PermissionError):
    """A second execution tried to record itself under a case that already exists.

    A recorded case is append-only. Before BEAAA-34389 A5 the case row was
    written with ``INSERT OR REPLACE``, so a caller holding a fresh human
    approval for the same ``case_id`` could overwrite the mandate, approval and
    assertion of a case that had already executed. The packet kept pairing the
    *first* execution's receipt with the *rewritten* sources, which is the
    tampering the probe describes: one case's evidence against another case's
    receipt.

    Refused before the grant is consumed and before the merchant adapter, so
    the second attempt spends nothing. A genuine second purchase is a second
    case; re-running one case is not a thing this store can represent.
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


def _source_divergences(
    mandate: Mandate,
    asserted_sku: str,
    asserted_amount_cents: int,
    merchant_evidence: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Where the three records of one purchase disagree, field by field.

    BEAAA-35630: the only comparison here used to be the agent's assertion
    against the mandate. The independent merchant record was written to the
    case and then never read, so a merchant that shipped a different SKU and
    charged 49x the approved amount produced ``status='MATCH'`` with an empty
    incident list. The independent evidence the spec joins on was stored and
    not compared.

    Each entry names the field, carries the value every source holds for it,
    and lists which sources differ from the mandate, so a reviewer sees who
    disagreed rather than only that something did. Spec section 10 keeps
    measured fact, agent assertion and derived interpretation apart; this is
    the derived interpretation, and it names its inputs.

    A divergence is not a refusal. Spec section 11, "Wrong amount/SKU/date":
    the mismatch is a reviewable incident, visible with its source evidence,
    and "not automatically denied merely for differing from the mandate". An
    assertion *outside* the mandate is a different thing and never reaches
    here - :func:`_mandate_overreach` refuses it before the merchant is
    called (BEAAA-34396 A4).
    """
    asserted = {"sku": asserted_sku, "amount_cents": asserted_amount_cents}
    divergences: list[dict[str, Any]] = []
    for field in ("sku", "amount_cents"):
        sources: dict[str, Any] = {
            "mandate": getattr(mandate, field),
            "agent_assertion": asserted[field],
        }
        if merchant_evidence is not None:
            sources["independent_merchant_evidence"] = merchant_evidence.get(field)
        disagreeing = sorted(
            name for name, value in sources.items() if value != sources["mandate"]
        )
        if not disagreeing:
            continue
        divergences.append(
            {
                # field/mandate/assertion are the shape this list has always
                # carried; readers of the old row keep working.
                "field": field,
                "mandate": sources["mandate"],
                "assertion": sources["agent_assertion"],
                "sources": sources,
                "disagrees_with_mandate": disagreeing,
            }
        )
    return divergences


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
              protection_json TEXT, recorded_at TEXT
            );
            CREATE TABLE IF NOT EXISTS approvals (
              grant_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, mandate_json TEXT NOT NULL,
              approval_token TEXT NOT NULL, origin TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0,
              approved_at TEXT, tenant TEXT
            );
            CREATE TABLE IF NOT EXISTS approval_receipts (
              task_id TEXT PRIMARY KEY, receipt_json TEXT NOT NULL, recorded_at TEXT NOT NULL,
              outcome_json TEXT, executed_at TEXT, protection_at_execution_json TEXT,
              execution_claimed_at TEXT, tenant TEXT, withdrawn_at TEXT
            );
            -- BEAAA-35631: the three instants the build specification (line 218)
            -- requires stored separately, as first-class columns rather than
            -- inside receipt_json. occurred_at is when the underlying task
            -- happened, evidence_received_at is when the independent record
            -- arrived, reserved_at and recorded_at are BackBond's own
            -- processing clock either side of the side effect.
            CREATE TABLE IF NOT EXISTS executions (
              id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT UNIQUE NOT NULL,
              case_id TEXT NOT NULL, ledger_allocation TEXT UNIQUE NOT NULL,
              receipt_json TEXT NOT NULL,
              reserved_at TEXT, occurred_at TEXT, evidence_received_at TEXT, recorded_at TEXT
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
        if "recorded_at" not in cols:
            self.db.execute("ALTER TABLE cases ADD COLUMN recorded_at TEXT")
            self.db.commit()
        approval_cols = {row[1] for row in self.db.execute("PRAGMA table_info(approvals)")}
        if "approved_at" not in approval_cols:
            self.db.execute("ALTER TABLE approvals ADD COLUMN approved_at TEXT")
            self.db.commit()
        execution_cols = {row[1] for row in self.db.execute("PRAGMA table_info(executions)")}
        for column in ("reserved_at", "occurred_at", "evidence_received_at", "recorded_at"):
            if column not in execution_cols:
                self.db.execute(f"ALTER TABLE executions ADD COLUMN {column} TEXT")
                self.db.commit()
        receipt_cols = {row[1] for row in self.db.execute("PRAGMA table_info(approval_receipts)")}
        if "protection_at_execution_json" not in receipt_cols:
            self.db.execute("ALTER TABLE approval_receipts ADD COLUMN protection_at_execution_json TEXT")
            self.db.commit()
        if "execution_claimed_at" not in receipt_cols:
            self.db.execute("ALTER TABLE approval_receipts ADD COLUMN execution_claimed_at TEXT")
            self.db.commit()
        if "tenant" not in receipt_cols:
            # Nullable, like cases.tenant. Rows written before the column
            # existed read as owned by the untenanted bootstrap store, which is
            # the only reader that could have written them.
            self.db.execute("ALTER TABLE approval_receipts ADD COLUMN tenant TEXT")
            self.db.commit()
        if "withdrawn_at" not in receipt_cols:
            self.db.execute("ALTER TABLE approval_receipts ADD COLUMN withdrawn_at TEXT")
            self.db.commit()
        approval_cols = {row[1] for row in self.db.execute("PRAGMA table_info(approvals)")}
        if "tenant" not in approval_cols:
            # Nullable for the same reason approval_receipts.tenant is: rows
            # written before the column existed are owned by the untenanted
            # bootstrap store, the only store that could have written them.
            self.db.execute("ALTER TABLE approvals ADD COLUMN tenant TEXT")
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
        # A recorded case is append-only (BEAAA-34389 A5). This runs before
        # _consume_approval for the same reason the mandate check does: a
        # refused attempt must leave the human's grant unspent.
        if self.db.execute(
            "SELECT 1 FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone() is not None:
            raise CaseAlreadyRecorded(
                f"case {case_id} is already recorded; the case ledger is append-only"
            )

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
        approved_at = approval["approved_at"]

        # Reserve and commit before the merchant side effect so replay cannot
        # reach the merchant a second time.
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO executions (idempotency_key, case_id, ledger_allocation, receipt_json,"
                    " reserved_at) VALUES (?, ?, ?, ?, ?)",
                    (idempotency_key, case_id, f"allocation-{idempotency_key}", json.dumps({"state": "reserved"}),
                     processing_instant()),
                )
        except sqlite3.IntegrityError:
            # A concurrent request reserved the same key first.
            replay = self._replay_for_idempotency_key(idempotency_key, case_id)
            if replay is None:
                raise
            return replay

        # The activation state in force when this case is recorded. Persisted
        # with the case so the receipt never restates today's program status as
        # though it had been true at purchase time (BEAAA-35133 A).
        protection = surface_protection_state(
            "receipt", default_program_version(), default_registry()
        )

        # BEAAA-35631: occurrence and receipt are two different instants and are
        # read separately, either side of the call. evidence_received_at stays
        # NULL when no independent record arrived, so the missing-evidence case
        # has no receipt time to claim it had one.
        merchant_evidence = None
        evidence_received_at = None
        occurred_at = processing_instant()
        if independent_evidence:
            merchant_evidence = merchant.purchase(
                case_id=case_id, sku=asserted_sku, amount_cents=asserted_amount_cents
            )
            if merchant_evidence is not None:
                evidence_received_at = processing_instant()
        # Anything outside the mandate was already refused above. What is left
        # is compared here, after the merchant has answered, so the independent
        # record is joined against both the mandate and the agent's assertion
        # rather than only stored beside them (BEAAA-35630).
        mismatches = _source_divergences(
            mandate, asserted_sku, asserted_amount_cents, merchant_evidence
        )
        status = "UNKNOWN/INCOMPLETE" if merchant_evidence is None else ("MISMATCH" if mismatches else "MATCH")
        assertion = {"sku": asserted_sku, "amount_cents": asserted_amount_cents, "source": "agent-assertion"}
        approval = {"source": "human-approval", "token_digest": hashlib.sha256(approval_token.encode()).hexdigest(),
                    "approved_at": approved_at}
        # BEAAA-35453 A14: the completion instant is recorded here, on the
        # execution that produced it. A shareable note that names a date
        # reads it from this row, so no date exists for a case that never ran.
        receipt = {"mode": MODE, "private": True, "case_id": case_id, "status": status,
                   "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

        recorded_at = processing_instant()
        with self.db:
            self.db.execute(
                "INSERT INTO cases (case_id, tenant, mode, mandate_json, approval_json,"
                " assertion_json, merchant_json, status, mismatch_json, protection_json, recorded_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (case_id, self.tenant, MODE, json.dumps(asdict(mandate), sort_keys=True), json.dumps(approval, sort_keys=True),
                 json.dumps(assertion, sort_keys=True), json.dumps(merchant_evidence, sort_keys=True) if merchant_evidence else None,
                 status, json.dumps(mismatches, sort_keys=True), json.dumps(protection, sort_keys=True), recorded_at),
            )
            self.db.execute(
                "UPDATE executions SET receipt_json = ?, occurred_at = ?, evidence_received_at = ?,"
                " recorded_at = ? WHERE idempotency_key = ?",
                (json.dumps(receipt, sort_keys=True), occurred_at, evidence_received_at, recorded_at,
                 idempotency_key),
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
                "INSERT INTO approvals (grant_id, case_id, mandate_json, approval_token, origin,"
                " approved_at, tenant) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (grant_id, case_id, json.dumps(asdict(mandate), sort_keys=True), approval_token, origin,
                 processing_instant(), self.tenant),
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
                "SELECT mandate_json, approval_token, origin, approved_at FROM approvals WHERE grant_id = ?",
                (grant_id,),
            ).fetchone()
        mandate = Mandate(**json.loads(row["mandate_json"]))
        if not _process_approval_authority_spends(mandate, grant_id, row["approval_token"]):
            return None
        return {
            "mandate": mandate,
            "approval_token": row["approval_token"],
            "origin": row["origin"],
            "approved_at": row["approved_at"],
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

        Scoped to the owner of the grant (BEAAA-35553 Z3). The approvals ledger
        carries the tenant now, the way ``cases`` has since A9 and
        ``approval_receipts`` has since BEAAA-35578. A revocation is a write,
        and the boundary that already held for reads did nothing here: a second
        store on the same file could name any grant id and cancel a stranger's
        pending purchase. The harm is denial rather than disclosure, which is
        why a read-side boundary never caught it.

        Unlike the receipt surface this answers False rather than raising. A
        grant id is a bearer secret held by the party that requested the
        approval, not a shared handle like a case id, so "no such grant here"
        is the whole truth this caller is owed and a refusal that named another
        owner would confirm the id is live.

        One lever, both paths. A user withdrawing consent knows the id they
        were given, not which of BackBond's two tables recorded it, so an id
        that names a Path 1 receipt is withdrawn here too (BEAAA-35553 Z2).
        ``withdraw_receipt`` remains the explicit Path 1 entry point for a
        caller that knows it holds a task id and wants to name the instant.
        """
        with self.db:
            cur = self.db.execute(
                "UPDATE approvals SET consumed = ? WHERE grant_id = ? AND consumed = ? AND tenant IS ?",
                (APPROVAL_REVOKED, grant_id, APPROVAL_UNSPENT, self.tenant),
            )
        if cur.rowcount == 1:
            return True
        return self._withdraw_receipt_by_any_id(grant_id)

    def _withdraw_receipt_by_any_id(self, identifier: str) -> bool:
        """The Path 1 half of ``revoke_approval``: a task id or a receipt id.

        Owner-scoped in SQL rather than through ``_enforce_receipt_owner``,
        because this arrives on the mandate lever's contract: a bearer id that
        is not ours is answered False, not with a refusal that confirms the id
        is live somewhere else.
        """
        task_id = identifier
        row = self.db.execute(
            "SELECT task_id FROM approval_receipts WHERE task_id = ? AND tenant IS ?",
            (identifier, self.tenant),
        ).fetchone()
        if row is None:
            # Not a task id. The receipt id is inside receipt_json rather than
            # a column of its own, so it is resolved by reading this owner's
            # rows — never another owner's.
            for candidate in self.db.execute(
                "SELECT task_id, receipt_json FROM approval_receipts "
                "WHERE tenant IS ? AND withdrawn_at IS NULL AND execution_claimed_at IS NULL",
                (self.tenant,),
            ):
                if json.loads(candidate["receipt_json"]).get("receipt_id") == identifier:
                    task_id = candidate["task_id"]
                    break
            else:
                return False
        with self.db:
            cur = self.db.execute(
                "UPDATE approval_receipts SET withdrawn_at = ? WHERE task_id = ? AND tenant IS ? "
                "AND withdrawn_at IS NULL AND execution_claimed_at IS NULL",
                (processing_instant(), task_id, self.tenant),
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
                    "INSERT INTO approval_receipts (task_id, receipt_json, recorded_at, tenant) "
                    "VALUES (?, ?, ?, ?)",
                    (receipt.task_id, receipt.to_json(), receipt.recorded_at, self.tenant),
                )
        except sqlite3.IntegrityError:
            raise PermissionError(f"task {receipt.task_id} already has a receipt; receipts are not rewritten")
        return receipt.task_id

    def withdraw_receipt(self, task_id: str, *, withdrawn_at: str) -> bool:
        """Withdraw a Path 1 approval before anything executes.

        The mandate path has had this since BEAAA-35133 (``revoke_approval``).
        Path 1 had nothing, and Path 1 is the path the consumer demo runs and
        the one the README calls the evidence root: a user who answered OK and
        then changed their mind had no lever to pull, and
        ``execute_approved_task`` still went to the merchant on 48000 cents the
        user had tried to stop (BEAAA-35553 Z2).

        Same answers the mandate lever gives. False when there is nothing to
        withdraw: no receipt under that task id, already withdrawn, or the
        execution slot is already claimed. Withdrawal never unwinds a purchase
        that already happened (Z4 parity) — once the slot is claimed the money
        may already be gone, and the honest record is the claim, not a
        retroactive cancellation.

        The conditional UPDATE is deliberate rather than a read-then-write.
        ``execute_approved_task`` claims the same row under the same write
        lock, so of a withdrawal and an execution racing on one file exactly
        one wins, and neither can observe a stale answer from the other.
        """
        if not isinstance(withdrawn_at, str) or not withdrawn_at.strip():
            raise ValueError("withdraw_receipt needs the instant the user withdrew, as a timestamp")
        row = self.db.execute(
            "SELECT tenant FROM approval_receipts WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return False
        # A withdrawal is a write on the receipt surface, so it answers a
        # foreign owner the way every other write there does (BEAAA-35578).
        self._enforce_receipt_owner(row, task_id)
        with self.db:
            cur = self.db.execute(
                "UPDATE approval_receipts SET withdrawn_at = ? WHERE task_id = ? AND tenant IS ? "
                "AND withdrawn_at IS NULL AND execution_claimed_at IS NULL",
                (withdrawn_at, task_id, self.tenant),
            )
        return cur.rowcount == 1

    def withdrawn_at(self, task_id: str) -> str | None:
        """When the user withdrew the approval on ``task_id``, or None."""
        row = self.db.execute(
            "SELECT withdrawn_at, tenant FROM approval_receipts WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        self._enforce_receipt_owner(row, task_id)
        return row["withdrawn_at"]

    def receipt_for(self, task_id: str) -> ApprovalReceipt | None:
        row = self.db.execute(
            "SELECT receipt_json, tenant FROM approval_receipts WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        self._enforce_receipt_owner(row, task_id)
        return _receipt_from_json(row["receipt_json"])

    def _enforce_receipt_owner(self, row: sqlite3.Row, task_id: str) -> None:
        """Refuse a read or a write of a receipt recorded under another owner.

        BEAAA-35578: the owner check BEAAA-35357 A9 put on cases stopped at the
        case surface. Path 1 keys on task_id alone, so a second store opened on
        the same file could read another owner's receipt, and write that
        owner's outcome. Refusing is not a "not found": a task id that exists
        under another owner is not this reader's to answer for.
        """
        recorded = row["tenant"] if "tenant" in row.keys() else None
        if recorded != self.tenant:
            raise PermissionError(
                f"task {task_id} belongs to another owner; this reader is scoped to {self.tenant!r}"
            )

    def execute_approved_task(
        self, task_id: str, *, merchant: SyntheticMerchantAdapter, executed_at: str
    ) -> dict[str, Any]:
        """Run the task the user approved, and only that.

        Refuses when there is no receipt, or the receipt does not carry an OK.
        A STOP, a skipped check and an unanswered check all refuse here, and all
        three stay on the record for the claim. Absence is never approval.

        Runs at most once per receipt. A repeat after the first call settled is
        answered with that call's claim; a repeat while one is in flight or
        unsettled raises ExecutionAlreadyClaimed. In neither case is the
        merchant reached a second time.
        """
        receipt = self.receipt_for(task_id)
        if receipt is None:
            raise CheckNotApproved(f"no pre-execution receipt for task {task_id}")
        if not receipt.approved:
            raise CheckNotApproved(
                f"task {task_id} was not approved: the recorded answer is {receipt.answer}"
            )
        withdrawn = self.withdrawn_at(task_id)
        if withdrawn is not None:
            # Read before the merchant is reached, like every other guard on
            # this path. A withdrawal only visible at claim time is a record of
            # the loss, not a lever (BEAAA-35553 Z2).
            raise CheckNotApproved(
                f"task {task_id} was withdrawn at {withdrawn}; the recorded approval no longer stands"
            )
        # Claim the one execution this receipt buys, before the merchant is
        # reached. Every other guard on this path lives inside record_outcome,
        # which runs after the purchase; a guard after the side effect is a
        # reconciliation, and the receipt's own rule is "enforce before the
        # side effect, never reconcile after it" (BEAAA-35579 W2/W3).
        #
        # The conditional UPDATE is the whole mechanism: SQLite settles it
        # under the write lock, so of two concurrent callers on one file
        # exactly one sees rowcount 1 and the loser never reaches the adapter.
        # Path 1 carries no caller-supplied idempotency key, so the task id is
        # the key: one receipt, one execution.
        with self.db:
            claimed = self.db.execute(
                "UPDATE approval_receipts SET execution_claimed_at = ? "
                "WHERE task_id = ? AND tenant IS ? AND execution_claimed_at IS NULL "
                "AND withdrawn_at IS NULL",
                (executed_at, task_id, self.tenant),
            )
        if claimed.rowcount != 1:
            # A withdrawal that landed between the read above and this UPDATE
            # loses the row here rather than being missed. Named before the
            # in-flight reading, because "already claimed" would describe the
            # wrong event and send a recovery down the wrong path.
            lost_to_withdrawal = self.withdrawn_at(task_id)
            if lost_to_withdrawal is not None:
                raise CheckNotApproved(
                    f"task {task_id} was withdrawn at {lost_to_withdrawal}; "
                    "the recorded approval no longer stands"
                )
            settled = self.db.execute(
                "SELECT outcome_json FROM approval_receipts WHERE task_id = ?", (task_id,)
            ).fetchone()
            if settled is not None and settled["outcome_json"]:
                # The first call finished. Answer the retry with the answer it
                # already gave rather than charging again; this is the posture
                # record_case takes on an idempotent retry (BEAAA-35354 A6).
                return self.claim_for(task_id)
            # Claimed but not settled: either a caller is inside the merchant
            # right now, or one was and did not come back. We cannot tell from
            # here whether that purchase landed, so we refuse rather than risk
            # a second one. Recovery is record_outcome with what the merchant
            # actually says happened — the same entry point used for a task
            # that executed outside this process.
            raise ExecutionAlreadyClaimed(
                f"task {task_id} already has an execution in flight or unsettled; "
                "one receipt buys one execution"
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
                "protection_at_execution_json = ? WHERE task_id = ? AND tenant IS ?",
                (json.dumps(asdict(outcome), sort_keys=True) if outcome else None, executed_at,
                 json.dumps(at_execution, sort_keys=True), task_id, self.tenant),
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
            "SELECT receipt_json, outcome_json, executed_at, protection_at_execution_json, tenant, "
            "withdrawn_at FROM approval_receipts WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        self._enforce_receipt_owner(row, task_id)
        receipt = _receipt_from_json(row["receipt_json"])
        outcome = None
        if row["outcome_json"]:
            outcome = ExecutedOutcome(**json.loads(row["outcome_json"]))
        review = review_claim(receipt, outcome)
        # A withdrawal is on the record whether or not anything executed after
        # it. When something did execute anyway, the verdict is the withdrawal
        # and not the OK the user had already taken back: settling that charge
        # as `not_covered_user_approved_this` describes an approval that did
        # not stand at the moment of execution (BEAAA-35553 Z5). Ordered after
        # the independence test the same way the STOP branch is, so a
        # self-reported outcome stays incomplete rather than becoming payable.
        review["withdrawn_at"] = row["withdrawn_at"]
        if row["withdrawn_at"] and outcome is not None and outcome.independent:
            review["claim"] = CLAIM_COVERED_EXECUTED_AGAINST_WITHDRAWAL
            review["covered"] = receipt.amount_at_risk_cents > 0
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
            "SELECT idempotency_key, ledger_allocation, receipt_json, reserved_at, occurred_at,"
            " evidence_received_at, recorded_at FROM executions WHERE case_id = ? ORDER BY id LIMIT 1",
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
            # BEAAA-35631: the time half of "one reproducible mandate-to-outcome
            # trace". Each instant is read from its own column, and `ordered`
            # is what lets a reconstruction put the approval, the task, the
            # independent record and BackBond's own write in sequence. An
            # instant that was never recorded is absent, never guessed.
            "trace": _trace(
                approved_at=sources["approval"].get("approved_at") if isinstance(sources["approval"], dict) else None,
                recorded_at=row["recorded_at"] if "recorded_at" in row.keys() else None,
                execution=execution,
            ),
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


def _trace(
    *,
    approved_at: str | None,
    recorded_at: str | None,
    execution: sqlite3.Row | None,
) -> dict[str, Any]:
    """The instants in one case, each from its own column, in time order.

    Keys name what the instant is, not which table it came from: ``approved_at``
    is the human decision, ``occurred_at`` is when the underlying task happened,
    ``evidence_received_at`` is when the independent record arrived, and
    ``reserved_at``/``recorded_at`` are BackBond's processing clock either side
    of the side effect. ``ordered`` is the sequence a claim is reconstructed
    from; ``unordered`` names the instants that were never recorded, so a
    missing time reads as missing rather than as zero.
    """
    instants: dict[str, str | None] = {
        "approved_at": approved_at,
        "reserved_at": execution["reserved_at"] if execution is not None else None,
        "occurred_at": execution["occurred_at"] if execution is not None else None,
        "evidence_received_at": execution["evidence_received_at"] if execution is not None else None,
        "recorded_at": recorded_at or (execution["recorded_at"] if execution is not None else None),
    }
    ordered = sorted(
        ({"event": name, "at": value} for name, value in instants.items() if value),
        key=lambda entry: (entry["at"], entry["event"]),
    )
    return {
        **instants,
        "ordered": ordered,
        "unordered": sorted(name for name, value in instants.items() if not value),
    }


def _receipt_from_json(raw: str) -> ApprovalReceipt:
    """Rebuild a receipt from its stored bytes. The store is the only writer.

    BEAAA-35632: the terms go through ``action_from_stored_terms``, not the bare
    constructor. Reading is not capturing, and a row written before the
    deadline capture gate landed has to stay readable - otherwise the user
    cannot see their receipt and the claim cannot be assessed at all.
    """
    payload = json.loads(raw)
    payload.pop("amount_at_risk_cents", None)
    payload["proposed_action"] = action_from_stored_terms(payload["proposed_action"])
    return ApprovalReceipt(**payload)
