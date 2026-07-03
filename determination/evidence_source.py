"""BEAAA-11372 evidence-packet source port.

The Determination Engine reads immutable ``EvidencePacket`` snapshots through
this port. Two implementations ship in v1:

* ``FixtureEvidenceSource`` — loads packets from dict/JSON. This backs the golden
  fixtures and can replay any exported evidence packet, so the entire engine is
  reproducible in the agent runtime with no database.
* ``EvidenceStoreReadAdapter`` — the LIVE binding to the append-only evidence
  store (BEAAA-11366) read model, i.e. the ``evidence_store.latest_*`` views.
  This adapter is NOT implemented in v1 because the evidence store ships as
  Postgres DDL only (``tools/evidence-store/``) with no Python read client. That
  missing interface is tracked as a first-class blocker on BEAAA-11372; see the
  docstring below for the exact contract the store team must expose.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from determination.records import EvidencePacket


class EvidencePacketSource(Protocol):
    def read_packet(
        self,
        *,
        tenant_id_hash: str,
        agent_id: str,
        agent_version: str,
        evidence_batch_id: str,
    ) -> EvidencePacket: ...


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


class EvidenceStoreReadAdapter:
    """Live evidence-store read binding — NOT YET AVAILABLE (blocker seam).

    Required missing interface (BEAAA-11372 dependency / BEAAA-11366 store):
    a tenant-isolated Python read client over the append-only evidence store
    read model that, given ``(tenant_id_hash, agent_id, agent_version,
    evidence_batch_id)``, returns the current-head rows and assembles an
    ``EvidencePacket``:

        * ``evidence_store.latest_signed_batch_manifests`` -> batch/manifest
          identity + content hash + collector/scanner/corpus/probe versions.
        * ``evidence_store.latest_structured_event_summaries`` -> structured
          event rows (Evidence Profile v1 attributes) for that batch.
        * ``evidence_store.evidence_content_hashes`` -> referenced content hashes.

    Reads MUST stay inside the requested ``tenant_id_hash`` lineage (the store's
    tenant-isolation invariant). Until the store team exposes this client, the
    engine runs on ``FixtureEvidenceSource`` (golden fixtures + exported packet
    replay) and determination emission stays behind the feature flag.
    """

    def read_packet(self, **_kwargs: Any) -> EvidencePacket:
        raise NotImplementedError(
            "Live evidence-store read path is unavailable: the evidence store "
            "(BEAAA-11366) ships as Postgres DDL only, with no Python read "
            "client over evidence_store.latest_signed_batch_manifests / "
            "latest_structured_event_summaries. Tracked as a first-class "
            "blocker on BEAAA-11372."
        )


__all__ = [
    "EvidencePacketSource",
    "EvidenceStoreReadAdapter",
    "FixtureEvidenceSource",
]
