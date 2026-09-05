from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

from .invariants import canonical_dumps


class CausalLineageError(ValueError):
    """A causal trace cannot be persisted or reconstructed unambiguously."""


@dataclass(frozen=True)
class CausalTrace:
    trace_id: str
    events: tuple[dict[str, Any], ...]
    loop_depth: int
    retries: int
    handoffs: int
    input_tokens: int
    output_tokens: int
    duration_ns: int
    terminal_outcome: str
    verification_failed: bool


def apply_causal_migrations(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS causal_spans (
            tenant_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            span_id TEXT NOT NULL,
            parent_span_id TEXT,
            started_at TEXT NOT NULL,
            event_json TEXT NOT NULL,
            event_sha256 TEXT NOT NULL,
            PRIMARY KEY (tenant_id, agent_id, trace_id, span_id)
        );
        CREATE INDEX IF NOT EXISTS causal_spans_parent_idx
            ON causal_spans(tenant_id, agent_id, trace_id, parent_span_id, started_at, span_id);
        CREATE TRIGGER IF NOT EXISTS causal_spans_no_update BEFORE UPDATE ON causal_spans
            BEGIN SELECT RAISE(ABORT, 'causal_spans_append_only'); END;
        CREATE TRIGGER IF NOT EXISTS causal_spans_no_delete BEFORE DELETE ON causal_spans
            BEGIN SELECT RAISE(ABORT, 'causal_spans_append_only'); END;
    """)


def append_causal_events(
    db: sqlite3.Connection, *, tenant_id: str, agent_id: str, events: Iterable[dict[str, Any]]
) -> None:
    rows = []
    for event in events:
        causal = event.get("payload", {}).get("causal")
        digest = event.get("event_ingest", {}).get("event_digest")
        if not isinstance(causal, dict) or not isinstance(digest, str):
            raise CausalLineageError("normalized causal event and signed event digest are required")
        rows.append((tenant_id, agent_id, causal["trace_id"], causal["span_id"], causal["parent_span_id"], causal["timing"]["started_at"], canonical_dumps(event).decode(), digest))
    try:
        with db:
            db.executemany("INSERT INTO causal_spans VALUES (?,?,?,?,?,?,?,?)", rows)
    except sqlite3.IntegrityError as exc:
        raise CausalLineageError("causal span replay or lineage collision rejected") from exc


def reconstruct_trace(db: sqlite3.Connection, *, tenant_id: str, agent_id: str, trace_id: str) -> CausalTrace:
    rows = db.execute(
        "SELECT event_json FROM causal_spans WHERE tenant_id=? AND agent_id=? AND trace_id=? ORDER BY started_at, span_id",
        (tenant_id, agent_id, trace_id),
    ).fetchall()
    events = tuple(json.loads(row[0]) for row in rows)
    if not events:
        raise CausalLineageError("trace not found")
    by_span = {event["payload"]["causal"]["span_id"]: event for event in events}
    if len(by_span) != len(events):
        raise CausalLineageError("duplicate span identity")
    roots = []
    depths: dict[str, int] = {}
    pending = list(events)
    while pending:
        progressed = False
        for event in pending[:]:
            causal = event["payload"]["causal"]
            parent = causal["parent_span_id"]
            if parent is None:
                roots.append(causal["span_id"])
                depths[causal["span_id"]] = 0
            elif parent in depths:
                depths[causal["span_id"]] = depths[parent] + 1
            else:
                continue
            pending.remove(event)
            progressed = True
        if not progressed:
            raise CausalLineageError("orphan or cyclic parent lineage")
    if len(roots) != 1:
        raise CausalLineageError("trace must have exactly one root")
    events = tuple(sorted(events, key=lambda event: (
        depths[event["payload"]["causal"]["span_id"]],
        event["payload"]["causal"]["timing"]["started_at"],
        event["payload"]["causal"]["span_id"],
    )))
    last = events[-1]["payload"]["causal"]
    terminal_code = last["status"]["code"]
    terminal = "error" if terminal_code in (2, "STATUS_CODE_ERROR", "ERROR") else "ok"
    return CausalTrace(
        trace_id=trace_id,
        events=events,
        loop_depth=max(depths.values()),
        retries=sum("retry.of_span_id" in e["payload"]["causal"]["facts"] for e in events),
        handoffs=sum("handoff.from_agent_id" in e["payload"]["causal"]["facts"] for e in events),
        input_tokens=sum(int(e["payload"]["span"].get("gen_ai.usage.input_tokens", 0)) for e in events),
        output_tokens=sum(int(e["payload"]["span"].get("gen_ai.usage.output_tokens", 0)) for e in events),
        duration_ns=sum(int(e["payload"]["causal"]["timing"]["duration_ns"]) for e in events),
        terminal_outcome=terminal,
        verification_failed=any(e["payload"]["causal"]["facts"].get("verification.status") == "failed" for e in events),
    )
