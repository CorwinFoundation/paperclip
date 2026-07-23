"""Scan log persistence — write-once `forensic_bundle_uri` + at-write-time
`snapshot_hash` (BEAAA-1271 / ADR-0009 §4).

Storage-layer discipline for the chain-of-custody trail. Keyed
``(agent_id, run_timestamp)``; indexed for the WS3 chain reconstruction
lookup; append-only (no row delete; only NULL-field updates permitted on
non-pinned fields).

Invariants enforced at the DB layer (sqlite triggers + WHERE-guarded
UPDATEs):

  4.1  ``forensic_bundle_uri`` is **write-once** per row. A second
       UPDATE attempt against a non-NULL value raises
       ``ForensicBundleWriteOnce``. The API edge surfaces this as
       HTTP 409 with reason ``forensic_bundle_uri_write_once``.

  4.2  ``snapshot_hash`` is the ``canonical_sha256`` of the
       RFC 8785-canonicalized ``score.json`` envelope, computed by the
       scanner post-envelope-finalization, pre-persistence, in the same
       write transaction as ``forensic_bundle_uri``. The hash is
       **never** recomputed by a later read path; a consumer verifies
       integrity by re-canonicalizing the stored bundle bytes and
       comparing against the persisted field.

  4   Append-only. No row delete. UPDATEs on the two pinned fields are
       only legal when the existing value is NULL — the initial post-
       finalize write fixes both pins together in one transaction.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from api.jcs import canonicalize


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scan_log (
    agent_id              TEXT     NOT NULL,
    run_timestamp         TEXT     NOT NULL,
    scan_id               TEXT     NOT NULL,
    scanner_version       TEXT     NOT NULL,
    corpus_version        TEXT,
    score_json            TEXT     NOT NULL,
    forensic_bundle_uri   TEXT,
    snapshot_hash         TEXT,
    probe_set_hash        TEXT,
    PRIMARY KEY (agent_id, run_timestamp)
);

-- WS3 chain reconstruction lookup: ordered prior-scan retrieval by agent.
CREATE INDEX IF NOT EXISTS ix_scan_log_agent_ts
    ON scan_log (agent_id, run_timestamp);

-- ADR-0009 §4.1: forensic_bundle_uri is write-once. A second UPDATE on a
-- non-NULL value raises a CHECK violation that the API edge maps to 409.
CREATE TRIGGER IF NOT EXISTS trg_scan_log_forensic_bundle_uri_write_once
BEFORE UPDATE OF forensic_bundle_uri ON scan_log
FOR EACH ROW
WHEN OLD.forensic_bundle_uri IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'forensic_bundle_uri_write_once');
END;

-- ADR-0009 §4.2: snapshot_hash is write-once for the same reason — it is
-- the at-write-time pin and must not be recomputed by a later path.
CREATE TRIGGER IF NOT EXISTS trg_scan_log_snapshot_hash_write_once
BEFORE UPDATE OF snapshot_hash ON scan_log
FOR EACH ROW
WHEN OLD.snapshot_hash IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'snapshot_hash_write_once');
END;

-- BEAAA-11726 item 4: probe_set_hash is write-once (same discipline as
-- snapshot_hash — identifies the exact probe subset that ran; critical for
-- smoke tier where coverage varies by version).
CREATE TRIGGER IF NOT EXISTS trg_scan_log_probe_set_hash_write_once
BEFORE UPDATE OF probe_set_hash ON scan_log
FOR EACH ROW
WHEN OLD.probe_set_hash IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'probe_set_hash_write_once');
END;

-- ADR-0009 §4: append-only. No row delete on a row that has been pinned.
CREATE TRIGGER IF NOT EXISTS trg_scan_log_append_only
BEFORE DELETE ON scan_log
WHEN OLD.forensic_bundle_uri IS NOT NULL OR OLD.snapshot_hash IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'scan_log_append_only');
END;
"""


# Additive, nullable columns that were introduced after the initial
# ``scan_log`` schema shipped. ``CREATE TABLE IF NOT EXISTS`` never alters
# an existing table, so a database created before one of these columns was
# added is silently missing it — and the first ``append_scan`` INSERT that
# names the column fails with ``OperationalError: table scan_log has no
# column named ...`` (this bit the 2026-07-09/10 production cutover:
# BEAAA-12895). Every entry here is applied idempotently on connect via
# ``ScanLog._migrate`` so a deploy onto an existing DB self-migrates.
#
# CONTRACT: additive-only. Columns must be nullable (no ``NOT NULL`` /
# ``DEFAULT`` backfill that would rewrite historical rows) so the migration
# never mutates a previously-pinned audit row — consistent with ADR-0009 §4.
# To retire a column you add a new one; you never drop.
_ADDITIVE_COLUMNS: tuple[tuple[str, str], ...] = (
    # BEAAA-11726 item 4 — probe subset identity, write-once (trigger in
    # _SCHEMA_SQL). Present in fresh-DB DDL above; migrated in for older DBs.
    ("probe_set_hash", "TEXT"),
)


class ScanLogError(Exception):
    """Base class for scan_log invariant violations."""


class ForensicBundleWriteOnce(ScanLogError):
    """Attempt to overwrite a previously-pinned ``forensic_bundle_uri``.

    The API edge maps this to HTTP 409 Conflict with reason
    ``forensic_bundle_uri_write_once`` per AC #3 of BEAAA-1271.
    """


class SnapshotHashWriteOnce(ScanLogError):
    """Attempt to overwrite a previously-pinned ``snapshot_hash``."""


class ProbeSetHashWriteOnce(ScanLogError):
    """Attempt to overwrite a previously-pinned ``probe_set_hash``.

    BEAAA-11726 item 4 — the probe set that ran is write-once per the same
    ADR-0009 §4 discipline as ``snapshot_hash``.
    """


class ScanLogAppendOnly(ScanLogError):
    """Attempt to delete a pinned scan_log row."""


class ScanRowNotFound(ScanLogError):
    """Attempt to update a row that does not exist."""


def compute_snapshot_hash(score_json: dict[str, Any]) -> str:
    """Return ``canonical_sha256(jcs(score_json))`` per ADR-0009 §4.2.

    The returned string matches the ``sha256:<64-hex>`` format used
    elsewhere in the contract (api/openapi.yaml snapshot_hash format).
    """
    digest = hashlib.sha256(canonicalize(score_json)).hexdigest()
    return f"sha256:{digest}"


class ScanLog:
    """Sqlite-backed scan log with ADR-0009 §4 invariants.

    Pass ``":memory:"`` (default) for tests; a filesystem path for
    durable storage. The schema, indexes, and triggers are idempotently
    installed on connection — re-opening an existing database does not
    re-create them.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        self._write_lock = threading.RLock()
        # ``check_same_thread=False`` enables the BEAAA-1273 (WS3) API-edge
        # wiring where FastAPI's threadpool may dispatch a request handler
        # on a worker thread distinct from the connection-creating one.
        # The application-level RLock serializes every access to this shared
        # connection; sqlite transaction state belongs to the connection, not
        # the calling thread. DB-layer write-once invariants remain defense in
        # depth (ADR-0009 §4.1 / §4.2).
        self._conn = sqlite3.connect(
            self._path, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        # Forward-migrate BEFORE the schema/trigger script: a pre-existing
        # table must gain any missing additive column so the write-once
        # trigger the script (re)creates for that column has a real column to
        # guard, and so the first append_scan INSERT does not 500. On a fresh
        # DB _migrate is a no-op — the table does not exist yet — and the
        # full column set comes from _SCHEMA_SQL below.
        self._migrate()
        self._conn.executescript(_SCHEMA_SQL)

    def _migrate(self) -> None:
        """Idempotently add any :data:`_ADDITIVE_COLUMNS` a pre-existing
        ``scan_log`` table is missing.

        ``CREATE TABLE IF NOT EXISTS`` in :data:`_SCHEMA_SQL` never alters an
        existing table, so a database created before a column was introduced
        silently lacks it (BEAAA-12895 — the live 159-row production DB
        predated ``probe_set_hash`` and every INSERT 500'd). This runs on
        every connect and is a no-op once the columns are present, so it is
        safe to leave in permanently.
        """
        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'scan_log'"
        ).fetchone()
        if exists is None:
            # Fresh DB — _SCHEMA_SQL creates the table with all columns.
            return
        present = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(scan_log)")
        }
        for name, decl in _ADDITIVE_COLUMNS:
            if name not in present:
                # ALTER ... ADD COLUMN of a nullable column is O(1) in sqlite
                # (metadata-only; existing rows read the new column as NULL)
                # so this does not rewrite the append-only history.
                self._conn.execute(
                    f"ALTER TABLE scan_log ADD COLUMN {name} {decl}"
                )

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def get(self, agent_id: str, run_timestamp: str) -> dict[str, Any] | None:
        with self._write_lock:
            cur = self._conn.execute(
                "SELECT * FROM scan_log WHERE agent_id = ? AND run_timestamp = ?",
                (agent_id, run_timestamp),
            )
            row = cur.fetchone()
            return _row_to_dict(row) if row is not None else None

    def list_for_agent(self, agent_id: str) -> list[dict[str, Any]]:
        """Return all scan_log rows for ``agent_id`` ordered by run_timestamp ASC.

        Used by WS3 chain reconstruction to build ``prior_scan_refs[]``.
        """
        with self._write_lock:
            cur = self._conn.execute(
                "SELECT * FROM scan_log WHERE agent_id = ? ORDER BY run_timestamp ASC",
                (agent_id,),
            )
            return [_row_to_dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def insert_pending(
        self,
        *,
        agent_id: str,
        run_timestamp: str,
        scan_id: str,
        scanner_version: str,
        corpus_version: str | None,
        score_json: dict[str, Any],
        probe_set_hash: str | None = None,
    ) -> None:
        """Insert a scan_log row with the pinned fields NULL.

        Use this when the scanner has finalized the score envelope but
        has not yet uploaded the forensic bundle. ``finalize_pins``
        completes the row by writing both pins together in a single
        transaction.

        ``probe_set_hash`` (BEAAA-11726 item 4) identifies the exact probe
        subset that ran — write-once like ``snapshot_hash``.
        """
        with self._write_lock:
            self._conn.execute(
                "INSERT INTO scan_log "
                "(agent_id, run_timestamp, scan_id, scanner_version, corpus_version, "
                "score_json, probe_set_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    agent_id,
                    run_timestamp,
                    scan_id,
                    scanner_version,
                    corpus_version,
                    canonicalize(score_json).decode(),
                    probe_set_hash,
                ),
            )

    def finalize_pins(
        self,
        *,
        agent_id: str,
        run_timestamp: str,
        forensic_bundle_uri: str,
    ) -> str:
        """Pin ``forensic_bundle_uri`` and ``snapshot_hash`` in one transaction.

        ``snapshot_hash`` is computed from the row's stored ``score_json``
        via ``canonical_sha256(jcs(score_json))`` — the scanner does not
        get to supply the hash separately. The hash is then **never**
        recomputed; later consumers verify against the persisted bundle
        bytes.

        Both pins must currently be NULL. A second pin attempt against
        ``forensic_bundle_uri`` raises :class:`ForensicBundleWriteOnce`
        (mapped by the API edge to HTTP 409 with reason
        ``forensic_bundle_uri_write_once``).

        Returns the persisted ``snapshot_hash`` for the caller's audit log.
        """
        with self._write_lock:
            row = self.get(agent_id, run_timestamp)
            if row is None:
                raise ScanRowNotFound(
                    f"scan_log row ({agent_id!r}, {run_timestamp!r}) does not exist"
                )
            # Forensic-bundle URI is the primary write-once pin per ADR-0009
            # §4.1; surface its violation eagerly so the API edge can return the
            # single canonical reason code ``forensic_bundle_uri_write_once``.
            # The DB-layer trigger below still backs this as defense-in-depth.
            if row["forensic_bundle_uri"] is not None:
                raise ForensicBundleWriteOnce(
                    "forensic_bundle_uri is write-once per ADR-0009 §4.1"
                )
            # `row["score_json"]` was deserialized by ``_row_to_dict``; re-
            # canonicalizing it reproduces the exact stored bytes (ADR-0009 §4.2).
            snapshot_hash = compute_snapshot_hash(row["score_json"])
            try:
                with _transaction(self._conn):
                    self._conn.execute(
                        "UPDATE scan_log SET forensic_bundle_uri = ?, snapshot_hash = ? "
                        "WHERE agent_id = ? AND run_timestamp = ?",
                        (
                            forensic_bundle_uri,
                            snapshot_hash,
                            agent_id,
                            run_timestamp,
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                _raise_for_trigger(exc)
                raise
            return snapshot_hash

    def append_scan(
        self,
        *,
        agent_id: str,
        run_timestamp: str,
        scan_id: str,
        scanner_version: str,
        corpus_version: str | None,
        score_json: dict[str, Any],
        forensic_bundle_uri: str,
        probe_set_hash: str | None = None,
    ) -> str:
        """Insert + pin in one logical write — the common-path API.

        Equivalent to ``insert_pending`` followed by ``finalize_pins``
        but executed in a single transaction so a crash between the two
        steps cannot leave a half-pinned row. Returns the persisted
        ``snapshot_hash``.

        ``probe_set_hash`` (BEAAA-11726 item 4) identifies the exact probe
        subset that ran — write-once like ``snapshot_hash``.
        """
        # If a row at this key already carries a pinned URI, surface the
        # same canonical write-once reason the UPDATE path uses — even
        # though the underlying sqlite error here would be a UNIQUE PK
        # violation, the contract-facing semantics are identical.
        with self._write_lock:
            existing = self.get(agent_id, run_timestamp)
            if existing is not None and existing["forensic_bundle_uri"] is not None:
                raise ForensicBundleWriteOnce(
                    "forensic_bundle_uri is write-once per ADR-0009 §4.1"
                )
            snapshot_hash = compute_snapshot_hash(score_json)
            try:
                with _transaction(self._conn):
                    self._conn.execute(
                        "INSERT INTO scan_log "
                        "(agent_id, run_timestamp, scan_id, scanner_version, "
                        "corpus_version, score_json, forensic_bundle_uri, "
                        "snapshot_hash, probe_set_hash) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            agent_id,
                            run_timestamp,
                            scan_id,
                            scanner_version,
                            corpus_version,
                            canonicalize(score_json).decode(),
                            forensic_bundle_uri,
                            snapshot_hash,
                            probe_set_hash,
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                _raise_for_trigger(exc)
                raise
            return snapshot_hash

    def close(self) -> None:
        with self._write_lock:
            self._conn.close()

    # Context-manager sugar so callers can ``with ScanLog(path) as log:``.
    def __enter__(self) -> "ScanLog":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    out = {k: row[k] for k in row.keys()}
    out["score_json"] = json.loads(out["score_json"])
    return out


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Wrap a multi-statement write in BEGIN / COMMIT / ROLLBACK.

    With ``isolation_level=None`` (autocommit) sqlite leaves transaction
    scope to the caller; we use BEGIN IMMEDIATE so the writer's lock is
    held for the duration of finalize_pins / append_scan even under
    concurrent connections to the same database file.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _raise_for_trigger(exc: sqlite3.IntegrityError) -> None:
    """Translate a sqlite trigger ABORT into a typed :class:`ScanLogError`."""
    msg = str(exc)
    if "forensic_bundle_uri_write_once" in msg:
        raise ForensicBundleWriteOnce(
            "forensic_bundle_uri is write-once per ADR-0009 §4.1"
        ) from exc
    if "snapshot_hash_write_once" in msg:
        raise SnapshotHashWriteOnce(
            "snapshot_hash is write-once per ADR-0009 §4.2"
        ) from exc
    if "scan_log_append_only" in msg:
        raise ScanLogAppendOnly(
            "scan_log is append-only per ADR-0009 §4"
        ) from exc
    if "probe_set_hash_write_once" in msg:
        raise ProbeSetHashWriteOnce(
            "probe_set_hash is write-once per BEAAA-11726 item 4"
        ) from exc


__all__ = [
    "ScanLog",
    "compute_snapshot_hash",
    "ScanLogError",
    "ForensicBundleWriteOnce",
    "SnapshotHashWriteOnce",
    "ProbeSetHashWriteOnce",
    "ScanLogAppendOnly",
    "ScanRowNotFound",
]
