"""Full Scan paid-pack entitlement store (BEAAA-38957).

Entitlements gate access to paid packs.  Today the only source is ``manual``
(an ops grant).  A payment-provider adapter would be added here once a
provider is configured in the repo; we do NOT activate any new paid service.

Schema is installed into the same SQLite file used for job persistence (or a
separate path if supplied).  All writes go through ``BEGIN IMMEDIATE`` under
the caller's RLock to stay consistent with FullScanJobPersistence.

Lifecycle
---------
``grant``   — create an unconsumed entitlement (idempotent on external_ref).
``check``   — True iff an unconsumed entitlement exists for (tenant, pack_id).
``consume`` — atomically claim one entitlement; idempotent on external_ref.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS full_scan_entitlements (
    entitlement_id  TEXT PRIMARY KEY,
    tenant          TEXT NOT NULL,
    pack_id         TEXT NOT NULL,
    max_cost_usd    REAL NOT NULL,
    source          TEXT NOT NULL,
    external_ref    TEXT,
    consumed        INTEGER NOT NULL DEFAULT 0,
    consumed_job_id TEXT,
    granted_at      TEXT,
    consumed_at     TEXT
);

-- Fast lookup: unconsumed grants for a tenant+pack
CREATE INDEX IF NOT EXISTS ix_entitlements_tenant_pack
    ON full_scan_entitlements (tenant, pack_id, consumed);

-- Idempotency: external_ref is unique per (tenant, pack_id) when non-NULL
CREATE UNIQUE INDEX IF NOT EXISTS ix_entitlements_ext_ref
    ON full_scan_entitlements (tenant, pack_id, external_ref)
    WHERE external_ref IS NOT NULL;
"""


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class FullScanEntitlementStore:
    """SQLite-backed entitlement store.

    Pass ``":memory:"`` for a transient store (useful in tests) or a filesystem
    path for durable storage.  Schema is installed idempotently on construction.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        self._conn = sqlite3.connect(
            self._path, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def grant(
        self,
        *,
        tenant: str,
        pack_id: str,
        max_cost_usd: float,
        source: str = "manual",
        external_ref: str | None = None,
    ) -> dict[str, Any]:
        """Create an unconsumed entitlement.

        Idempotent on (tenant, pack_id, external_ref): returns the existing
        record without creating a duplicate.
        """
        if external_ref is not None:
            # Check for an existing matching grant first (idempotent path).
            cur = self._conn.execute(
                "SELECT * FROM full_scan_entitlements "
                "WHERE tenant=? AND pack_id=? AND external_ref=?",
                (tenant, pack_id, external_ref),
            )
            row = cur.fetchone()
            if row is not None:
                return dict(row)

        entitlement_id = str(uuid.uuid4())
        now = _utcnow()
        with _transaction(self._conn):
            self._conn.execute(
                "INSERT OR IGNORE INTO full_scan_entitlements "
                "(entitlement_id, tenant, pack_id, max_cost_usd, source, "
                " external_ref, consumed, granted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                (entitlement_id, tenant, pack_id, max_cost_usd, source,
                 external_ref, now),
            )
        # Return the canonical row (may have been INSERT OR IGNORE-d if there's
        # a race on the unique index).
        cur = self._conn.execute(
            "SELECT * FROM full_scan_entitlements WHERE entitlement_id=?",
            (entitlement_id,),
        )
        row = cur.fetchone()
        if row is None:
            # Raced — return the existing row.
            if external_ref is not None:
                cur = self._conn.execute(
                    "SELECT * FROM full_scan_entitlements "
                    "WHERE tenant=? AND pack_id=? AND external_ref=?",
                    (tenant, pack_id, external_ref),
                )
                row = cur.fetchone()
        return dict(row) if row else {}

    def consume(
        self,
        *,
        tenant: str,
        pack_id: str,
        job_id: str,
        external_ref: str | None = None,
    ) -> dict[str, Any] | None:
        """Claim the first unconsumed entitlement for (tenant, pack_id).

        Returns the consumed record, or ``None`` if no unconsumed entitlement
        exists.  Idempotent when ``external_ref`` matches an already-consumed
        record for this job.
        """
        now = _utcnow()
        with _transaction(self._conn):
            # Idempotent: already consumed by this job?
            if external_ref is not None:
                cur = self._conn.execute(
                    "SELECT * FROM full_scan_entitlements "
                    "WHERE tenant=? AND pack_id=? AND external_ref=? "
                    "  AND consumed=1 AND consumed_job_id=?",
                    (tenant, pack_id, external_ref, job_id),
                )
                row = cur.fetchone()
                if row is not None:
                    return dict(row)

            # Find the first unconsumed entitlement.
            cur = self._conn.execute(
                "SELECT entitlement_id FROM full_scan_entitlements "
                "WHERE tenant=? AND pack_id=? AND consumed=0 "
                "ORDER BY granted_at ASC LIMIT 1",
                (tenant, pack_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            eid = row["entitlement_id"]
            self._conn.execute(
                "UPDATE full_scan_entitlements "
                "SET consumed=1, consumed_job_id=?, consumed_at=? "
                "WHERE entitlement_id=?",
                (job_id, now, eid),
            )

        cur = self._conn.execute(
            "SELECT * FROM full_scan_entitlements WHERE entitlement_id=?",
            (eid,),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def check(self, *, tenant: str, pack_id: str) -> bool:
        """Return True iff at least one unconsumed entitlement exists."""
        cur = self._conn.execute(
            "SELECT 1 FROM full_scan_entitlements "
            "WHERE tenant=? AND pack_id=? AND consumed=0 LIMIT 1",
            (tenant, pack_id),
        )
        return cur.fetchone() is not None

    def list_entitlements(self, *, tenant: str) -> list[dict[str, Any]]:
        """Return all entitlement records for a tenant (for ops inspection)."""
        cur = self._conn.execute(
            "SELECT * FROM full_scan_entitlements WHERE tenant=? "
            "ORDER BY granted_at ASC",
            (tenant,),
        )
        return [dict(row) for row in cur.fetchall()]
