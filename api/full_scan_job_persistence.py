"""Durable persistence backend for :class:`api.full_scan_executor.FullScanJobStore`.

BEAAA-14401 (P1#5 fix 4 of 4) — rule-execution scope. Accepted full-scan jobs
were owned only by in-memory state (`FullScanJobStore._jobs` + daemon threads);
a process restart erased every queued/running job. This module adds a
SQLite-backed store for the job records + idempotency index so an accepted job
survives a restart and can either resume or reach an explicit terminal failure.

It changes no rubric weight, no scoring math, no judge selection, and no
byte-locked BackBond Scanner API v1.0 score envelope. The persisted job record
is the same ``FullScanJob`` state the in-memory store already produced —
serialized, not re-derived. All business logic (lease/checkpoint/finalize/
recovery) stays in ``FullScanJobStore``; this layer only serializes and stores.

Storage discipline mirrors ``api/scan_log.py``: a single connection opened with
``check_same_thread=False`` and ``isolation_level=None`` (writes serialized by
the store's ``RLock`` and per-write ``BEGIN IMMEDIATE``), and a sidecar advisory
lock around idempotent additive schema installation. Unlike ``scan_log`` (which
is append-only), a job record is mutable state, so writes are ``INSERT OR
REPLACE`` keyed by ``job_id``.
"""
from __future__ import annotations

import fcntl
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS full_scan_jobs (
    job_id       TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    updated_at   TEXT,
    job_json     TEXT NOT NULL
);

-- Recovery sweep scans by status to find non-terminal jobs after a restart.
CREATE INDEX IF NOT EXISTS ix_full_scan_jobs_status
    ON full_scan_jobs (status);

CREATE TABLE IF NOT EXISTS full_scan_idempotency (
    idempotency_key TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL
);
"""


class FullScanJobPersistence:
    """SQLite-backed durable store for full-scan job records.

    Pass ``":memory:"`` for a transient store (each connection is its own
    database — not shared across processes) or a filesystem path for durable
    storage that survives a process restart. The schema is installed
    idempotently on connect.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        self._conn = sqlite3.connect(
            self._path, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with _schema_migration_lock(self._path):
            self._conn.executescript(_SCHEMA_SQL)

    # ------------------------------------------------------------------
    # Write path — job record + idempotency index.
    # ------------------------------------------------------------------

    def save(self, *, job_id: str, status: str, updated_at: str | None, record: dict[str, Any]) -> None:
        """Upsert a job record. ``record`` is the serialized ``FullScanJob``."""
        with _transaction(self._conn):
            self._conn.execute(
                "INSERT OR REPLACE INTO full_scan_jobs "
                "(job_id, status, updated_at, job_json) VALUES (?, ?, ?, ?)",
                (job_id, status, updated_at, json.dumps(record, sort_keys=True)),
            )

    def save_accepted_job(
        self,
        *,
        job_id: str,
        status: str,
        updated_at: str | None,
        record: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> None:
        """Atomically persist a newly accepted job and optional idempotency key."""
        with _transaction(self._conn):
            self._conn.execute(
                "INSERT OR REPLACE INTO full_scan_jobs "
                "(job_id, status, updated_at, job_json) VALUES (?, ?, ?, ?)",
                (job_id, status, updated_at, json.dumps(record, sort_keys=True)),
            )
            if idempotency_key:
                self._conn.execute(
                    "INSERT OR IGNORE INTO full_scan_idempotency "
                    "(idempotency_key, job_id) VALUES (?, ?)",
                    (idempotency_key, job_id),
                )

    def save_idempotency(self, *, idempotency_key: str, job_id: str) -> None:
        """Record the idempotency-key → job_id mapping (write-once by key)."""
        with _transaction(self._conn):
            self._conn.execute(
                "INSERT OR IGNORE INTO full_scan_idempotency "
                "(idempotency_key, job_id) VALUES (?, ?)",
                (idempotency_key, job_id),
            )

    # ------------------------------------------------------------------
    # Read path — used by ``FullScanJobStore`` on construction / recovery.
    # ------------------------------------------------------------------

    def load_jobs(self) -> list[dict[str, Any]]:
        """Return every persisted job record (deserialized JSON payloads)."""
        cur = self._conn.execute("SELECT job_json FROM full_scan_jobs")
        return [json.loads(row["job_json"]) for row in cur.fetchall()]

    def load_idempotency(self) -> dict[str, str]:
        """Return the persisted idempotency-key → job_id index."""
        cur = self._conn.execute(
            "SELECT idempotency_key, job_id FROM full_scan_idempotency"
        )
        return {row["idempotency_key"]: row["job_id"] for row in cur.fetchall()}

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "FullScanJobPersistence":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Helpers (mirrors api/scan_log.py).
# ---------------------------------------------------------------------------


@contextmanager
def _schema_migration_lock(path: str) -> Iterator[None]:
    """Serialize filesystem-backed additive schema installation across procs."""
    if path == ":memory:":
        yield
        return
    db_path = Path(path)
    lock_path = db_path.with_name(db_path.name + ".migrations.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Wrap a write in BEGIN IMMEDIATE / COMMIT / ROLLBACK."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


__all__ = ["FullScanJobPersistence"]
