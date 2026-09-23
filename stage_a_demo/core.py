from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import MODE
from .activation import (
    default_program_version,
    default_registry,
    surface_protection_state,
)


@dataclass(frozen=True)
class Mandate:
    mandate_id: str
    sku: str
    amount_cents: int


class ApprovalVerifier:
    """Execution-side capability: verifies approvals and has no minting API."""

    def __init__(self, secret: str = "stage-a-human-demo-secret") -> None:
        self._secret = secret

    def verify(self, mandate: Mandate, token: str) -> bool:
        return secrets.compare_digest(_approval_digest(self._secret, mandate), token)


class HumanApprovalIssuer:
    """Human-side demo seam. Never pass this capability to an agent or adapter."""

    def __init__(self, secret: str = "stage-a-human-demo-secret") -> None:
        self._secret = secret

    def approve(self, mandate: Mandate) -> tuple[str, ApprovalVerifier]:
        return _approval_digest(self._secret, mandate), ApprovalVerifier(self._secret)


def _approval_digest(secret: str, mandate: Mandate) -> str:
    body = json.dumps(asdict(mandate), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{secret}:{body}".encode()).hexdigest()


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
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
              case_id TEXT PRIMARY KEY, mode TEXT NOT NULL, mandate_json TEXT NOT NULL,
              approval_json TEXT NOT NULL, assertion_json TEXT NOT NULL,
              merchant_json TEXT, status TEXT NOT NULL, mismatch_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS executions (
              id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT UNIQUE NOT NULL,
              case_id TEXT NOT NULL, ledger_allocation TEXT UNIQUE NOT NULL,
              receipt_json TEXT NOT NULL
            );
            """
        )
        self.db.commit()

    def record_case(
        self,
        *,
        case_id: str,
        mandate: Mandate,
        approval_token: str,
        asserted_sku: str,
        asserted_amount_cents: int,
        idempotency_key: str,
        approval_verifier: ApprovalVerifier,
        merchant: SyntheticMerchantAdapter,
        independent_evidence: bool = True,
    ) -> dict[str, Any]:
        if not approval_verifier.verify(mandate, approval_token):
            raise PermissionError("human approval token is invalid")

        existing = self.db.execute(
            "SELECT case_id FROM executions WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if existing is not None:
            return self.packet(existing["case_id"])

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
            return self.packet(existing["case_id"])

        mismatches: list[dict[str, Any]] = []
        if asserted_sku != mandate.sku:
            mismatches.append({"field": "sku", "mandate": mandate.sku, "assertion": asserted_sku})
        if asserted_amount_cents != mandate.amount_cents:
            mismatches.append({"field": "amount_cents", "mandate": mandate.amount_cents, "assertion": asserted_amount_cents})

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
                "INSERT OR REPLACE INTO cases VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (case_id, MODE, json.dumps(asdict(mandate), sort_keys=True), json.dumps(approval, sort_keys=True),
                 json.dumps(assertion, sort_keys=True), json.dumps(merchant_evidence, sort_keys=True) if merchant_evidence else None,
                 status, json.dumps(mismatches, sort_keys=True)),
            )
            self.db.execute(
                "UPDATE executions SET receipt_json = ? WHERE idempotency_key = ?",
                (json.dumps(receipt, sort_keys=True), idempotency_key),
            )
        return self.packet(case_id)

    def packet(self, case_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(case_id)
        execution = self.db.execute(
            "SELECT idempotency_key, ledger_allocation, receipt_json FROM executions WHERE case_id = ? ORDER BY id LIMIT 1",
            (case_id,),
        ).fetchone()
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
            # Protection status comes only from the activation gate. It is
            # never inferred from this receipt, its status, or its evidence.
            "protection": surface_protection_state(
                "receipt", default_program_version(), default_registry()
            ),
        }

    def execution_count(self, idempotency_key: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM executions WHERE idempotency_key = ?", (idempotency_key,)).fetchone()[0]

    def close(self) -> None:
        self.db.close()
