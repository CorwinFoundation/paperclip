"""Paid Full Scan pack entitlements (BEAAA-38957).

A paid-pack job is accepted only against an unconsumed entitlement for the same
``(tenant_id, pack_id)``. One entitlement pays for exactly one job, capped at the
entitlement's ``max_cost_usd``.

Scope: the only grant source is ``manual`` (an operator records a grant by
hand). No payment provider is wired and nothing here charges anyone; a
provider adapter is a separate, later change.

Storage: the same SQLite file as :mod:`api.full_scan_job_persistence` (pass the
same path), its own ``full_scan_entitlements`` table, same write discipline
(``BEGIN IMMEDIATE`` per write, additive schema under the sidecar migration
lock). ``grant`` is idempotent on ``(source, external_ref)``.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from api.full_scan_job_persistence import _schema_migration_lock, _transaction


ENTITLEMENT_SOURCES = frozenset({"manual"})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS full_scan_entitlements (
    entitlement_id  TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    pack_id         TEXT NOT NULL,
    max_cost_usd    REAL NOT NULL,
    source          TEXT NOT NULL,
    external_ref    TEXT NOT NULL,
    granted_at      TEXT NOT NULL,
    consumed_job_id TEXT,
    consumed_at     TEXT,
    UNIQUE (source, external_ref)
);

CREATE INDEX IF NOT EXISTS ix_full_scan_entitlements_tenant_pack
    ON full_scan_entitlements (tenant_id, pack_id, consumed_job_id);
"""


class EntitlementError(ValueError):
    pass


@dataclass(frozen=True)
class Entitlement:
    entitlement_id: str
    tenant_id: str
    pack_id: str
    max_cost_usd: float
    source: str
    external_ref: str
    granted_at: str
    consumed_job_id: str | None = None
    consumed_at: str | None = None

    @property
    def consumed(self) -> bool:
        return self.consumed_job_id is not None

    def __getitem__(self, key: str):
        if key == "consumed":
            return int(self.consumed)
        return getattr(self, key)


class FullScanEntitlementStore:
    """SQLite-backed entitlement ledger shared with the job store file."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        self._conn = sqlite3.connect(
            self._path, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with _schema_migration_lock(self._path):
            self._conn.executescript(_SCHEMA_SQL)

    def grant(
        self,
        *,
        tenant_id: str | None = None,
        tenant: str | None = None,
        pack_id: str,
        max_cost_usd: float,
        source: str = "manual",
        external_ref: str | None = None,
    ) -> Entitlement:
        """Record a grant; replaying the same ``(source, external_ref)`` is a no-op.

        A replay that disagrees with the stored grant (other tenant, pack or
        cap) is refused rather than silently returning a different grant.
        """
        legacy_call = tenant_id is None and tenant is not None
        tenant_id = tenant_id or tenant
        if not legacy_call:
            from api.full_scan_packs import resolve_pack
            resolve_pack(pack_id)  # unknown fixed paid pack -> executor error
        if source not in ENTITLEMENT_SOURCES:
            raise EntitlementError(f"unsupported entitlement source: {source!r}")
        if not tenant_id:
            raise EntitlementError("tenant_id is required")
        external_ref = external_ref or str(uuid.uuid4())
        cap = float(max_cost_usd)
        if not cap > 0:
            raise EntitlementError("max_cost_usd must be positive")
        with _transaction(self._conn):
            existing = self._conn.execute(
                "SELECT * FROM full_scan_entitlements WHERE source = ? AND external_ref = ?",
                (source, external_ref),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    "INSERT INTO full_scan_entitlements (entitlement_id, tenant_id, "
                    "pack_id, max_cost_usd, source, external_ref, granted_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        tenant_id,
                        pack_id,
                        cap,
                        source,
                        external_ref,
                        _now(),
                    ),
                )
                existing = self._conn.execute(
                    "SELECT * FROM full_scan_entitlements WHERE source = ? AND external_ref = ?",
                    (source, external_ref),
                ).fetchone()
        entitlement = _from_row(existing)
        if (
            entitlement.tenant_id != tenant_id
            or entitlement.pack_id != pack_id
            or entitlement.max_cost_usd != cap
        ):
            raise EntitlementError(
                "external_ref already granted with different terms"
            )
        return entitlement

    def check(
        self, *, tenant_id: str | None = None, tenant: str | None = None,
        pack_id: str,
    ) -> Entitlement | None:
        """Oldest unconsumed entitlement for ``(tenant_id, pack_id)``, or ``None``."""
        tenant_id = tenant_id or tenant or ""
        row = self._conn.execute(
            "SELECT * FROM full_scan_entitlements WHERE tenant_id = ? AND pack_id = ? "
            "AND consumed_job_id IS NULL ORDER BY granted_at, entitlement_id LIMIT 1",
            (tenant_id, pack_id),
        ).fetchone()
        return _from_row(row) if row is not None else None

    def consume(
        self,
        *,
        tenant_id: str | None = None,
        tenant: str | None = None,
        pack_id: str,
        job_id: str,
        entitlement_id: str | None = None,
        external_ref: str | None = None,
    ) -> Entitlement:
        """Bind one unconsumed entitlement to ``job_id``.

        Idempotent per ``job_id``: consuming again for the same job returns the
        entitlement already bound to it. Raises when none is available.
        """
        tenant_id = tenant_id or tenant or ""
        with _transaction(self._conn):
            bound = self._conn.execute(
                "SELECT * FROM full_scan_entitlements WHERE consumed_job_id = ?",
                (job_id,),
            ).fetchone()
            if bound is not None:
                return _from_row(bound)
            query = (
                "SELECT * FROM full_scan_entitlements WHERE tenant_id = ? AND pack_id = ? "
                "AND consumed_job_id IS NULL"
            )
            params: tuple = (tenant_id, pack_id)
            if entitlement_id is not None:
                query += " AND entitlement_id = ?"
                params += (entitlement_id,)
            row = self._conn.execute(
                query + " ORDER BY granted_at, entitlement_id LIMIT 1", params
            ).fetchone()
            if row is None:
                raise EntitlementError("no unconsumed entitlement")
            self._conn.execute(
                "UPDATE full_scan_entitlements SET consumed_job_id = ?, consumed_at = ? "
                "WHERE entitlement_id = ? AND consumed_job_id IS NULL",
                (job_id, _now(), row["entitlement_id"]),
            )
            updated = self._conn.execute(
                "SELECT * FROM full_scan_entitlements WHERE entitlement_id = ?",
                (row["entitlement_id"],),
            ).fetchone()
        return _from_row(updated)

    def get(self, entitlement_id: str) -> Entitlement | None:
        row = self._conn.execute(
            "SELECT * FROM full_scan_entitlements WHERE entitlement_id = ?",
            (entitlement_id,),
        ).fetchone()
        return _from_row(row) if row is not None else None

    def close(self) -> None:
        self._conn.close()


def _from_row(row: sqlite3.Row) -> Entitlement:
    return Entitlement(
        entitlement_id=row["entitlement_id"],
        tenant_id=row["tenant_id"],
        pack_id=row["pack_id"],
        max_cost_usd=float(row["max_cost_usd"]),
        source=row["source"],
        external_ref=row["external_ref"],
        granted_at=row["granted_at"],
        consumed_job_id=row["consumed_job_id"],
        consumed_at=row["consumed_at"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "ENTITLEMENT_SOURCES",
    "Entitlement",
    "EntitlementError",
    "FullScanEntitlementStore",
]
