"""Claim candidate write hook for Log Tracker v0.

BEAAA-6161 implements child E of BEAAA-6151 §9: post-bind claim
candidate events are written with the Scanner runs that bracket bind and
loss. This module consumes detector output; it does not import scoring,
rubric, pricing, or band code.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol


ALLOWED_CLAIM_PERILS: frozenset[str] = frozenset(
    {"P1", "P2", "P3", "P4", "P5", "P6"}
)
ALLOWED_SEVERITY_CLASSES: frozenset[str] = frozenset(
    {"S0", "S1", "S2", "S3", "S4"}
)
ALLOWED_CLAIM_KINDS: frozenset[str] = frozenset(
    {"candidate", "submitted", "paid", "denied"}
)


class ClaimEventError(ValueError):
    """Raised when a claim event violates the tracker contract."""


class ClaimEventSink(Protocol):
    """Storage boundary consumed by the post-bind claim hook."""

    def append_claim_event(self, event: "ClaimEvent") -> dict[str, Any]:
        """Append ``event`` and return the stored row projection."""


@dataclass(frozen=True)
class ClaimEvent:
    """Validated claim event envelope for Log Tracker v0."""

    claim_id: str
    policy_id: str
    agent_id: str
    customer_id: str
    peril: str
    severity_class: str
    claim_kind: str
    event_t: str
    metadata: dict[str, Any]
    redaction_class: dict[str, str]
    evidence_refs_uri: str | None = None
    claim_event_id: str = field(default_factory=lambda: str(uuid.uuid4()))


def emit_claim_candidate_event(
    *,
    sink: ClaimEventSink,
    claim_id: str,
    policy_id: str,
    agent_id: str,
    customer_id: str,
    peril: str,
    severity_class: str,
    event_t: str,
    redaction_class: Mapping[str, str],
    metadata: Mapping[str, Any] | None = None,
    evidence_refs_uri: str | None = None,
    claim_event_id: str | None = None,
    claim_kind: str = "candidate",
) -> dict[str, Any]:
    """Validate and append a claim candidate event.

    ``scan_id_at_bind`` and ``scan_id_at_loss`` are resolved by the sink from
    persisted policy and scanner rows. Missing brackets fail closed unless a
    candidate event explicitly sets ``metadata.bracket_status = "pending"``.
    """
    event = ClaimEvent(
        claim_event_id=claim_event_id or str(uuid.uuid4()),
        claim_id=_required_text(claim_id, "claim_id"),
        policy_id=_required_text(policy_id, "policy_id"),
        agent_id=_required_text(agent_id, "agent_id"),
        customer_id=_required_text(customer_id, "customer_id"),
        peril=_validate_enum(peril, ALLOWED_CLAIM_PERILS, "peril"),
        severity_class=_validate_enum(
            severity_class, ALLOWED_SEVERITY_CLASSES, "severity_class"
        ),
        claim_kind=_validate_enum(claim_kind, ALLOWED_CLAIM_KINDS, "claim_kind"),
        event_t=_validate_event_t(event_t),
        evidence_refs_uri=_optional_text(evidence_refs_uri, "evidence_refs_uri"),
        metadata=_validate_mapping(metadata or {}, "metadata"),
        redaction_class=_validate_redaction_class(redaction_class),
    )
    return sink.append_claim_event(event)


class SQLiteClaimEventSink:
    """Small durable sink used by tests and local harnesses.

    Production Log Tracker v0 is Postgres per BEAAA-6151 §3. This SQLite
    adapter mirrors the claim-event columns and bracketing semantics without
    introducing a database service into unit tests.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scanner_runs (
                scan_id       TEXT PRIMARY KEY,
                agent_id      TEXT NOT NULL,
                run_timestamp TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS policy_events (
                policy_event_id TEXT PRIMARY KEY,
                policy_id       TEXT NOT NULL,
                agent_id        TEXT NOT NULL,
                customer_id     TEXT NOT NULL,
                event_kind      TEXT NOT NULL,
                scan_id_priced  TEXT,
                event_t         TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS claim_events (
                claim_event_id   TEXT PRIMARY KEY,
                claim_id         TEXT NOT NULL,
                policy_id        TEXT NOT NULL,
                agent_id         TEXT NOT NULL,
                customer_id      TEXT NOT NULL,
                peril            TEXT NOT NULL,
                severity_class   TEXT NOT NULL,
                claim_kind       TEXT NOT NULL,
                event_t          TEXT NOT NULL,
                scan_id_at_bind  TEXT,
                scan_id_at_loss  TEXT,
                evidence_refs_uri TEXT,
                metadata         TEXT NOT NULL,
                redaction_class  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS ix_claim_events_claim_event_t
                ON claim_events (claim_id, event_t);
            CREATE INDEX IF NOT EXISTS ix_claim_events_policy_event_t
                ON claim_events (policy_id, event_t);
            CREATE INDEX IF NOT EXISTS ix_claim_events_peril_event_t
                ON claim_events (peril, event_t);
            CREATE INDEX IF NOT EXISTS ix_claim_events_scan_bind
                ON claim_events (scan_id_at_bind);
            CREATE INDEX IF NOT EXISTS ix_claim_events_scan_loss
                ON claim_events (scan_id_at_loss);
            """
        )

    def seed_scanner_run(
        self, *, scan_id: str, agent_id: str, run_timestamp: str
    ) -> None:
        self._conn.execute(
            "INSERT INTO scanner_runs (scan_id, agent_id, run_timestamp) "
            "VALUES (?, ?, ?)",
            (
                _required_text(scan_id, "scan_id"),
                _required_text(agent_id, "agent_id"),
                _validate_event_t(run_timestamp),
            ),
        )

    def seed_policy_event(
        self,
        *,
        policy_id: str,
        agent_id: str,
        customer_id: str,
        event_kind: str,
        scan_id_priced: str | None,
        event_t: str,
        policy_event_id: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO policy_events "
            "(policy_event_id, policy_id, agent_id, customer_id, event_kind, "
            "scan_id_priced, event_t) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                policy_event_id or str(uuid.uuid4()),
                _required_text(policy_id, "policy_id"),
                _required_text(agent_id, "agent_id"),
                _required_text(customer_id, "customer_id"),
                _required_text(event_kind, "event_kind"),
                _optional_text(scan_id_priced, "scan_id_priced"),
                _validate_event_t(event_t),
            ),
        )

    def append_claim_event(self, event: ClaimEvent) -> dict[str, Any]:
        scan_id_at_bind = self._scan_id_at_bind(event)
        scan_id_at_loss = self._scan_id_at_loss(event)
        bracket_pending = (
            event.claim_kind == "candidate"
            and event.metadata.get("bracket_status") == "pending"
        )
        if (scan_id_at_bind is None or scan_id_at_loss is None) and not bracket_pending:
            raise ClaimEventError("scan_bracket_required")
        self._conn.execute(
            "INSERT INTO claim_events "
            "(claim_event_id, claim_id, policy_id, agent_id, customer_id, "
            "peril, severity_class, claim_kind, event_t, scan_id_at_bind, "
            "scan_id_at_loss, evidence_refs_uri, metadata, redaction_class) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.claim_event_id,
                event.claim_id,
                event.policy_id,
                event.agent_id,
                event.customer_id,
                event.peril,
                event.severity_class,
                event.claim_kind,
                event.event_t,
                scan_id_at_bind,
                scan_id_at_loss,
                event.evidence_refs_uri,
                json.dumps(event.metadata, sort_keys=True),
                json.dumps(event.redaction_class, sort_keys=True),
            ),
        )
        row = self._conn.execute(
            "SELECT * FROM claim_events WHERE claim_event_id = ?",
            (event.claim_event_id,),
        ).fetchone()
        return _row_to_dict(row)

    def list_claim_events(self, policy_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM claim_events WHERE policy_id = ? ORDER BY event_t ASC",
            (policy_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def count_missing_brackets(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM claim_events "
            "WHERE scan_id_at_bind IS NULL OR scan_id_at_loss IS NULL"
        ).fetchone()
        return int(row["n"])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SQLiteClaimEventSink":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _scan_id_at_bind(self, event: ClaimEvent) -> str | None:
        row = self._conn.execute(
            "SELECT scan_id_priced FROM policy_events "
            "WHERE policy_id = ? AND agent_id = ? AND event_kind = 'bind' "
            "AND event_t <= ? ORDER BY event_t DESC LIMIT 1",
            (event.policy_id, event.agent_id, event.event_t),
        ).fetchone()
        if row is None:
            return None
        return row["scan_id_priced"]

    def _scan_id_at_loss(self, event: ClaimEvent) -> str | None:
        row = self._conn.execute(
            "SELECT scan_id FROM scanner_runs "
            "WHERE agent_id = ? AND run_timestamp <= ? "
            "ORDER BY run_timestamp DESC LIMIT 1",
            (event.agent_id, event.event_t),
        ).fetchone()
        if row is None:
            return None
        return row["scan_id"]


def _validate_enum(value: Any, allowed: frozenset[str], field_name: str) -> str:
    value = _required_text(value, field_name)
    if value not in allowed:
        raise ClaimEventError(f"{field_name}_invalid:{value}")
    return value


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or value == "":
        raise ClaimEventError(f"{field_name}_required")
    return value


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value == "":
        raise ClaimEventError(f"{field_name}_invalid")
    return value


def _validate_event_t(value: Any) -> str:
    event_t = _required_text(value, "event_t")
    try:
        datetime.fromisoformat(event_t.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ClaimEventError("event_t_invalid") from exc
    return event_t


def _validate_mapping(value: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ClaimEventError(f"{field_name}_invalid")
    return dict(value)


def _validate_redaction_class(value: Mapping[str, str]) -> dict[str, str]:
    redaction_class = _validate_mapping(value, "redaction_class")
    if not redaction_class:
        raise ClaimEventError("redaction_class_required")
    for field_path, class_name in redaction_class.items():
        if not isinstance(field_path, str) or field_path == "":
            raise ClaimEventError("redaction_class_path_invalid")
        if class_name not in {"clear", "hash", "redact-on-receipt", "drop"}:
            raise ClaimEventError(f"redaction_class_invalid:{field_path}")
    return redaction_class


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    out = {key: row[key] for key in row.keys()}
    out["metadata"] = json.loads(out["metadata"])
    out["redaction_class"] = json.loads(out["redaction_class"])
    return out


__all__ = [
    "ALLOWED_CLAIM_KINDS",
    "ALLOWED_CLAIM_PERILS",
    "ALLOWED_SEVERITY_CLASSES",
    "ClaimEvent",
    "ClaimEventError",
    "SQLiteClaimEventSink",
    "emit_claim_candidate_event",
]
