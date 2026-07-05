"""BEAAA-11876 — JSONL->Postgres durable loader + reconciler for the
fired-probe canary audit-spine ledger.

Follow-on to BEAAA-11872. 11872 fixed the ``ledger=None``-in-prod defect and
made the fired-probe ledger append-only + per-record tamper-evident at both the
JSONL runtime sink (EFS) and the durable ``canary.fired_probe_records`` Postgres
book of record. But the *whole-of-record* tamper-proof guarantee lived only in
the Postgres table, and no loader drained the JSONL sink into it — so the
durable table was, at best, a partial copy, and Founder rule 3d was not fully
closed on the EFS sink alone.

This module closes the durability gap:

* :class:`FiredProbeLedgerLoader` **drains** the EFS JSONL sink
  (:class:`~scanners.canary_anti_gaming.AuditSpineFiredProbeLedger`) into the
  durable append-only book of record, keyed on the hash-chain value
  (``chain_sha256``) so re-draining the same lines is idempotent.
* :meth:`FiredProbeLedgerLoader.reconcile` **detects gaps** between the EFS sink
  and Postgres — records present in the durable book but missing from the JSONL
  (whole-line delete / tail truncation at the runtime sink) and a broken JSONL
  hash-chain (mid-stream delete / reorder / edit) — and surfaces a tamper alarm.

The row sink is a narrow port so the module imports with no Postgres present
(mirrors ``determination/evidence_source.py``): the production
:class:`PostgresFiredProbeRowSink` lazy-imports nothing itself — it takes a live
DB-API 2.0 connection — and :class:`InMemoryFiredProbeRowSink` backs the tests.

This is additive anti-gaming sidecar infrastructure under
are-scanner-api-v1@v1.9.0. It changes no BackBond Score math, rubric weight,
judge model, or ``/scanner/v1/scans`` response shape — the canary ledger is an
Underwriting-review signal only and never feeds scoring. It strictly *increases*
tamper resistance (rule-change: tamper-resistance touch → CTO co-sign at merge).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from scanners.canary_anti_gaming import (
    AuditSpineFiredProbeLedger,
    FiredProbeChainVerification,
    normalize_phrasing_hash,
)

# The durable-row columns the loader carries over from each JSONL book-of-record
# line. ``fired_probe_row_id`` and (optionally) ``recorded_at`` are supplied by
# the table defaults; everything else is copied verbatim so the durable table is
# a faithful, complete copy of the runtime sink.
_LOADER_COLUMNS = (
    "schema_version",
    "preimage_sha256",
    "prev_sha256",
    "chain_sha256",
    "tenant_id",
    "vendor_id",
    "phrasing_hash",
    "scan_id",
    "fired_at",
    "rubric_point_id",
    "test_id",
    "recorded_at",
)

_REQUIRED_ENTRY_FIELDS = (
    "chain_sha256",
    "prev_sha256",
    "preimage_sha256",
    "tenant_id",
    "vendor_id",
    "phrasing_hash",
    "scan_id",
    "fired_at",
)


def _durable_row_from_entry(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project one JSONL book-of-record line into a durable insert row.

    Returns ``None`` for a line that is not a well-formed chained fired-probe
    entry (legacy pre-chain line, corrupt row, wrong record kind) so the loader
    skips it rather than corrupting the durable table.
    """
    if entry.get("record_kind") != "fired_probe":
        return None
    for required in _REQUIRED_ENTRY_FIELDS:
        if not isinstance(entry.get(required), str):
            return None
    try:
        phrasing_hash = normalize_phrasing_hash(entry["phrasing_hash"])
    except ValueError:
        return None
    return {
        "schema_version": entry.get("schema_version"),
        "preimage_sha256": entry["preimage_sha256"],
        "prev_sha256": entry["prev_sha256"],
        "chain_sha256": entry["chain_sha256"],
        "tenant_id": entry["tenant_id"],
        "vendor_id": entry["vendor_id"],
        "phrasing_hash": phrasing_hash,
        "scan_id": entry["scan_id"],
        "fired_at": entry["fired_at"],
        "rubric_point_id": entry.get("rubric_point_id"),
        "test_id": entry.get("test_id"),
        "recorded_at": entry.get("recorded_at"),
    }


class FiredProbeRowSink(Protocol):
    """Narrow durable-write port over ``canary.fired_probe_records``.

    Implementations MUST be append-only (the table's ``reject_mutation`` trigger
    enforces this at the DB layer) and MUST make :meth:`insert` idempotent on
    ``chain_sha256`` so re-draining the JSONL sink inserts nothing new.
    """

    def chain_sha256s(self) -> frozenset[str]: ...

    def insert(self, rows: Sequence[Mapping[str, Any]]) -> int: ...


@dataclass
class InMemoryFiredProbeRowSink:
    """Append-only in-memory durable book of record for tests and dry runs.

    Idempotent on ``chain_sha256`` exactly like the Postgres unique index.
    """

    _rows: list[dict[str, Any]] = field(default_factory=list)
    _chains: set[str] = field(default_factory=set)

    def chain_sha256s(self) -> frozenset[str]:
        return frozenset(self._chains)

    def insert(self, rows: Sequence[Mapping[str, Any]]) -> int:
        inserted = 0
        for row in rows:
            chain = row.get("chain_sha256")
            if not isinstance(chain, str) or chain in self._chains:
                continue
            self._rows.append(dict(row))
            self._chains.add(chain)
            inserted += 1
        return inserted

    @property
    def rows(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._rows)


_INSERT_SQL = """
INSERT INTO canary.fired_probe_records
    (schema_version, preimage_sha256, prev_sha256, chain_sha256,
     tenant_id, vendor_id, phrasing_hash, scan_id, fired_at,
     rubric_point_id, test_id, recorded_at)
VALUES
    (%(schema_version)s, %(preimage_sha256)s, %(prev_sha256)s, %(chain_sha256)s,
     %(tenant_id)s, %(vendor_id)s, %(phrasing_hash)s, %(scan_id)s, %(fired_at)s,
     %(rubric_point_id)s, %(test_id)s, COALESCE(%(recorded_at)s, now()))
ON CONFLICT (chain_sha256) WHERE chain_sha256 IS NOT NULL DO NOTHING
"""

_CHAIN_SELECT_SQL = """
SELECT chain_sha256
FROM canary.fired_probe_records
WHERE chain_sha256 IS NOT NULL
"""


class PostgresFiredProbeRowSink:
    """Production DB-API binding for :class:`FiredProbeRowSink`.

    Writes only ``INSERT ... ON CONFLICT (chain_sha256) DO NOTHING`` — never an
    ``UPDATE`` or ``DELETE`` — so it composes with the table's append-only
    ``reject_mutation`` trigger and stays idempotent under re-drains and
    concurrent loaders. The connection is any DB-API 2.0 connection
    (e.g. ``psycopg`` / ``psycopg2``); the driver is never imported here, so this
    module loads in the agent runtime with no Postgres available.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def chain_sha256s(self) -> frozenset[str]:
        cursor = self._connection.cursor()
        try:
            cursor.execute(_CHAIN_SELECT_SQL)
            return frozenset(row[0] for row in cursor.fetchall() if row[0])
        finally:
            cursor.close()

    def insert(self, rows: Sequence[Mapping[str, Any]]) -> int:
        inserted = 0
        cursor = self._connection.cursor()
        try:
            for row in rows:
                cursor.execute(_INSERT_SQL, {col: row.get(col) for col in _LOADER_COLUMNS})
                # rowcount is 1 on insert, 0 when the ON CONFLICT no-op fires.
                if cursor.rowcount and cursor.rowcount > 0:
                    inserted += 1
        finally:
            cursor.close()
        return inserted


@dataclass(frozen=True)
class LoadReport:
    """Outcome of a JSONL->Postgres drain."""

    scanned: int
    inserted: int
    already_present: int
    skipped_malformed: int


@dataclass(frozen=True)
class ReconcileReport:
    """Gap analysis between the EFS JSONL sink and the durable Postgres book.

    ``tamper_alarm`` is the operator-facing signal. It fires when the durable
    book holds records the JSONL sink no longer does (whole-line delete / tail
    truncation), when the JSONL hash-chain is broken (mid-stream delete /
    reorder / edit), or when the JSONL sink contains duplicate chain values.

    ``missing_in_postgres`` (JSONL lines not yet loaded) is *not* on its own a
    tamper signal — it is the normal pre-drain backlog — so it is reported but
    does not raise the alarm by itself.
    """

    jsonl_records: int
    postgres_records: int
    missing_in_postgres: tuple[str, ...]
    missing_in_jsonl: tuple[str, ...]
    duplicate_in_jsonl: tuple[str, ...]
    chain_verification: FiredProbeChainVerification
    tamper_alarm: bool


class FiredProbeLedgerLoader:
    """Drain + reconcile the EFS JSONL sink against the durable Postgres book.

    Construct with the runtime ledger (the same
    :class:`AuditSpineFiredProbeLedger` the scan runtime writes to) and a durable
    row sink::

        loader = FiredProbeLedgerLoader(
            ledger=AuditSpineFiredProbeLedger(efs_path),
            sink=PostgresFiredProbeRowSink(pg_conn),
        )
        report = loader.load()        # idempotent drain
        recon = loader.reconcile()    # tamper gap analysis
    """

    def __init__(
        self, *, ledger: AuditSpineFiredProbeLedger, sink: FiredProbeRowSink
    ) -> None:
        self._ledger = ledger
        self._sink = sink

    def _durable_rows(self) -> tuple[list[dict[str, Any]], int]:
        rows: list[dict[str, Any]] = []
        skipped = 0
        for entry in self._ledger.read_all():
            row = _durable_row_from_entry(entry)
            if row is None:
                skipped += 1
            else:
                rows.append(row)
        return rows, skipped

    def load(self) -> LoadReport:
        """Insert every not-yet-durable JSONL line into the Postgres book.

        Idempotent: rows already present (matched on ``chain_sha256``) are
        skipped, so repeated drains converge with nothing new inserted.
        """
        rows, skipped = self._durable_rows()
        existing = self._sink.chain_sha256s()
        pending = [row for row in rows if row["chain_sha256"] not in existing]
        inserted = self._sink.insert(pending)
        return LoadReport(
            scanned=len(rows),
            inserted=inserted,
            already_present=len(rows) - len(pending),
            skipped_malformed=skipped,
        )

    def reconcile(self) -> ReconcileReport:
        """Compare the JSONL sink and the durable book; raise a tamper alarm on
        any whole-of-record divergence."""
        rows, _ = self._durable_rows()
        jsonl_chain_list = [row["chain_sha256"] for row in rows]
        jsonl_chains = set(jsonl_chain_list)
        duplicate_in_jsonl = sorted(
            {c for c in jsonl_chain_list if jsonl_chain_list.count(c) > 1}
        )
        pg_chains = set(self._sink.chain_sha256s())

        missing_in_postgres = sorted(jsonl_chains - pg_chains)
        missing_in_jsonl = sorted(pg_chains - jsonl_chains)
        chain_verification = self._ledger.verify_chain()

        tamper_alarm = bool(
            missing_in_jsonl
            or duplicate_in_jsonl
            or not chain_verification.ok
        )
        return ReconcileReport(
            jsonl_records=len(rows),
            postgres_records=len(pg_chains),
            missing_in_postgres=tuple(missing_in_postgres),
            missing_in_jsonl=tuple(missing_in_jsonl),
            duplicate_in_jsonl=tuple(duplicate_in_jsonl),
            chain_verification=chain_verification,
            tamper_alarm=tamper_alarm,
        )


__all__ = [
    "FiredProbeLedgerLoader",
    "FiredProbeRowSink",
    "InMemoryFiredProbeRowSink",
    "LoadReport",
    "PostgresFiredProbeRowSink",
    "ReconcileReport",
]
