"""Internal request-to-scan provenance index for vendor audits.

BEAAA-18866 is rule-execution under Scanner API contract v1.1.0. This module
adds no public listing surface and does not participate in score calculation.
It persists a write-once tenant/request reference to ``scan_id`` mapping and
projects an allowlisted audit view from the authoritative scan record.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable, Mapping


class ProvenanceConflict(RuntimeError):
    """The request reference is already bound to a different scan."""


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS request_scan_provenance (
    tenant_id       TEXT NOT NULL,
    source_issue_id TEXT NOT NULL,
    request_id      TEXT NOT NULL,
    scan_id         TEXT NOT NULL,
    PRIMARY KEY (tenant_id, source_issue_id, request_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_request_scan_provenance_scan
    ON request_scan_provenance (tenant_id, scan_id);
"""


class RequestScanProvenanceIndex:
    """SQLite-backed, tenant-scoped, write-once provenance mapping."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(
            str(path), isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)

    def bind(
        self,
        *,
        tenant_id: str,
        source_issue_id: str,
        request_id: str,
        scan_id: str,
    ) -> None:
        values = (tenant_id, source_issue_id, request_id, scan_id)
        if not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError("provenance_fields_required")
        try:
            self._conn.execute(
                "INSERT INTO request_scan_provenance "
                "(tenant_id, source_issue_id, request_id, scan_id) "
                "VALUES (?, ?, ?, ?)",
                values,
            )
        except sqlite3.IntegrityError as exc:
            existing = self.resolve_scan_id(
                tenant_id=tenant_id,
                source_issue_id=source_issue_id,
                request_id=request_id,
            )
            if existing == scan_id:
                return
            raise ProvenanceConflict("request_reference_already_bound") from exc

    def resolve_scan_id(
        self,
        *,
        tenant_id: str | None,
        source_issue_id: str,
        request_id: str,
    ) -> str | None:
        if not tenant_id:
            return None
        row = self._conn.execute(
            "SELECT scan_id FROM request_scan_provenance "
            "WHERE tenant_id = ? AND source_issue_id = ? AND request_id = ?",
            (tenant_id, source_issue_id, request_id),
        ).fetchone()
        return str(row["scan_id"]) if row is not None else None

    def lookup(
        self,
        *,
        tenant_id: str | None,
        source_issue_id: str,
        request_id: str,
        scan_loader: Callable[[str], Mapping[str, Any] | None],
    ) -> dict[str, Any] | None:
        scan_id = self.resolve_scan_id(
            tenant_id=tenant_id,
            source_issue_id=source_issue_id,
            request_id=request_id,
        )
        if scan_id is None:
            return None
        record = scan_loader(scan_id)
        if record is None:
            return None
        return {
            "scan_id": scan_id,
            "status": record.get("status"),
            "terminal_at": record.get("terminal_at") or record.get("run_timestamp"),
            "backbond_score": record.get("are_total"),
            "band": record.get("band"),
            "scanner_version": record.get("scanner_version"),
            "corpus_version": record.get("corpus_version"),
            "corpus_snapshot_hash": (
                record.get("corpus_snapshot_hash") or record.get("snapshot_hash")
            ),
            "model_versions": record.get("model_versions"),
            "seed": record.get("seed"),
            "evidence_refs": list(record.get("evidence_refs") or ()),
        }

    def close(self) -> None:
        self._conn.close()


__all__ = [
    "ProvenanceConflict",
    "RequestScanProvenanceIndex",
]
