"""Append-only Log Tracker persistence for evidence-tier-assignment/1.0."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Mapping

POLICY_VERSION = "evidence-tier-assignment/1.0"
EVENT_TYPES = {"assignment", "downgrade", "restoration", "exception", "override"}
TIERS = ("T0", "T1", "T2", "T3", "T4")


def _identity_hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def append_assignment(conn: sqlite3.Connection, assignment: Mapping[str, Any]) -> None:
    required = {"event_id", "tenant_id", "agent_id", "term_id", "policy_version", "assigned_at", "agent_tier", "status", "surface_assignments", "event_type", "before_tier", "after_tier", "scope", "actor", "reason", "approval_ref", "mapping_version"}
    if missing := sorted(required.difference(assignment)):
        raise ValueError("evidence_tier_assignment_missing:" + ",".join(missing))
    if assignment["policy_version"] != POLICY_VERSION:
        raise ValueError("evidence_tier_policy_version_invalid")
    if assignment["event_type"] not in EVENT_TYPES:
        raise ValueError("evidence_tier_event_type_invalid")
    for key in ("event_id", "tenant_id", "agent_id", "term_id", "assigned_at", "after_tier", "scope", "actor", "reason", "approval_ref", "mapping_version"):
        if not isinstance(assignment[key], str) or not assignment[key].strip():
            raise ValueError(f"evidence_tier_field_invalid:{key}")
    if assignment["after_tier"] != assignment["agent_tier"]:
        raise ValueError("evidence_tier_after_mismatch")
    before = assignment["before_tier"]
    after = assignment["after_tier"]
    if after not in TIERS or (before is not None and before not in TIERS):
        raise ValueError("evidence_tier_transition_tier_invalid")
    event_type = assignment["event_type"]
    if event_type == "assignment" and before is not None:
        raise ValueError("evidence_tier_assignment_before_forbidden")
    if event_type == "downgrade" and (before is None or TIERS.index(before) <= TIERS.index(after)):
        raise ValueError("evidence_tier_downgrade_transition_invalid")
    if event_type == "restoration" and (before is None or TIERS.index(before) >= TIERS.index(after)):
        raise ValueError("evidence_tier_restoration_transition_invalid")
    if event_type == "exception" and not str(assignment.get("exception_id") or "").strip():
        raise ValueError("evidence_tier_exception_id_required")
    if event_type == "override" and not str(assignment.get("override_id") or "").strip():
        raise ValueError("evidence_tier_override_id_required")
    snapshots = sorted({
        row.get("verification_snapshot_hash") for row in assignment["surface_assignments"]
        if row.get("verification_snapshot_hash")
    })
    stored = dict(assignment)
    for key in ("tenant_id", "agent_id", "term_id"):
        stored.pop(key, None)
    conn.execute(
        """INSERT INTO evidence_tier_events
        (event_id, tenant_id_hash, agent_id_hash, term_id_hash, policy_version,
         assigned_at, agent_tier, status, verification_snapshot_json, assignment_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (assignment["event_id"], _identity_hash(assignment["tenant_id"]), _identity_hash(assignment["agent_id"]),
         _identity_hash(assignment["term_id"]), assignment["policy_version"], assignment["assigned_at"],
         assignment["agent_tier"], assignment["status"], json.dumps(snapshots, sort_keys=True),
         json.dumps(stored, sort_keys=True, separators=(",", ":"))),
    )


def list_assignments(conn: sqlite3.Connection, *, tenant_id: str, agent_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT assignment_json FROM evidence_tier_events
           WHERE tenant_id_hash = ? AND agent_id_hash = ? ORDER BY assigned_at, event_id""",
        (_identity_hash(tenant_id), _identity_hash(agent_id)),
    ).fetchall()
    return [json.loads(row[0]) for row in rows]
