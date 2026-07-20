"""BEAAA-15883 multi-turn evidence bridge for the Passive Monitor lane.

This module is an adapter: it projects existing SAI/mock-tool behavioral
observations into the Determination Engine's EvidencePacket shape, then projects
determination candidates into the existing automatic-underwriting decision
envelope. It does not add rules, scoring math, judge behavior, or API fields.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping

from determination.records import EvidencePacket

SCANNER_VERSION = "scanner@beaaa-15883-multiturn-passive-fixture"
CORPUS_VERSION = "corpus@beaaa-15883-fixture-v1"
PROBE_SET_VERSION = "probe-set@beaaa-15883-multiturn-v1"
MODEL_VERSIONS = {"deterministic_fixture_agent": "mock-agent-session@1"}
PROMPT_VERSION = "harness/mock_tools/pilots/warranty_authority_bound_v1.yaml@fixture"
SEED = 0
RUBRIC_POINT_ID = "OPS.COVERED_EVENT.EVIDENCE_PACKAGE"


def sha256_json(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evidence_packet_from_behavioral_observation(
    observation: Mapping[str, Any],
    *,
    evidence_batch_id: str = "batch-beaaa-15883-multiturn",
    tenant_id_hash: str = "tenant-hash-beaaa-15883",
    agent_id: str = "agent-beaaa-15883",
    agent_version: str = "release-2026-07-20",
) -> EvidencePacket:
    """Build a deterministic EvidencePacket from one multi-turn observation.

    The packet uses existing Evidence Profile-style attributes that
    ``determination_rules_v1.yaml`` already evaluates. No rule expansion is
    performed here.
    """
    refs = [dict(ref) for ref in observation.get("evidence_refs", []) if isinstance(ref, Mapping)]
    observed = {
        "fixture_kind": observation.get("kind"),
        "signal": observation.get("signal"),
        "pass_k": observation.get("pass_k"),
        "end_state": observation.get("end_state"),
        "evidence_refs": refs,
        "scored": observation.get("scored"),
        "pins": {
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
            "probe_set_version": PROBE_SET_VERSION,
            "model_versions": MODEL_VERSIONS,
            "prompt_version": PROMPT_VERSION,
            "seed": SEED,
        },
    }
    event_sha = sha256_json(observed)
    manifest_sha = sha256_json(
        {
            "evidence_batch_id": evidence_batch_id,
            "agent_id": agent_id,
            "event_sha": event_sha,
            "ref_sha256": [ref.get("sha256") for ref in refs],
        }
    )
    end_state = observation.get("end_state") or {}
    attributes = {
        "action_class": "admin_change",
        # Existing rule DET-UNA-001 treats escalated unauthorized actions as
        # covered-peril candidates routed to human review.
        "outcome_status": "escalated" if end_state.get("blocked_policy") else "completed",
        "authority_context": {
            "authorized": bool(end_state.get("activation_authorized")),
            "source": "mock_warranty_authority_table",
        },
        "passive_monitor": {
            "collector_plane": "observability",
            "inline_proxy": False,
            "source": "vendor_production_environment_fixture",
        },
        "behavioral_fixture": {
            "kind": observation.get("kind"),
            "signal": observation.get("signal"),
            "pass_k": observation.get("pass_k"),
            "scored": observation.get("scored"),
            "evidence_ref_count": len(refs),
        },
    }
    return EvidencePacket.from_dict(
        {
            "evidence_batch_id": evidence_batch_id,
            "tenant_id_hash": tenant_id_hash,
            "agent_id": agent_id,
            "agent_version": agent_version,
            "manifest_content_sha256": f"sha256:{manifest_sha}",
            "deployment_id": "deployment-beaaa-15883",
            "collector_version": "passive-monitor-fixture-collector@1",
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
            "probe_set_version": PROBE_SET_VERSION,
            "manifest_signature_algorithm": "fixture-sha256",
            "content_hashes": [f"sha256:{event_sha}", f"sha256:{manifest_sha}"],
            "events": [
                {
                    "evidence_event_id": "evt-beaaa-15883-unauthorized-admin-change",
                    "event_kind": "backbond.behavioral_fixture.tool_call",
                    "event_timestamp": "2026-07-20T00:00:00Z",
                    "content_sha256": f"sha256:{event_sha}",
                    "trace_id": "trace-beaaa-15883",
                    "span_id": "span-warranty-activation",
                    "rubric_point_id": RUBRIC_POINT_ID,
                    "attributes": attributes,
                }
            ],
        }
    )


def envelope_with_determination_findings(
    base_envelope: Mapping[str, Any],
    determinations: list[Mapping[str, Any]],
    *,
    evidence_refs: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Attach determination candidates as existing ``peril_findings`` rows."""
    envelope = copy.deepcopy(dict(base_envelope))
    findings = []
    for record in determinations:
        if record.get("final_decision") != "covered_peril_candidate_pending_human_review":
            continue
        findings.append(
            {
                "peril": record.get("peril"),
                "severity": "high",
                "status": "active",
                "determination_id": record.get("determination_id"),
                "triggered_rule_id": record.get("triggered_rule_id"),
                "evidence_refs": [
                    ref.get("artifact_uri")
                    for ref in evidence_refs
                    if isinstance(ref, Mapping) and ref.get("artifact_uri")
                ],
            }
        )
    envelope["peril_findings"] = findings
    envelope["evidence_refs"] = [normalize_behavioral_evidence_ref(ref) for ref in evidence_refs]
    envelope["scanner_version"] = SCANNER_VERSION
    envelope["corpus_version"] = CORPUS_VERSION
    envelope["probe-set-version"] = PROBE_SET_VERSION
    envelope["probe_set_version"] = PROBE_SET_VERSION
    envelope["model_versions"] = MODEL_VERSIONS
    envelope["seed"] = SEED
    envelope["run_timestamp"] = "2026-07-20T00:00:00Z"
    return envelope


def normalize_behavioral_evidence_ref(ref: Mapping[str, Any]) -> dict[str, Any]:
    """Return the evidence-ref fields required by the decision engine."""
    return {
        "rubric_point_id": RUBRIC_POINT_ID,
        "test_id": str(ref.get("kind") or "multiturn_behavioral_fixture"),
        "artifact_uri": str(ref.get("artifact_uri")),
        "verdict": "pass",
        "weight": 0.0,
        "sha256": ref.get("sha256"),
        "evidence_grade": ref.get("evidence_grade", "fixture_behavioral"),
    }


__all__ = [
    "CORPUS_VERSION",
    "MODEL_VERSIONS",
    "PROBE_SET_VERSION",
    "PROMPT_VERSION",
    "RUBRIC_POINT_ID",
    "SCANNER_VERSION",
    "SEED",
    "envelope_with_determination_findings",
    "evidence_packet_from_behavioral_observation",
    "normalize_behavioral_evidence_ref",
    "sha256_json",
]
