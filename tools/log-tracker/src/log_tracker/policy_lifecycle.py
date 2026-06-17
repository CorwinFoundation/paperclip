"""Policy lifecycle write hook for Log Tracker v0.

BEAAA-6160 implements child D of BEAAA-6151 §9: bind/quote service
integration that writes policy lifecycle events to the tracker with the
Scanner run that priced the event.

This module is deliberately consumer-side. It validates and persists
policy-event envelopes; it does not import Scanner scoring, rubric,
pricing, or band code.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol


ALLOWED_POLICY_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "bind",
        "renewal",
        "mid_term_rescore",
        "mid_term_reprice",
        "cancel",
        "non_renewal",
    }
)

SCAN_ID_OPTIONAL_EVENT_KINDS: frozenset[str] = frozenset(
    {"cancel", "non_renewal"}
)

POLICY_EVENT_KIND_ALIASES: Mapping[str, str] = {
    "policy.bound": "bind",
    "policy.bind": "bind",
    "policy.renewed": "renewal",
    "policy.renewal": "renewal",
    "policy.mid_term_rescore": "mid_term_rescore",
    "policy.rescored": "mid_term_rescore",
    "policy.mid_term_reprice": "mid_term_reprice",
    "policy.repriced": "mid_term_reprice",
    "policy.cancelled": "cancel",
    "policy.canceled": "cancel",
    "policy.cancel": "cancel",
    "policy.non_renewed": "non_renewal",
    "policy.non_renewal": "non_renewal",
}


class PolicyLifecycleError(ValueError):
    """Raised when a policy lifecycle event violates the tracker contract."""


class PolicyLifecycleSink(Protocol):
    """Storage boundary consumed by the bind/quote service hook."""

    def append_policy_event(self, event: "PolicyLifecycleEvent") -> dict[str, Any]:
        """Append ``event`` and return the stored row projection."""


@dataclass(frozen=True)
class PolicyLifecycleEvent:
    """Validated policy lifecycle envelope for Log Tracker v0."""

    policy_id: str
    agent_id: str
    customer_id: str
    event_kind: str
    event_t: str
    wording_version: str
    redaction_class: dict[str, str]
    metadata: dict[str, Any] = field(default_factory=dict)
    scan_id_priced: str | None = None
    premium_band: str | None = None
    policy_event_id: str = field(default_factory=lambda: str(uuid.uuid4()))


def emit_policy_lifecycle_event(
    *,
    sink: PolicyLifecycleSink,
    event_kind: str,
    policy_id: str,
    agent_id: str,
    customer_id: str,
    event_t: str,
    wording_version: str,
    redaction_class: Mapping[str, str],
    metadata: Mapping[str, Any] | None = None,
    scan_id_priced: str | None = None,
    premium_band: str | None = None,
    policy_event_id: str | None = None,
) -> dict[str, Any]:
    """Validate and append a policy lifecycle event.

    ``scan_id_priced`` is required for bind, renewal, mid-term rescore, and
    mid-term reprice. It may be absent only for cancel/non-renewal cases where
    no re-score priced the action, matching BEAAA-6160 acceptance.
    """
    event = PolicyLifecycleEvent(
        policy_event_id=policy_event_id or str(uuid.uuid4()),
        policy_id=_required_text(policy_id, "policy_id"),
        agent_id=_required_text(agent_id, "agent_id"),
        customer_id=_required_text(customer_id, "customer_id"),
        event_kind=_normalize_event_kind(event_kind),
        scan_id_priced=_optional_text(scan_id_priced, "scan_id_priced"),
        event_t=_validate_event_t(event_t),
        premium_band=_optional_text(premium_band, "premium_band"),
        wording_version=_required_text(wording_version, "wording_version"),
        metadata=_validate_mapping(metadata or {}, "metadata"),
        redaction_class=_validate_redaction_class(redaction_class),
    )
    _validate_scan_join(event)
    return sink.append_policy_event(event)


class SQLitePolicyLifecycleSink:
    """Small durable sink used by tests and local harnesses.

    Production Log Tracker v0 is Postgres per BEAAA-6151 §3. This SQLite
    adapter keeps the child-D write hook executable without introducing a
    database service into unit tests; the columns mirror §3.2 exactly.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS policy_events (
                policy_event_id TEXT PRIMARY KEY,
                policy_id       TEXT NOT NULL,
                agent_id        TEXT NOT NULL,
                customer_id     TEXT NOT NULL,
                event_kind      TEXT NOT NULL,
                scan_id_priced  TEXT,
                event_t         TEXT NOT NULL,
                premium_band    TEXT,
                wording_version TEXT NOT NULL,
                metadata        TEXT NOT NULL,
                redaction_class TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS ix_policy_events_policy_event_t
                ON policy_events (policy_id, event_t);
            CREATE INDEX IF NOT EXISTS ix_policy_events_agent_event_t
                ON policy_events (agent_id, event_t);
            CREATE INDEX IF NOT EXISTS ix_policy_events_scan_id_priced
                ON policy_events (scan_id_priced);
            """
        )

    def append_policy_event(self, event: PolicyLifecycleEvent) -> dict[str, Any]:
        self._conn.execute(
            "INSERT INTO policy_events "
            "(policy_event_id, policy_id, agent_id, customer_id, event_kind, "
            "scan_id_priced, event_t, premium_band, wording_version, metadata, "
            "redaction_class) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.policy_event_id,
                event.policy_id,
                event.agent_id,
                event.customer_id,
                event.event_kind,
                event.scan_id_priced,
                event.event_t,
                event.premium_band,
                event.wording_version,
                json.dumps(event.metadata, sort_keys=True),
                json.dumps(event.redaction_class, sort_keys=True),
            ),
        )
        row = self._conn.execute(
            "SELECT * FROM policy_events WHERE policy_event_id = ?",
            (event.policy_event_id,),
        ).fetchone()
        return _row_to_dict(row)

    def list_policy_events(self, policy_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM policy_events WHERE policy_id = ? ORDER BY event_t ASC",
            (policy_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def count_missing_pricing_scan_ids(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM policy_events "
            "WHERE scan_id_priced IS NULL "
            "AND event_kind NOT IN ('cancel', 'non_renewal')"
        ).fetchone()
        return int(row["n"])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SQLitePolicyLifecycleSink":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _normalize_event_kind(value: str) -> str:
    event_kind = _required_text(value, "event_kind")
    event_kind = POLICY_EVENT_KIND_ALIASES.get(event_kind, event_kind)
    if event_kind not in ALLOWED_POLICY_EVENT_KINDS:
        raise PolicyLifecycleError(f"event_kind_invalid:{event_kind}")
    return event_kind


def _validate_scan_join(event: PolicyLifecycleEvent) -> None:
    if (
        event.event_kind not in SCAN_ID_OPTIONAL_EVENT_KINDS
        and event.scan_id_priced is None
    ):
        raise PolicyLifecycleError("scan_id_priced_required")


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or value == "":
        raise PolicyLifecycleError(f"{field_name}_required")
    return value


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value == "":
        raise PolicyLifecycleError(f"{field_name}_invalid")
    return value


def _validate_event_t(value: Any) -> str:
    event_t = _required_text(value, "event_t")
    try:
        datetime.fromisoformat(event_t.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PolicyLifecycleError("event_t_invalid") from exc
    return event_t


def _validate_mapping(value: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PolicyLifecycleError(f"{field_name}_invalid")
    return dict(value)


def _validate_redaction_class(value: Mapping[str, str]) -> dict[str, str]:
    redaction_class = _validate_mapping(value, "redaction_class")
    if not redaction_class:
        raise PolicyLifecycleError("redaction_class_required")
    for field_path, class_name in redaction_class.items():
        if not isinstance(field_path, str) or field_path == "":
            raise PolicyLifecycleError("redaction_class_path_invalid")
        if class_name not in {"clear", "hash", "redact-on-receipt", "drop"}:
            raise PolicyLifecycleError(f"redaction_class_invalid:{field_path}")
    return redaction_class


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    out = {key: row[key] for key in row.keys()}
    out["metadata"] = json.loads(out["metadata"])
    out["redaction_class"] = json.loads(out["redaction_class"])
    return out


__all__ = [
    "ALLOWED_POLICY_EVENT_KINDS",
    "POLICY_EVENT_KIND_ALIASES",
    "PolicyLifecycleError",
    "PolicyLifecycleEvent",
    "SQLitePolicyLifecycleSink",
    "emit_policy_lifecycle_event",
]
