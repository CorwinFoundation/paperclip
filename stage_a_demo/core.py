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

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
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
        """
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

        existing = self.db.execute(
            "SELECT case_id FROM executions WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if existing is not None:
            return self._await_packet(existing["case_id"])

        # Reserve and commit before the merchant side effect so replay cannot
        # reach the merchant a second time.
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO executions (idempotency_key, case_id, ledger_allocation, receipt_json) VALUES (?, ?, ?, ?)",
                    (idempotency_key, case_id, f"allocation-{idempotency_key}", json.dumps({"state": "reserved"})),
                )
        except sqlite3.IntegrityError:
            existing = self.db.execute(
                "SELECT case_id FROM executions WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is None:
                raise
            return self._await_packet(existing["case_id"])

        mismatches: list[dict[str, Any]] = []
        if asserted_sku != mandate.sku:
            mismatches.append({"field": "sku", "mandate": mandate.sku, "assertion": asserted_sku})
        if asserted_amount_cents != mandate.amount_cents:
            mismatches.append({"field": "amount_cents", "mandate": mandate.amount_cents, "assertion": asserted_amount_cents})

        # The activation state in force when this case is recorded. Persisted
        # with the case so the receipt never restates today's program status as
        # though it had been true at purchase time (BEAAA-35133 A).
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
        receipt = {"mode": MODE, "private": True, "case_id": case_id, "status": status}

        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO cases (case_id, mode, mandate_json, approval_json,"
                " assertion_json, merchant_json, status, mismatch_json, protection_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (case_id, MODE, json.dumps(asdict(mandate), sort_keys=True), json.dumps(approval, sort_keys=True),
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
