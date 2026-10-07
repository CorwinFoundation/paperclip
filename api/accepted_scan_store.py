"""Durable record of accepted scans so a scanner restart does not lose them.

BEAAA-38869 item 2. Every master merge redeploys and restarts the scanner
container. Before this module the API edge kept accepted scans only in
``ScanRuntime`` memory, so a scan running during a deploy vanished: no result,
no terminal webhook, and the public intake's report never filled in.

This store keeps, per accepted ``scan_id``:

* the validated ``ScanRequest`` (never the credential secret; the endpoint
  ``auth`` block is reduced to its ``type``/``header_name``),
* the caller-facing context needed to finish the scan after a restart
  (``agent_id``, invocation context, webhook url + ``secret_ref``),
* the terminal ``ScanResult`` projection, so ``GET /scan/{scan_id}`` and the
  terminal webhook can be served after the in-memory state is gone,
* the terminal-webhook outbox records (:class:`SqliteOutboxStore`), so a
  delivery that was pending when the process died is retried on boot and a
  delivered one is never re-sent.

Credentials are deliberately NOT persisted (Hard Invariant #4 and BEAAA-38865:
a SIGKILL-then-restart must leave no auth material behind). A scan whose
endpoint needs a credential therefore cannot be re-run after a restart; it is
finished with an explicit terminal ``failed`` result and its webhook fires. A
scan against an ``auth: none`` endpoint carries no credential and is re-run.

Rows are deleted ``retention_seconds`` after acceptance (default 180 days, the
public intake's report-link lifetime). ``secure_delete`` zeroes purged pages.

Storage discipline mirrors :mod:`api.full_scan_job_persistence`: one
connection, ``check_same_thread=False``, ``BEGIN IMMEDIATE`` per write, and a
process-local lock serialising writers.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from contextlib import contextmanager

from scanners.canary_anti_gaming import AntiGamingCanaryResult
from scanners.live_probe_orchestrator import DimResult, ScanRequest, ScanResult

#: Default retention: the public intake's report link lives 180 days
#: (BEAAA-38862 item 9), so the scanner keeps the result at least that long.
DEFAULT_RETENTION_DAYS: float = 180.0

STATUS_ACCEPTED = "accepted"
STATUS_RUNNING = "running"
STATUS_TERMINAL = "terminal"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accepted_scans (
    scan_id            TEXT PRIMARY KEY,
    tenant_id          TEXT NOT NULL,
    state              TEXT NOT NULL,
    accepted_at        REAL NOT NULL,
    updated_at         REAL NOT NULL,
    resumable          INTEGER NOT NULL,
    resume_count       INTEGER NOT NULL DEFAULT 0,
    request_json       TEXT NOT NULL,
    context_json       TEXT NOT NULL,
    kickoff_key_json   TEXT,
    result_json        TEXT
);
CREATE INDEX IF NOT EXISTS ix_accepted_scans_state ON accepted_scans (state);
CREATE INDEX IF NOT EXISTS ix_accepted_scans_accepted_at ON accepted_scans (accepted_at);

CREATE TABLE IF NOT EXISTS webhook_outbox (
    idempotency_key    TEXT PRIMARY KEY,
    status             TEXT NOT NULL,
    created_at         REAL NOT NULL,
    record_json        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_webhook_outbox_status ON webhook_outbox (status);
"""


def _sanitized_endpoint_config(config: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if config is None:
        return None
    clean = dict(config)
    auth = clean.get("auth")
    if isinstance(auth, Mapping):
        clean["auth"] = {
            key: auth[key] for key in ("type", "header_name") if key in auth
        }
    return clean


def credential_free(scan_request: ScanRequest) -> bool:
    """True when the endpoint takes no credential, so a re-run needs none."""
    config = scan_request.endpoint_config or {}
    auth = config.get("auth") if isinstance(config, Mapping) else None
    return isinstance(auth, Mapping) and auth.get("type") == "none"


def request_to_record(scan_request: ScanRequest) -> dict[str, Any]:
    record = asdict(scan_request)
    record["endpoint_config"] = _sanitized_endpoint_config(scan_request.endpoint_config)
    return record


def request_from_record(record: Mapping[str, Any]) -> ScanRequest:
    known = {f.name for f in fields(ScanRequest)}
    kwargs = {key: value for key, value in record.items() if key in known}
    for key in ("requested_pillars", "canary_phrasing_hashes"):
        if key in kwargs and kwargs[key] is not None:
            kwargs[key] = tuple(kwargs[key])
    return ScanRequest(**kwargs)


def result_to_record(result: ScanResult) -> dict[str, Any]:
    """Serialise what GET /scan and the webhook read. Evidence records are
    owned by the bundle store and are not duplicated here."""
    return {
        "scan_id": result.scan_id,
        "status": result.status,
        "scanner_version": result.scanner_version,
        "corpus_version": result.corpus_version,
        "rows_emitted": result.rows_emitted,
        "k_replicates": result.k_replicates,
        "live_probe_variance_band": result.live_probe_variance_band,
        "dim_results": [
            {
                "dim_id": d.dim_id,
                "raw": d.raw,
                "spread": d.spread,
                "replicates": list(d.replicates),
            }
            for d in result.dim_results
        ],
        "spread_per_dim": dict(result.spread_per_dim),
        "pillar_spread": dict(result.pillar_spread),
        "total_spread": result.total_spread,
        "incomplete": result.incomplete,
        "incomplete_reason": result.incomplete_reason,
        "full_suite_manifest": (
            dict(result.full_suite_manifest)
            if result.full_suite_manifest is not None
            else None
        ),
        "starter_result_rows": [dict(row) for row in result.starter_result_rows],
        "anti_gaming": result.anti_gaming.to_dict(),
    }


def result_from_record(record: Mapping[str, Any]) -> ScanResult:
    anti_gaming = record.get("anti_gaming") or {}
    return ScanResult(
        scan_id=record["scan_id"],
        status=record["status"],
        scanner_version=record["scanner_version"],
        corpus_version=record["corpus_version"],
        rows_emitted=int(record.get("rows_emitted") or 0),
        k_replicates=record["k_replicates"],
        live_probe_variance_band=record["live_probe_variance_band"],
        dim_results=tuple(
            DimResult(
                dim_id=d["dim_id"],
                raw=d["raw"],
                spread=d["spread"],
                replicates=tuple(d["replicates"]),
            )
            for d in record.get("dim_results") or ()
        ),
        spread_per_dim=dict(record.get("spread_per_dim") or {}),
        pillar_spread=dict(record.get("pillar_spread") or {}),
        total_spread=record.get("total_spread") or 0.0,
        incomplete=bool(record.get("incomplete")),
        incomplete_reason=record.get("incomplete_reason"),
        full_suite_manifest=record.get("full_suite_manifest"),
        starter_result_rows=tuple(record.get("starter_result_rows") or ()),
        anti_gaming=AntiGamingCanaryResult(**anti_gaming),
    )


class AcceptedScanStore:
    """SQLite-backed accepted-scan journal + durable webhook outbox."""

    def __init__(
        self,
        path: str | Path,
        *,
        retention_seconds: float = DEFAULT_RETENTION_DAYS * 86400,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._retention_seconds = float(retention_seconds)
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self._path, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA secure_delete=ON")
        if self._path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(_SCHEMA_SQL)

    @property
    def path(self) -> str:
        return self._path

    @property
    def retention_seconds(self) -> float:
        return self._retention_seconds

    # -- accepted scans -----------------------------------------------------

    def accept(
        self,
        scan_request: ScanRequest,
        *,
        agent_id: str | None,
        invocation_context: Mapping[str, Any] | None,
        webhook_cfg: Mapping[str, str] | None,
    ) -> None:
        now = self._clock()
        context = {
            "agent_id": agent_id,
            "invocation_context": (
                dict(invocation_context) if invocation_context is not None else None
            ),
            "webhook_cfg": dict(webhook_cfg) if webhook_cfg is not None else None,
        }
        with self._write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO accepted_scans (scan_id, tenant_id, state, "
                "accepted_at, updated_at, resumable, resume_count, request_json, "
                "context_json) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    scan_request.scan_id,
                    scan_request.tenant_id,
                    STATUS_ACCEPTED,
                    now,
                    now,
                    1 if credential_free(scan_request) else 0,
                    json.dumps(request_to_record(scan_request), sort_keys=True),
                    json.dumps(context, sort_keys=True),
                ),
            )
        self.purge_expired()

    def mark_running(self, scan_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE accepted_scans SET state=?, updated_at=? "
                "WHERE scan_id=? AND state!=?",
                (STATUS_RUNNING, self._clock(), scan_id, STATUS_TERMINAL),
            )

    def note_resume(self, scan_id: str) -> int:
        with self._write() as conn:
            conn.execute(
                "UPDATE accepted_scans SET resume_count=resume_count+1, updated_at=? "
                "WHERE scan_id=?",
                (self._clock(), scan_id),
            )
            row = conn.execute(
                "SELECT resume_count FROM accepted_scans WHERE scan_id=?", (scan_id,)
            ).fetchone()
        return int(row["resume_count"]) if row else 0

    def mark_terminal(
        self,
        scan_id: str,
        result: ScanResult,
        *,
        kickoff_key: tuple[str, str] | None = None,
    ) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE accepted_scans SET state=?, updated_at=?, result_json=?, "
                "kickoff_key_json=COALESCE(?, kickoff_key_json) WHERE scan_id=?",
                (
                    STATUS_TERMINAL,
                    self._clock(),
                    json.dumps(result_to_record(result), sort_keys=True),
                    json.dumps(list(kickoff_key)) if kickoff_key else None,
                    scan_id,
                ),
            )

    def get(self, scan_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accepted_scans WHERE scan_id=?", (scan_id,)
            ).fetchone()
        if row is None:
            return None
        if row["accepted_at"] < self._clock() - self._retention_seconds:
            return None
        return _decode(row)

    def unfinished(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM accepted_scans WHERE state!=? ORDER BY accepted_at, scan_id",
                (STATUS_TERMINAL,),
            ).fetchall()
        return [_decode(row) for row in rows]

    def terminal_with_webhook(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM accepted_scans WHERE state=? ORDER BY updated_at, scan_id",
                (STATUS_TERMINAL,),
            ).fetchall()
        decoded = [_decode(row) for row in rows]
        return [row for row in decoded if row["webhook_cfg"] is not None]

    def purge_expired(self) -> int:
        """Delete terminal scans and settled outbox rows past retention."""
        cutoff = self._clock() - self._retention_seconds
        with self._write() as conn:
            deleted = conn.execute(
                "DELETE FROM accepted_scans WHERE accepted_at < ?", (cutoff,)
            ).rowcount
            conn.execute(
                "DELETE FROM webhook_outbox WHERE created_at < ?", (cutoff,)
            )
        return int(deleted or 0)

    # -- webhook outbox -----------------------------------------------------

    def outbox_store(self) -> "SqliteOutboxStore":
        return SqliteOutboxStore(self)

    def _outbox_get(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT record_json FROM webhook_outbox WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        return json.loads(row["record_json"]) if row else None

    def _outbox_put(self, idempotency_key: str, status: str, record: dict[str, Any]) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO webhook_outbox (idempotency_key, status, created_at, record_json) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(idempotency_key) DO UPDATE SET "
                "status=excluded.status, record_json=excluded.record_json",
                (idempotency_key, status, self._clock(), json.dumps(record, sort_keys=True)),
            )

    def _outbox_pending(self, status: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT record_json FROM webhook_outbox WHERE status=? "
                "ORDER BY created_at, idempotency_key",
                (status,),
            ).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    # -- plumbing -----------------------------------------------------------

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    context = json.loads(row["context_json"])
    return {
        "scan_id": row["scan_id"],
        "tenant_id": row["tenant_id"],
        "state": row["state"],
        "accepted_at": row["accepted_at"],
        "resumable": bool(row["resumable"]),
        "resume_count": int(row["resume_count"]),
        "request": request_from_record(json.loads(row["request_json"])),
        "agent_id": context.get("agent_id"),
        "invocation_context": context.get("invocation_context"),
        "webhook_cfg": context.get("webhook_cfg"),
        "kickoff_key": (
            tuple(json.loads(row["kickoff_key_json"]))
            if row["kickoff_key_json"]
            else None
        ),
        "result": (
            result_from_record(json.loads(row["result_json"]))
            if row["result_json"]
            else None
        ),
    }


class SqliteOutboxStore:
    """Durable :class:`api.webhook_outbox.OutboxStore` sharing the scan DB."""

    def __init__(self, store: AcceptedScanStore) -> None:
        self._store = store

    def get(self, idempotency_key: str):  # -> OutboxRecord | None
        record = self._store._outbox_get(idempotency_key)
        return _outbox_record_from_json(record) if record is not None else None

    def put(self, record) -> None:  # record: OutboxRecord
        self._store._outbox_put(
            record.idempotency_key, record.status, _outbox_record_to_json(record)
        )

    def pending(self) -> list:
        from api.webhook_outbox import STATUS_PENDING

        return [
            _outbox_record_from_json(record)
            for record in self._store._outbox_pending(STATUS_PENDING)
        ]


def _outbox_record_to_json(record) -> dict[str, Any]:
    return {
        "idempotency_key": record.idempotency_key,
        "url": record.url,
        "secret_ref": record.secret_ref,
        "payload": record.payload,
        "delivery_id": record.delivery_id,
        "status": record.status,
        "attempts": record.attempts,
        "last_error": record.last_error,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _outbox_record_from_json(data: Mapping[str, Any]):
    from api.webhook_outbox import OutboxRecord

    return OutboxRecord(
        idempotency_key=data["idempotency_key"],
        url=data["url"],
        secret_ref=data["secret_ref"],
        payload=dict(data["payload"]),
        delivery_id=data["delivery_id"],
        status=data["status"],
        attempts=int(data.get("attempts") or 0),
        last_error=data.get("last_error"),
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
    )


__all__ = [
    "AcceptedScanStore",
    "DEFAULT_RETENTION_DAYS",
    "SqliteOutboxStore",
    "credential_free",
    "request_from_record",
    "request_to_record",
    "result_from_record",
    "result_to_record",
]
