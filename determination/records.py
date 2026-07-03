"""BEAAA-11372 evidence-packet and determination-record types.

An ``EvidencePacket`` is the immutable, tenant-isolated snapshot the
Determination Engine reads. It mirrors the evidence-store read model
(``evidence_store.latest_signed_batch_manifests`` +
``latest_structured_event_summaries`` + ``evidence_content_hashes``, BEAAA-11366)
and carries Evidence Profile v1 attributes (BEAAA-11364). The determination is a
pure function of the packet plus the rule-set version, so it can be recomputed
from immutable evidence alone.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping

from determination.rule_dsl import canonical_json_bytes, get_path

SCHEMA_VERSION = "determination-record-preimage@1.0"

# Decision vocabulary. v1 runs a human lane first (graded autonomy): a matched
# rule produces a covered-peril *candidate* routed to a human, never an
# auto-bound coverage ruling. The deterministic decision below is fully
# reproducible; the human's later coverage ruling is a separate appended lane.
DECISION_COVERED_CANDIDATE = "covered_peril_candidate_pending_human_review"
DECISION_NO_FINDING = "no_covered_peril_finding"
DECISION_INSUFFICIENT_EVIDENCE = "insufficient_evidence_referred"


@dataclass(frozen=True)
class StructuredEvent:
    """One structured runtime event summary under an evidence batch.

    Field names track Evidence Profile v1 (`backbond.evidence.*`) and the
    `evidence_store.structured_event_summaries` columns. ``attributes`` holds the
    Evidence Profile attribute bag the rules evaluate against.
    """

    evidence_event_id: str
    event_kind: str
    event_timestamp: str
    content_sha256: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    span_id: str | None = None
    rubric_point_id: str | None = None

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "StructuredEvent":
        return cls(
            evidence_event_id=str(row["evidence_event_id"]),
            event_kind=str(row.get("event_kind", "")),
            event_timestamp=str(row.get("event_timestamp", "")),
            content_sha256=str(row["content_sha256"]),
            attributes=dict(row.get("attributes", {}) or {}),
            trace_id=row.get("trace_id"),
            span_id=row.get("span_id"),
            rubric_point_id=row.get("rubric_point_id"),
        )

    def eval_view(self) -> dict[str, Any]:
        """Mapping the rule DSL evaluates against (attributes + event context)."""
        view = dict(self.attributes)
        view["event_kind"] = self.event_kind
        view["event_timestamp"] = self.event_timestamp
        return view


@dataclass(frozen=True)
class EvidencePacket:
    """Immutable, tenant-isolated view of one evidence batch and its events."""

    evidence_batch_id: str
    tenant_id_hash: str
    agent_id: str
    agent_version: str
    manifest_content_sha256: str
    events: tuple[StructuredEvent, ...]
    deployment_id: str | None = None
    collector_version: str | None = None
    scanner_version: str | None = None
    corpus_version: str | None = None
    probe_set_version: str | None = None
    manifest_signature_algorithm: str | None = None
    content_hashes: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidencePacket":
        events = tuple(
            StructuredEvent.from_dict(row) for row in data.get("events", []) or []
        )
        return cls(
            evidence_batch_id=str(data["evidence_batch_id"]),
            tenant_id_hash=str(data["tenant_id_hash"]),
            agent_id=str(data["agent_id"]),
            agent_version=str(data["agent_version"]),
            manifest_content_sha256=str(data["manifest_content_sha256"]),
            events=events,
            deployment_id=data.get("deployment_id"),
            collector_version=data.get("collector_version"),
            scanner_version=data.get("scanner_version"),
            corpus_version=data.get("corpus_version"),
            probe_set_version=data.get("probe_set_version"),
            manifest_signature_algorithm=data.get("manifest_signature_algorithm"),
            content_hashes=tuple(
                str(h) for h in (data.get("content_hashes") or [])
            ),
        )

    def packet_ref(self) -> dict[str, Any]:
        """Stable reference back to the immutable evidence packet."""
        return {
            "evidence_batch_id": self.evidence_batch_id,
            "tenant_id_hash": self.tenant_id_hash,
            "agent_id": self.agent_id,
            "agent_version": self.agent_version,
            "manifest_content_sha256": self.manifest_content_sha256,
            "event_ids": [e.evidence_event_id for e in self.events],
        }


@dataclass(frozen=True)
class DeterminationRecord:
    """The book of record for one determination over one evidence packet.

    ``determination_id`` is the SHA-256 of the canonical preimage, so two runs
    over the same immutable evidence and the same rule-set version produce the
    same id — reproducibility without an LLM in the deciding path.
    """

    determination_id: str
    determination_preimage_sha256: str
    rule_set_version: str
    engine_version: str
    peril: str | None
    triggered_rule_id: str | None
    final_decision: str
    review_required: bool
    review_owner: str
    trigger: dict[str, Any]
    source_spans: tuple[dict[str, Any], ...]
    content_hashes: tuple[str, ...]
    evidence_packet_ref: dict[str, Any]
    policy_engine_check: dict[str, Any]
    audit: dict[str, Any]
    advisory_triage: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "determination_id": self.determination_id,
            "determination_preimage_sha256": self.determination_preimage_sha256,
            "rule_set_version": self.rule_set_version,
            "engine_version": self.engine_version,
            "peril": self.peril,
            "triggered_rule_id": self.triggered_rule_id,
            "final_decision": self.final_decision,
            "review_required": self.review_required,
            "review_owner": self.review_owner,
            "trigger": dict(self.trigger),
            "source_spans": [dict(s) for s in self.source_spans],
            "content_hashes": list(self.content_hashes),
            "evidence_packet_ref": dict(self.evidence_packet_ref),
            "policy_engine_check": dict(self.policy_engine_check),
            "audit": dict(self.audit),
            # Advisory-only; excluded from the deciding-path preimage by design.
            "advisory_triage": (
                dict(self.advisory_triage)
                if self.advisory_triage is not None
                else None
            ),
        }


def determination_preimage(
    *,
    rule_set_version: str,
    engine_version: str,
    peril: str | None,
    triggered_rule_id: str | None,
    final_decision: str,
    trigger: Mapping[str, Any],
    source_spans: list[dict[str, Any]],
    content_hashes: list[str],
    evidence_packet_ref: Mapping[str, Any],
    policy_engine_check: Mapping[str, Any],
) -> dict[str, Any]:
    """Canonical hash preimage for a determination.

    Binds the determination id to: the rule-set version, the covered-peril
    decision, the triggering evidence (event/span ids + content hashes), and the
    immutable evidence packet reference. It deliberately EXCLUDES any advisory
    LLM triage output so no LLM can influence the deciding path or the id.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "rule_set_version": rule_set_version,
        "engine_version": engine_version,
        "peril": peril,
        "triggered_rule_id": triggered_rule_id,
        "final_decision": final_decision,
        "trigger": dict(sorted(trigger.items())),
        "source_spans": sorted(
            (dict(s) for s in source_spans),
            key=lambda s: str(s.get("evidence_event_id")),
        ),
        "content_hashes": sorted(content_hashes),
        "evidence_packet_ref": {
            "evidence_batch_id": evidence_packet_ref.get("evidence_batch_id"),
            "tenant_id_hash": evidence_packet_ref.get("tenant_id_hash"),
            "agent_id": evidence_packet_ref.get("agent_id"),
            "agent_version": evidence_packet_ref.get("agent_version"),
            "manifest_content_sha256": evidence_packet_ref.get(
                "manifest_content_sha256"
            ),
            "event_ids": sorted(evidence_packet_ref.get("event_ids", []) or []),
        },
        "policy_engine_check": dict(sorted(policy_engine_check.items())),
    }


def determination_id(preimage: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(preimage)).hexdigest()


__all__ = [
    "DECISION_COVERED_CANDIDATE",
    "DECISION_INSUFFICIENT_EVIDENCE",
    "DECISION_NO_FINDING",
    "DeterminationRecord",
    "EvidencePacket",
    "SCHEMA_VERSION",
    "StructuredEvent",
    "determination_id",
    "determination_preimage",
    "get_path",
]
