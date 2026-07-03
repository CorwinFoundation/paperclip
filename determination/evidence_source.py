"""BEAAA-11372 / BEAAA-11378 evidence-packet source port.

The Determination Engine reads immutable ``EvidencePacket`` snapshots through
this port. Two implementations ship in v1:

* ``FixtureEvidenceSource`` — loads packets from dict/JSON. This backs the golden
  fixtures and can replay any exported evidence packet, so the entire engine is
  reproducible in the agent runtime with no database.
* ``EvidenceStoreReadAdapter`` — the LIVE, tenant-isolated binding to the
  append-only evidence store (BEAAA-11366) read model, i.e. the
  ``evidence_store.latest_*`` views plus ``evidence_store.evidence_content_hashes``.
  It assembles exactly one immutable ``EvidencePacket`` for a
  ``(tenant_id_hash, agent_id, agent_version, evidence_batch_id)`` lineage and
  fails closed on any lineage ambiguity or malformed evidence shape.

The adapter is deliberately split into two seams:

* ``EvidenceRowSource`` — a narrow read port that returns raw current-head rows
  from the store views/table. ``PostgresEvidenceRowSource`` is the production
  DB-API binding; it lazy-imports the driver so this module imports with no
  Postgres present. Test doubles implement the same three methods.
* ``EvidenceStoreReadAdapter`` — the pure, DB-agnostic assembler that maps rows
  to an ``EvidencePacket`` and enforces the fail-closed contract from the CTO
  handoff (``artifacts/beaaa-11377/evidence_store_read_client_handoff_v0.1.md``).

This module adds a read path only. It changes no BackBond Score math, rubric,
judge, pricing, or ``are-scanner-api-v1`` response shape. ``scanner_version`` and
``corpus_version`` are observational read-through fields on the packet.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from determination.records import EvidencePacket, StructuredEvent


# --- Named fail-closed errors ----------------------------------------------


class EvidencePacketReadError(RuntimeError):
    """Base class for live evidence-store read failures."""


class EvidencePacketNotFoundError(EvidencePacketReadError):
    """No current-head manifest exists for the requested lineage."""


class EvidencePacketLineageError(EvidencePacketReadError):
    """A cross-tenant, cross-agent, cross-version, or cross-batch mismatch, or an
    ambiguous manifest head, was observed between manifest, event, or
    content-hash rows."""


class EvidencePacketShapeError(EvidencePacketReadError):
    """Required evidence JSON shape is malformed (non-object ``summary`` or
    ``attributes``, missing ``manifest_content_sha256``, bad timestamp, etc.)."""


class EvidencePacketSource(Protocol):
    def read_packet(
        self,
        *,
        tenant_id_hash: str,
        agent_id: str,
        agent_version: str,
        evidence_batch_id: str,
    ) -> EvidencePacket: ...


# --- Fixture / replay source -----------------------------------------------


class FixtureEvidenceSource:
    """Serves evidence packets from in-memory dicts (golden fixtures / replays)."""

    def __init__(self, packets: Iterable[Mapping[str, Any]] = ()) -> None:
        self._by_key: dict[tuple[str, str, str, str], EvidencePacket] = {}
        for data in packets:
            self.add(data)

    def add(self, data: Mapping[str, Any]) -> EvidencePacket:
        packet = EvidencePacket.from_dict(data)
        self._by_key[self._key_for(packet)] = packet
        return packet

    @classmethod
    def from_json_file(cls, path: str | Path) -> "FixtureEvidenceSource":
        import json

        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        packets = raw if isinstance(raw, list) else [raw]
        return cls(packets)

    def read_packet(
        self,
        *,
        tenant_id_hash: str,
        agent_id: str,
        agent_version: str,
        evidence_batch_id: str,
    ) -> EvidencePacket:
        key = (tenant_id_hash, agent_id, agent_version, evidence_batch_id)
        if key not in self._by_key:
            raise KeyError(f"no evidence packet for {key}")
        return self._by_key[key]

    @staticmethod
    def _key_for(packet: EvidencePacket) -> tuple[str, str, str, str]:
        return (
            packet.tenant_id_hash,
            packet.agent_id,
            packet.agent_version,
            packet.evidence_batch_id,
        )


# --- Live read seams --------------------------------------------------------


class EvidenceRowSource(Protocol):
    """Narrow read port over the append-only evidence-store current-head views.

    Implementations MUST return raw rows (column-name -> value mappings) and MUST
    apply the exact ``(tenant_id_hash, agent_id, agent_version,
    evidence_batch_id)`` filter — never a widened one — reading only from the
    latest-head views / content-hash table. All head-selection and tenant
    isolation happens at the data layer; the adapter validates it defensively.
    """

    def fetch_latest_manifests(
        self,
        *,
        tenant_id_hash: str,
        agent_id: str,
        agent_version: str,
        evidence_batch_id: str,
    ) -> Sequence[Mapping[str, Any]]: ...

    def fetch_latest_events(
        self,
        *,
        tenant_id_hash: str,
        agent_id: str,
        agent_version: str,
        evidence_batch_id: str,
    ) -> Sequence[Mapping[str, Any]]: ...

    def fetch_content_hashes(
        self,
        *,
        tenant_id_hash: str,
        evidence_event_ids: Sequence[str],
    ) -> Sequence[Mapping[str, Any]]: ...


_MANIFEST_SQL = """
SELECT *
FROM evidence_store.latest_signed_batch_manifests
WHERE tenant_id_hash = %(tenant_id_hash)s
  AND agent_id = %(agent_id)s
  AND agent_version = %(agent_version)s
  AND evidence_batch_id = %(evidence_batch_id)s
"""

_EVENTS_SQL = """
SELECT *
FROM evidence_store.latest_structured_event_summaries
WHERE tenant_id_hash = %(tenant_id_hash)s
  AND agent_id = %(agent_id)s
  AND agent_version = %(agent_version)s
  AND evidence_batch_id = %(evidence_batch_id)s
"""

_CONTENT_HASHES_SQL = """
SELECT evidence_event_id, tenant_id_hash, content_sha256
FROM evidence_store.evidence_content_hashes
WHERE tenant_id_hash = %(tenant_id_hash)s
  AND evidence_event_id = ANY(%(evidence_event_ids)s)
"""


class PostgresEvidenceRowSource:
    """Production DB-API binding for :class:`EvidenceRowSource`.

    Reads only from ``evidence_store.latest_signed_batch_manifests``,
    ``evidence_store.latest_structured_event_summaries``, and
    ``evidence_store.evidence_content_hashes``. Every query carries the full
    lineage filter; none reads a base manifest/event table directly.

    The connection is any DB-API 2.0 connection (e.g. ``psycopg`` /
    ``psycopg2``). The driver is never imported here, so this module loads in the
    agent runtime with no Postgres available; construction just needs a live
    connection supplied by the caller.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    @staticmethod
    def _rows(cursor: Any) -> list[dict[str, Any]]:
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _query(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql, params)
            return self._rows(cursor)
        finally:
            cursor.close()

    def fetch_latest_manifests(
        self,
        *,
        tenant_id_hash: str,
        agent_id: str,
        agent_version: str,
        evidence_batch_id: str,
    ) -> Sequence[Mapping[str, Any]]:
        return self._query(
            _MANIFEST_SQL,
            {
                "tenant_id_hash": tenant_id_hash,
                "agent_id": agent_id,
                "agent_version": agent_version,
                "evidence_batch_id": evidence_batch_id,
            },
        )

    def fetch_latest_events(
        self,
        *,
        tenant_id_hash: str,
        agent_id: str,
        agent_version: str,
        evidence_batch_id: str,
    ) -> Sequence[Mapping[str, Any]]:
        return self._query(
            _EVENTS_SQL,
            {
                "tenant_id_hash": tenant_id_hash,
                "agent_id": agent_id,
                "agent_version": agent_version,
                "evidence_batch_id": evidence_batch_id,
            },
        )

    def fetch_content_hashes(
        self,
        *,
        tenant_id_hash: str,
        evidence_event_ids: Sequence[str],
    ) -> Sequence[Mapping[str, Any]]:
        return self._query(
            _CONTENT_HASHES_SQL,
            {
                "tenant_id_hash": tenant_id_hash,
                "evidence_event_ids": list(evidence_event_ids),
            },
        )


def _to_iso_utc(value: Any, *, request: tuple[str, str, str, str]) -> str:
    """Normalize a store timestamp to an ISO-8601 UTC string with a ``Z`` suffix.

    Accepts ``datetime`` (as returned by DB-API for ``TIMESTAMPTZ``) or an
    already-normalized ISO string. Fails closed on any other shape.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, str) and value:
        return value
    raise EvidencePacketShapeError(
        f"malformed event_timestamp for lineage {request}"
    )


class EvidenceStoreReadAdapter:
    """Live evidence-store read binding (BEAAA-11378).

    Given ``(tenant_id_hash, agent_id, agent_version, evidence_batch_id)``,
    assembles exactly one immutable :class:`EvidencePacket` from the current-head
    evidence-store rows returned by an :class:`EvidenceRowSource`, enforcing the
    fail-closed / tenant-isolation contract in the CTO handoff.

    Construct with an explicit row source; the live path is never auto-selected
    from the environment (rollback stays a code decision):

        adapter = EvidenceStoreReadAdapter.from_connection(pg_conn)
    """

    def __init__(self, row_source: EvidenceRowSource) -> None:
        self._rows = row_source

    @classmethod
    def from_connection(cls, connection: Any) -> "EvidenceStoreReadAdapter":
        return cls(PostgresEvidenceRowSource(connection))

    def read_packet(
        self,
        *,
        tenant_id_hash: str,
        agent_id: str,
        agent_version: str,
        evidence_batch_id: str,
    ) -> EvidencePacket:
        request = (tenant_id_hash, agent_id, agent_version, evidence_batch_id)

        manifest = self._resolve_manifest(
            request,
            self._rows.fetch_latest_manifests(
                tenant_id_hash=tenant_id_hash,
                agent_id=agent_id,
                agent_version=agent_version,
                evidence_batch_id=evidence_batch_id,
            ),
        )

        event_rows = self._rows.fetch_latest_events(
            tenant_id_hash=tenant_id_hash,
            agent_id=agent_id,
            agent_version=agent_version,
            evidence_batch_id=evidence_batch_id,
        )
        events = self._build_events(request, event_rows)
        event_ids = [e.evidence_event_id for e in events]

        content_hashes = self._aggregate_content_hashes(
            request,
            event_ids,
            self._rows.fetch_content_hashes(
                tenant_id_hash=tenant_id_hash,
                evidence_event_ids=event_ids,
            ),
        )

        return self._assemble_packet(request, manifest, events, content_hashes)

    # --- assembly steps -----------------------------------------------------

    def _resolve_manifest(
        self,
        request: tuple[str, str, str, str],
        rows: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        tenant_id_hash, agent_id, agent_version, evidence_batch_id = request
        if not rows:
            raise EvidencePacketNotFoundError(
                f"no current-head manifest for lineage {request}"
            )
        if len(rows) > 1:
            raise EvidencePacketLineageError(
                f"ambiguous manifest head ({len(rows)} rows) for lineage {request}"
            )
        row = rows[0]
        # Defense in depth: reject any row whose lineage does not exactly match,
        # even though the row source already filtered on all four fields.
        if (
            str(row.get("tenant_id_hash")) != tenant_id_hash
            or str(row.get("agent_id")) != agent_id
            or str(row.get("agent_version")) != agent_version
            or str(row.get("evidence_batch_id")) != evidence_batch_id
        ):
            raise EvidencePacketLineageError(
                f"manifest row lineage does not match request {request}"
            )
        manifest_sha = row.get("manifest_content_sha256")
        if not manifest_sha:
            raise EvidencePacketShapeError(
                f"manifest missing manifest_content_sha256 for lineage {request}"
            )
        return row

    def _build_events(
        self,
        request: tuple[str, str, str, str],
        rows: Sequence[Mapping[str, Any]],
    ) -> tuple[StructuredEvent, ...]:
        tenant_id_hash, agent_id, agent_version, evidence_batch_id = request
        built: list[tuple[str, str, StructuredEvent]] = []
        for row in rows:
            if (
                str(row.get("tenant_id_hash")) != tenant_id_hash
                or str(row.get("agent_id")) != agent_id
                or str(row.get("agent_version")) != agent_version
                or str(row.get("evidence_batch_id")) != evidence_batch_id
            ):
                raise EvidencePacketLineageError(
                    f"event row lineage differs from request {request}"
                )
            evidence_event_id = row.get("evidence_event_id")
            if not evidence_event_id:
                raise EvidencePacketShapeError(
                    f"event missing evidence_event_id for lineage {request}"
                )
            content_sha256 = row.get("content_sha256")
            if not content_sha256:
                raise EvidencePacketShapeError(
                    f"event {evidence_event_id} missing content_sha256"
                )

            summary = row.get("summary")
            if summary is None:
                summary = {}
            if not isinstance(summary, Mapping):
                raise EvidencePacketShapeError(
                    f"event {evidence_event_id} summary is not an object"
                )
            attributes = summary.get("attributes")
            if attributes is None:
                attributes = {}
            elif not isinstance(attributes, Mapping):
                raise EvidencePacketShapeError(
                    f"event {evidence_event_id} attributes is not an object"
                )

            event_timestamp = _to_iso_utc(
                row.get("event_timestamp"), request=request
            )
            event = StructuredEvent(
                evidence_event_id=str(evidence_event_id),
                event_kind=str(row.get("event_kind", "")),
                event_timestamp=event_timestamp,
                content_sha256=str(content_sha256),
                attributes=dict(attributes),
                trace_id=summary.get("trace_id"),
                span_id=summary.get("span_id"),
                rubric_point_id=row.get("rubric_point_id"),
            )
            built.append((event_timestamp, str(evidence_event_id), event))

        # Deterministic order: (event_timestamp, evidence_event_id).
        built.sort(key=lambda item: (item[0], item[1]))
        return tuple(item[2] for item in built)

    def _aggregate_content_hashes(
        self,
        request: tuple[str, str, str, str],
        event_ids: Sequence[str],
        rows: Sequence[Mapping[str, Any]],
    ) -> tuple[str, ...]:
        tenant_id_hash = request[0]
        allowed = set(event_ids)
        hashes: set[str] = set()
        for row in rows:
            if str(row.get("tenant_id_hash")) != tenant_id_hash:
                raise EvidencePacketLineageError(
                    f"content-hash row tenant differs from request {request}"
                )
            row_event_id = str(row.get("evidence_event_id"))
            if row_event_id not in allowed:
                raise EvidencePacketLineageError(
                    f"content-hash row references unselected event for {request}"
                )
            content_sha256 = row.get("content_sha256")
            if not content_sha256:
                raise EvidencePacketShapeError(
                    f"content-hash row missing content_sha256 for {request}"
                )
            hashes.add(str(content_sha256))
        return tuple(sorted(hashes))

    def _assemble_packet(
        self,
        request: tuple[str, str, str, str],
        manifest: Mapping[str, Any],
        events: tuple[StructuredEvent, ...],
        content_hashes: tuple[str, ...],
    ) -> EvidencePacket:
        signed_manifest = manifest.get("signed_manifest")
        deployment_id: str | None = None
        if isinstance(signed_manifest, Mapping):
            deployment_id = signed_manifest.get("deployment_id")

        return EvidencePacket(
            evidence_batch_id=str(manifest["evidence_batch_id"]),
            tenant_id_hash=str(manifest["tenant_id_hash"]),
            agent_id=str(manifest["agent_id"]),
            agent_version=str(manifest["agent_version"]),
            manifest_content_sha256=str(manifest["manifest_content_sha256"]),
            events=events,
            deployment_id=deployment_id,
            collector_version=manifest.get("collector_version"),
            scanner_version=manifest.get("scanner_version"),
            corpus_version=manifest.get("corpus_version"),
            probe_set_version=manifest.get("probe_set_version"),
            manifest_signature_algorithm=manifest.get(
                "manifest_signature_algorithm"
            ),
            content_hashes=content_hashes,
        )


__all__ = [
    "EvidencePacketLineageError",
    "EvidencePacketNotFoundError",
    "EvidencePacketReadError",
    "EvidencePacketShapeError",
    "EvidencePacketSource",
    "EvidenceRowSource",
    "EvidenceStoreReadAdapter",
    "FixtureEvidenceSource",
    "PostgresEvidenceRowSource",
]
