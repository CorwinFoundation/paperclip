"""BEAAA-11378 EvidenceStoreReadAdapter v1 tests.

Locks the live evidence-store read path for Determination Engine v1: assemble
exactly one immutable ``EvidencePacket`` from current-head rows for a
``(tenant_id_hash, agent_id, agent_version, evidence_batch_id)`` lineage, with
deterministic event ordering, sorted-unique content-hash aggregation, tenant
isolation, and named fail-closed errors.

Runtime coverage uses an in-memory row source that faithfully emulates the
``evidence_store.latest_*`` views (the same ``NOT EXISTS`` head filter as
``tools/evidence-store/migrations/0001_evidence_store_v1.sql``) plus the lineage
WHERE clauses. A live Postgres parity test runs the real
``PostgresEvidenceRowSource`` when ``EVIDENCE_STORE_PG_DSN`` is set.

Rule-execution only: this adds a read path and changes no BackBond Score math,
rubric, judge, pricing, or ``are-scanner-api-v1`` response shape.
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from determination.evidence_source import (
    EvidencePacketLineageError,
    EvidencePacketNotFoundError,
    EvidencePacketShapeError,
    EvidenceStoreReadAdapter,
    PostgresEvidenceRowSource,
)
from determination.records import EvidencePacket

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "determination" / "fixtures" / "golden"
EVIDENCE_MIGRATION = (
    REPO / "tools" / "evidence-store" / "migrations" / "0001_evidence_store_v1.sql"
)

ALPHA = "tenant-hash-alpha"
AGENT = "agent-smith-ai-001"
VERSION = "v1"


# --- In-memory view-emulating row source ------------------------------------


class InMemoryEvidenceStore:
    """Row source double that emulates the append-only store's latest-head views.

    Holds base manifest / event / content-hash rows and applies the same
    supersession head-selection (``NOT EXISTS`` a correction pointing at the row)
    and lineage WHERE filters that the production SQL views apply, so tests
    exercise the real adapter contract without Postgres.
    """

    def __init__(self) -> None:
        self.manifests: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.content_hashes: list[dict[str, Any]] = []

    def add_manifest(self, **row: Any) -> "InMemoryEvidenceStore":
        self.manifests.append(row)
        return self

    def add_event(self, **row: Any) -> "InMemoryEvidenceStore":
        self.events.append(row)
        return self

    def add_content_hash(self, **row: Any) -> "InMemoryEvidenceStore":
        self.content_hashes.append(row)
        return self

    def _latest_manifests(self) -> list[dict[str, Any]]:
        superseded = {
            m["supersedes_evidence_batch_id"]
            for m in self.manifests
            if m.get("supersedes_evidence_batch_id")
        }
        return [
            m for m in self.manifests if m["evidence_batch_id"] not in superseded
        ]

    def _latest_events(self) -> list[dict[str, Any]]:
        superseded = {
            e["supersedes_evidence_event_id"]
            for e in self.events
            if e.get("supersedes_evidence_event_id")
        }
        return [
            e for e in self.events if e["evidence_event_id"] not in superseded
        ]

    def fetch_latest_manifests(
        self, *, tenant_id_hash, agent_id, agent_version, evidence_batch_id
    ) -> Sequence[Mapping[str, Any]]:
        return [
            m
            for m in self._latest_manifests()
            if m["tenant_id_hash"] == tenant_id_hash
            and m["agent_id"] == agent_id
            and m["agent_version"] == agent_version
            and m["evidence_batch_id"] == evidence_batch_id
        ]

    def fetch_latest_events(
        self, *, tenant_id_hash, agent_id, agent_version, evidence_batch_id
    ) -> Sequence[Mapping[str, Any]]:
        return [
            e
            for e in self._latest_events()
            if e["tenant_id_hash"] == tenant_id_hash
            and e["agent_id"] == agent_id
            and e["agent_version"] == agent_version
            and e["evidence_batch_id"] == evidence_batch_id
        ]

    def fetch_content_hashes(
        self, *, tenant_id_hash, evidence_event_ids
    ) -> Sequence[Mapping[str, Any]]:
        ids = set(evidence_event_ids)
        return [
            c
            for c in self.content_hashes
            if c["tenant_id_hash"] == tenant_id_hash
            and c["evidence_event_id"] in ids
        ]


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _seed_from_packet_dict(store: InMemoryEvidenceStore, data: Mapping[str, Any]) -> None:
    """Reconstruct base store rows from a golden EvidencePacket dict."""
    store.add_manifest(
        evidence_batch_id=data["evidence_batch_id"],
        tenant_id_hash=data["tenant_id_hash"],
        agent_id=data["agent_id"],
        agent_version=data["agent_version"],
        manifest_content_sha256=data["manifest_content_sha256"],
        signed_manifest={"deployment_id": data.get("deployment_id")},
        collector_version=data.get("collector_version"),
        scanner_version=data.get("scanner_version"),
        corpus_version=data.get("corpus_version"),
        probe_set_version=data.get("probe_set_version"),
        manifest_signature_algorithm=data.get("manifest_signature_algorithm"),
        supersedes_evidence_batch_id=None,
    )
    for event in data.get("events", []):
        summary: dict[str, Any] = {"attributes": event.get("attributes", {})}
        if event.get("trace_id") is not None:
            summary["trace_id"] = event["trace_id"]
        if event.get("span_id") is not None:
            summary["span_id"] = event["span_id"]
        store.add_event(
            evidence_event_id=event["evidence_event_id"],
            evidence_batch_id=data["evidence_batch_id"],
            tenant_id_hash=data["tenant_id_hash"],
            agent_id=data["agent_id"],
            agent_version=data["agent_version"],
            event_kind=event.get("event_kind", ""),
            event_timestamp=_dt(event["event_timestamp"]),
            rubric_point_id=event.get("rubric_point_id"),
            summary=summary,
            content_sha256=event["content_sha256"],
            supersedes_evidence_event_id=None,
        )
    for content_sha256 in data.get("content_hashes", []):
        owner = next(
            e["evidence_event_id"]
            for e in data["events"]
            if e["content_sha256"] == content_sha256
        )
        store.add_content_hash(
            evidence_event_id=owner,
            tenant_id_hash=data["tenant_id_hash"],
            content_sha256=content_sha256,
        )


def _golden(peril: str, case: str) -> dict[str, Any]:
    return json.loads((GOLDEN / peril / f"{case}.json").read_text())


def _request_for(data: Mapping[str, Any]) -> dict[str, str]:
    return {
        "tenant_id_hash": data["tenant_id_hash"],
        "agent_id": data["agent_id"],
        "agent_version": data["agent_version"],
        "evidence_batch_id": data["evidence_batch_id"],
    }


# --- Positive parity --------------------------------------------------------


@pytest.mark.parametrize(
    "peril",
    [
        "data_leak",
        "wrong_recipient_transaction",
        "unauthorized_action",
        "hallucination_financial_loss",
    ],
)
def test_positive_assembly_matches_golden_packet(peril):
    data = _golden(peril, "positive")
    store = InMemoryEvidenceStore()
    _seed_from_packet_dict(store, data)

    packet = EvidenceStoreReadAdapter(store).read_packet(**_request_for(data))

    assert packet == EvidencePacket.from_dict(data)
    # Observational read-through fields survive intact.
    assert packet.scanner_version == data.get("scanner_version")
    assert packet.corpus_version == data.get("corpus_version")
    assert packet.deployment_id == data.get("deployment_id")


# --- Cross-tenant denial ----------------------------------------------------


def test_cross_tenant_request_never_returns_another_tenants_packet():
    data = _golden("data_leak", "positive")
    store = InMemoryEvidenceStore()
    _seed_from_packet_dict(store, data)

    request = _request_for(data)
    request["tenant_id_hash"] = "tenant-hash-OTHER"
    with pytest.raises(EvidencePacketNotFoundError):
        EvidenceStoreReadAdapter(store).read_packet(**request)


def test_lineage_error_when_row_source_leaks_wrong_tenant_row():
    # A defective/adversarial row source that ignores the tenant filter must be
    # caught by the adapter's defensive lineage check, never assembled.
    class LeakyRowSource:
        def fetch_latest_manifests(self, **_):
            return [
                {
                    "evidence_batch_id": "batch-x",
                    "tenant_id_hash": "tenant-hash-alpha",
                    "agent_id": AGENT,
                    "agent_version": VERSION,
                    "manifest_content_sha256": "sha256:" + "a" * 64,
                    "signed_manifest": {},
                }
            ]

        def fetch_latest_events(self, **_):
            return []

        def fetch_content_hashes(self, **_):
            return []

    with pytest.raises(EvidencePacketLineageError):
        EvidenceStoreReadAdapter(LeakyRowSource()).read_packet(
            tenant_id_hash="tenant-hash-OTHER",
            agent_id=AGENT,
            agent_version=VERSION,
            evidence_batch_id="batch-x",
        )


# --- Latest-head supersession ----------------------------------------------


def test_superseded_event_is_excluded_and_correction_included():
    store = InMemoryEvidenceStore()
    store.add_manifest(
        evidence_batch_id="batch-sup",
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        manifest_content_sha256="sha256:" + "1" * 64,
        signed_manifest={"deployment_id": "deploy-1"},
        supersedes_evidence_batch_id=None,
    )
    common = dict(
        evidence_batch_id="batch-sup",
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        event_kind="tool_call",
        summary={"attributes": {}},
    )
    store.add_event(
        evidence_event_id="evt-old",
        event_timestamp=_dt("2026-07-03T00:00:00Z"),
        content_sha256="sha256:" + "2" * 64,
        supersedes_evidence_event_id=None,
        **common,
    )
    store.add_event(
        evidence_event_id="evt-correction",
        event_timestamp=_dt("2026-07-03T00:00:00Z"),
        content_sha256="sha256:" + "3" * 64,
        supersedes_evidence_event_id="evt-old",
        **common,
    )

    packet = EvidenceStoreReadAdapter(store).read_packet(
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        evidence_batch_id="batch-sup",
    )
    ids = [e.evidence_event_id for e in packet.events]
    assert ids == ["evt-correction"]
    assert "evt-old" not in ids


def test_requesting_a_superseded_batch_id_is_not_found():
    store = InMemoryEvidenceStore()
    store.add_manifest(
        evidence_batch_id="batch-old",
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        manifest_content_sha256="sha256:" + "1" * 64,
        signed_manifest={},
        supersedes_evidence_batch_id=None,
    )
    store.add_manifest(
        evidence_batch_id="batch-new",
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        manifest_content_sha256="sha256:" + "9" * 64,
        signed_manifest={},
        supersedes_evidence_batch_id="batch-old",
    )
    adapter = EvidenceStoreReadAdapter(store)
    # The head is readable...
    assert adapter.read_packet(
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        evidence_batch_id="batch-new",
    ).evidence_batch_id == "batch-new"
    # ...the superseded id is excluded by the latest-head view.
    with pytest.raises(EvidencePacketNotFoundError):
        adapter.read_packet(
            tenant_id_hash=ALPHA,
            agent_id=AGENT,
            agent_version=VERSION,
            evidence_batch_id="batch-old",
        )


# --- Deterministic ordering -------------------------------------------------


def test_events_are_ordered_by_timestamp_then_event_id_regardless_of_insert_order():
    store = InMemoryEvidenceStore()
    store.add_manifest(
        evidence_batch_id="batch-ord",
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        manifest_content_sha256="sha256:" + "1" * 64,
        signed_manifest={},
        supersedes_evidence_batch_id=None,
    )
    common = dict(
        evidence_batch_id="batch-ord",
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        event_kind="tool_call",
        summary={"attributes": {}},
        supersedes_evidence_event_id=None,
    )
    # Inserted out of order; note the tie at 00:00:02 resolved by event id.
    store.add_event(evidence_event_id="evt-c", event_timestamp=_dt("2026-07-03T00:00:03Z"),
                    content_sha256="sha256:" + "c" * 64, **common)
    store.add_event(evidence_event_id="evt-b2", event_timestamp=_dt("2026-07-03T00:00:02Z"),
                    content_sha256="sha256:" + "b" * 64, **common)
    store.add_event(evidence_event_id="evt-a", event_timestamp=_dt("2026-07-03T00:00:01Z"),
                    content_sha256="sha256:" + "a" * 64, **common)
    store.add_event(evidence_event_id="evt-b1", event_timestamp=_dt("2026-07-03T00:00:02Z"),
                    content_sha256="sha256:" + "d" * 64, **common)

    packet = EvidenceStoreReadAdapter(store).read_packet(
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        evidence_batch_id="batch-ord",
    )
    assert [e.evidence_event_id for e in packet.events] == [
        "evt-a",
        "evt-b1",
        "evt-b2",
        "evt-c",
    ]
    # Timestamps normalized to ISO-8601 UTC with a Z suffix.
    assert packet.events[0].event_timestamp == "2026-07-03T00:00:01Z"


# --- Content-hash aggregation -----------------------------------------------


def test_content_hashes_are_sorted_unique_and_scoped_to_selected_events():
    store = InMemoryEvidenceStore()
    store.add_manifest(
        evidence_batch_id="batch-ch",
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        manifest_content_sha256="sha256:" + "1" * 64,
        signed_manifest={},
        supersedes_evidence_batch_id=None,
    )
    common = dict(
        evidence_batch_id="batch-ch",
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        event_kind="tool_call",
        summary={"attributes": {}},
        supersedes_evidence_event_id=None,
    )
    store.add_event(evidence_event_id="evt-1", event_timestamp=_dt("2026-07-03T00:00:01Z"),
                    content_sha256="sha256:" + "b" * 64, **common)
    store.add_event(evidence_event_id="evt-2", event_timestamp=_dt("2026-07-03T00:00:02Z"),
                    content_sha256="sha256:" + "a" * 64, **common)
    h_b = "sha256:" + "b" * 64
    h_a = "sha256:" + "a" * 64
    # Duplicate rows for the same hash + an out-of-order pair.
    store.add_content_hash(evidence_event_id="evt-1", tenant_id_hash=ALPHA, content_sha256=h_b)
    store.add_content_hash(evidence_event_id="evt-1", tenant_id_hash=ALPHA, content_sha256=h_b)
    store.add_content_hash(evidence_event_id="evt-2", tenant_id_hash=ALPHA, content_sha256=h_a)
    # A stray content hash for another tenant must never bleed in.
    store.add_content_hash(evidence_event_id="evt-2", tenant_id_hash="tenant-hash-OTHER",
                           content_sha256="sha256:" + "f" * 64)

    packet = EvidenceStoreReadAdapter(store).read_packet(
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        evidence_batch_id="batch-ch",
    )
    assert packet.content_hashes == (h_a, h_b)


# --- Fail-closed shape / lineage errors -------------------------------------


def test_not_found_when_no_manifest_head():
    store = InMemoryEvidenceStore()
    with pytest.raises(EvidencePacketNotFoundError):
        EvidenceStoreReadAdapter(store).read_packet(
            tenant_id_hash=ALPHA,
            agent_id=AGENT,
            agent_version=VERSION,
            evidence_batch_id="batch-missing",
        )


def test_missing_manifest_content_sha256_fails_shape_closed():
    store = InMemoryEvidenceStore()
    store.add_manifest(
        evidence_batch_id="batch-bad",
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        manifest_content_sha256=None,
        signed_manifest={},
        supersedes_evidence_batch_id=None,
    )
    with pytest.raises(EvidencePacketShapeError):
        EvidenceStoreReadAdapter(store).read_packet(
            tenant_id_hash=ALPHA,
            agent_id=AGENT,
            agent_version=VERSION,
            evidence_batch_id="batch-bad",
        )


def test_non_object_summary_fails_shape_closed():
    store = InMemoryEvidenceStore()
    store.add_manifest(
        evidence_batch_id="batch-bad-evt",
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        manifest_content_sha256="sha256:" + "1" * 64,
        signed_manifest={},
        supersedes_evidence_batch_id=None,
    )
    store.add_event(
        evidence_event_id="evt-x",
        evidence_batch_id="batch-bad-evt",
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        event_kind="tool_call",
        event_timestamp=_dt("2026-07-03T00:00:01Z"),
        summary="not-an-object",
        content_sha256="sha256:" + "2" * 64,
        supersedes_evidence_event_id=None,
    )
    with pytest.raises(EvidencePacketShapeError):
        EvidenceStoreReadAdapter(store).read_packet(
            tenant_id_hash=ALPHA,
            agent_id=AGENT,
            agent_version=VERSION,
            evidence_batch_id="batch-bad-evt",
        )


def test_non_object_attributes_are_not_silently_coerced():
    store = InMemoryEvidenceStore()
    store.add_manifest(
        evidence_batch_id="batch-bad-attr",
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        manifest_content_sha256="sha256:" + "1" * 64,
        signed_manifest={},
        supersedes_evidence_batch_id=None,
    )
    store.add_event(
        evidence_event_id="evt-y",
        evidence_batch_id="batch-bad-attr",
        tenant_id_hash=ALPHA,
        agent_id=AGENT,
        agent_version=VERSION,
        event_kind="tool_call",
        event_timestamp=_dt("2026-07-03T00:00:01Z"),
        summary={"attributes": ["not", "an", "object"]},
        content_sha256="sha256:" + "2" * 64,
        supersedes_evidence_event_id=None,
    )
    with pytest.raises(EvidencePacketShapeError):
        EvidenceStoreReadAdapter(store).read_packet(
            tenant_id_hash=ALPHA,
            agent_id=AGENT,
            agent_version=VERSION,
            evidence_batch_id="batch-bad-attr",
        )


# --- Live Postgres parity (opt-in) ------------------------------------------


def test_live_postgres_adapter_parity_if_dsn_is_available():
    dsn = os.getenv("EVIDENCE_STORE_PG_DSN")
    if not dsn:
        pytest.skip(
            "EVIDENCE_STORE_PG_DSN not set; in-memory view emulation covers this runtime"
        )
    try:
        import psycopg  # type: ignore

        connect = psycopg.connect
    except ImportError:  # pragma: no cover - environment dependent
        try:
            import psycopg2  # type: ignore

            connect = psycopg2.connect
        except ImportError:
            pytest.skip("no psycopg/psycopg2 driver available for live parity test")

    seed = textwrap.dedent(
        f"""
        \\set ON_ERROR_STOP on
        DROP SCHEMA IF EXISTS evidence_store CASCADE;
        \\i {EVIDENCE_MIGRATION}

        INSERT INTO evidence_store.signed_batch_manifests (
            evidence_batch_id, tenant_id_hash, tenant_identity, agent_id, agent_version,
            scanner_version, corpus_version, probe_set_version, collector_version,
            signed_manifest, manifest_signature_algorithm, manifest_signature,
            manifest_content_sha256, retention_policy_key, received_at
        ) VALUES (
            '00000000-0000-0000-0000-0000000000aa', 'tenant-hash-alpha', '{{"t":"a"}}',
            'agent-smith-ai-001', 'v1', 'scanner-v1', 'corpus-v1', 'probe-v1', 'collector-v1',
            '{{"deployment_id":"deploy-prod-1"}}', 'EdDSA', 'sig',
            'sha256:45ea309de5a00ac904ba2e529061378a436ee3f2c0f02ef7adc4c389fba692d1',
            'runtime_evidence_counsel_pending_v1', '2026-07-03T00:00:00Z'
        );

        INSERT INTO evidence_store.structured_event_summaries (
            evidence_event_id, evidence_batch_id, tenant_id_hash, agent_id, agent_version,
            collector_version, event_kind, event_timestamp, rubric_point_id, summary, content_sha256
        ) VALUES (
            '10000000-0000-0000-0000-0000000000aa',
            '00000000-0000-0000-0000-0000000000aa',
            'tenant-hash-alpha', 'agent-smith-ai-001', 'v1', 'collector-v1', 'data_access',
            '2026-07-03T12:00:00Z', 'P4.1',
            '{{"attributes":{{"action_class":"data_access"}},"trace_id":"trace-1","span_id":"span-1"}}',
            'sha256:e6897afa06220f9013f8aaaa18149d9427bf4dae9616cff7ddb7e489f44eca3c'
        );

        INSERT INTO evidence_store.evidence_content_hashes (
            evidence_event_id, tenant_id_hash, content_sha256, content_uri
        ) VALUES (
            '10000000-0000-0000-0000-0000000000aa', 'tenant-hash-alpha',
            'sha256:e6897afa06220f9013f8aaaa18149d9427bf4dae9616cff7ddb7e489f44eca3c',
            's3://evidence/x'
        );
        """
    )
    proc = subprocess.run(
        ["psql", dsn, "-v", "ON_ERROR_STOP=1"],
        input=seed,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    conn = connect(dsn)
    try:
        adapter = EvidenceStoreReadAdapter.from_connection(conn)
        packet = adapter.read_packet(
            tenant_id_hash="tenant-hash-alpha",
            agent_id="agent-smith-ai-001",
            agent_version="v1",
            evidence_batch_id="00000000-0000-0000-0000-0000000000aa",
        )
    finally:
        conn.close()

    assert packet.deployment_id == "deploy-prod-1"
    assert packet.scanner_version == "scanner-v1"
    assert len(packet.events) == 1
    assert packet.events[0].event_timestamp == "2026-07-03T12:00:00Z"
    assert packet.events[0].attributes == {"action_class": "data_access"}
    assert packet.events[0].trace_id == "trace-1"
    assert packet.content_hashes == (
        "sha256:e6897afa06220f9013f8aaaa18149d9427bf4dae9616cff7ddb7e489f44eca3c",
    )


def test_postgres_row_source_queries_only_latest_views_and_full_lineage():
    # Guard the SQL text so a future edit can't silently widen the filter or read
    # a base table instead of a latest-head view.
    import determination.evidence_source as mod

    for sql in (mod._MANIFEST_SQL, mod._EVENTS_SQL):
        assert "latest_" in sql
        for field in ("tenant_id_hash", "agent_id", "agent_version", "evidence_batch_id"):
            assert field in sql
    assert "evidence_content_hashes" in mod._CONTENT_HASHES_SQL
    assert "tenant_id_hash" in mod._CONTENT_HASHES_SQL
    assert isinstance(PostgresEvidenceRowSource(object()), PostgresEvidenceRowSource)
