"""Production adapter for sealed appliance contract-v1.0 batches.

The sealed appliance supplies ``verify_batch`` and its prohibited content-field
set. This adapter owns only authentication, durable ordering/idempotency, and a
counts/hashes-only Log Tracker projection.
"""
from __future__ import annotations

import hashlib
import hmac
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Collection, Mapping, Any


class IngestRejected(ValueError):
    """A stable, content-free ingest rejection."""


@dataclass(frozen=True)
class IngestResult:
    accepted: bool
    duplicate: bool
    batch_hash: str


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _find_prohibited(value: Any, prohibited: Collection[str], path: str = "$") -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in prohibited:
                return child_path
            found = _find_prohibited(child, prohibited, child_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_prohibited(child, prohibited, f"{path}[{index}]")
            if found:
                return found
    return None


class ApplianceIngestStore:
    """Atomic SQLite adapter; verification always precedes duplicate handling."""

    def __init__(
        self,
        database: str | Path,
        migration: str | Path,
        *,
        verify_batch: Callable[[dict, str], bool],
        prohibited_fields: Collection[str],
    ) -> None:
        self.database = str(database)
        self.verify_batch = verify_batch
        self.prohibited_fields = frozenset(prohibited_fields)
        with self._connect() as db:
            db.executescript(Path(migration).read_text(encoding="utf-8"))

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def enroll(
        self, *, appliance_id: str, tenant_id: str, source_id: str,
        token: str, public_key_pem: str,
    ) -> None:
        if not all((appliance_id, tenant_id, source_id, token, public_key_pem)):
            raise ValueError("complete enrollment required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            values = (_digest(tenant_id), _digest(source_id), _digest(token), public_key_pem)
            current = db.execute(
                "SELECT tenant_id_hash,source_id_hash,token_hash,public_key_pem "
                "FROM appliance_enrollments WHERE appliance_id=?", (appliance_id,),
            ).fetchone()
            if current is None:
                db.execute(
                    "INSERT INTO appliance_enrollments VALUES (?,?,?,?,?,"
                    "strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                    (appliance_id, *values),
                )
            elif current != values:
                raise ValueError("enrollment_binding_conflict")
            db.commit()

    def ingest(self, batch: dict, *, token: str, tenant_id: str, source_id: str) -> IngestResult:
        appliance_id = str(batch.get("appliance_id", ""))
        batch_hash = str(batch.get("batch_hash", ""))
        with self._connect() as db:
            enrollment = db.execute(
                "SELECT tenant_id_hash,source_id_hash,token_hash,public_key_pem "
                "FROM appliance_enrollments WHERE appliance_id=?", (appliance_id,),
            ).fetchone()
        if enrollment is None:
            raise IngestRejected("unknown_appliance")
        tenant_hash, source_hash, token_hash, public_key = enrollment
        if not (
            hmac.compare_digest(token_hash, _digest(token))
            and hmac.compare_digest(tenant_hash, _digest(tenant_id))
            and hmac.compare_digest(source_hash, _digest(source_id))
        ):
            raise IngestRejected("unauthorized_scope")

        # Sealed contract verifier recomputes the canonical batch hash and checks
        # the Ed25519 signature. This must happen before the duplicate lookup.
        if not self.verify_batch(batch, public_key):
            raise IngestRejected("signature_or_batch_hash_invalid")

        leaked_path = _find_prohibited(batch, self.prohibited_fields)
        if leaked_path:
            raise IngestRejected(f"content_boundary_violated:{leaked_path}")

        events = batch.get("events", [])
        determinations = batch.get("determinations", [])
        for item in [*events, *determinations]:
            if item.get("tenant_id") != tenant_id:
                raise IngestRejected("unauthorized_scope")
        counts = batch.get("counts") or {}
        receipt = (
            batch_hash, appliance_id, int(batch.get("batch_seq", -1)),
            batch.get("prev_batch_hash"), tenant_hash, source_hash,
            len(events), len(determinations),
            int(counts.get("billable_actions", 0)), int(counts.get("tokens", 0)),
        )

        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                if db.execute(
                    "SELECT 1 FROM appliance_ingest_receipts WHERE batch_hash=?",
                    (batch_hash,),
                ).fetchone():
                    db.rollback()
                    return IngestResult(True, True, batch_hash)
                tip = db.execute(
                    "SELECT batch_hash,batch_seq FROM appliance_ingest_receipts "
                    "WHERE appliance_id=? ORDER BY batch_seq DESC LIMIT 1",
                    (appliance_id,),
                ).fetchone()
                expected_prev = tip[0] if tip else None
                expected_seq = tip[1] + 1 if tip else 0
                if batch.get("prev_batch_hash") != expected_prev or int(batch.get("batch_seq", -1)) != expected_seq:
                    raise IngestRejected("broken_chain")
                db.execute(
                    "INSERT INTO appliance_ingest_receipts "
                    "(batch_hash,appliance_id,batch_seq,prev_batch_hash,tenant_id_hash,"
                    "source_id_hash,events_count,determinations_count,billable_actions,tokens_count) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    receipt,
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return IngestResult(True, False, batch_hash)

    def status(self) -> dict[str, int]:
        with self._connect() as db:
            batches, events, determinations = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(events_count),0),"
                "COALESCE(SUM(determinations_count),0) FROM appliance_ingest_receipts"
            ).fetchone()
        return {"batches_accepted": batches, "events_accepted": events,
                "determinations_accepted": determinations}
