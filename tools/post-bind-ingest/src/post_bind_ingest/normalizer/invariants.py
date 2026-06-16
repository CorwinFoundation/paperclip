"""Cross-event invariants 1–5 (Log Event Schema v1.0 §Cross-event invariants).

The normalizer pipes a stream of typed events through this enforcer after the
per-event field shape gate. Violations emit a diagnostic
`security_event(pattern_family="other", detector_id="schema.session_continuity")`
or `claim_evidence_unresolved` and (per invariant 4) cause the offending event
to be dropped from the output stream and recorded in the dead-letter queue.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from .transcript_sha import sha256_of


# Event types that require a `turn_index` per Schema §Common envelope.
_TURN_INDEXED = frozenset({"llm_completion", "tool_call", "tool_source_reference", "escalation"})

# Event types that require a preceding `agent_invocation` per Schema invariant 1.
_REQUIRES_INVOCATION = frozenset(
    {"llm_completion", "tool_call", "tool_source_reference", "escalation",
     "error", "pii_emission", "security_event"}
)


@dataclass
class InvariantSessionState:
    invocation_seen: bool = False
    last_turn_index: int = -1
    tool_call_event_ids: set[str] = field(default_factory=set)
    seen_event_ids: dict[str, str] = field(default_factory=dict)  # event_id -> event_type


@dataclass
class InvariantReport:
    """Diagnostic emitted when an invariant is violated.

    Caller turns this into a `security_event` (invariants 1, 2, 3) or drops
    the event and emits a `security_event` (invariant 4).
    """
    invariant: int
    session_id: str
    event_id: str | None
    detail: str
    detector_id: str = "schema.session_continuity"


def enforce(events: Iterable[dict]) -> Iterator[tuple[dict, list[InvariantReport]]]:
    """Yield `(event, reports)` pairs in input order.

    `reports` is the list of invariant violations the event triggered. Invariant
    4 (claim_evidence_unresolved) is enforced at sink time (BEAAA-5867), not at
    normalizer time, because `claim_candidate_event` is detector-emitted and
    never flows through this normalizer. Invariant 5 (schema-version stability)
    is enforced at the gateway, before the normalizer — see
    `ingest_gateway.manifest_schema.validate_manifest`.
    """
    sessions: dict[str, InvariantSessionState] = {}
    schema_version_seen: str | None = None
    for event in events:
        reports: list[InvariantReport] = []
        sv = event.get("schema_version")
        if schema_version_seen is None:
            schema_version_seen = sv
        elif sv != schema_version_seen:
            # Invariant 5 defense-in-depth: gateway rejects mixed-version
            # manifests, but a manifest-spanning normalizer pass also checks.
            reports.append(
                InvariantReport(
                    invariant=5,
                    session_id=str(event.get("session_id", "")),
                    event_id=str(event.get("event_id", "")),
                    detail=f"schema_version drift: {schema_version_seen!r} vs {sv!r}",
                    detector_id="schema.schema_version_stability",
                )
            )
        session_id = event.get("session_id")
        event_type = event.get("event_type")
        event_id = event.get("event_id")
        if not isinstance(session_id, str):
            yield event, reports
            continue
        state = sessions.setdefault(session_id, InvariantSessionState())

        # Invariant 1: session continuity
        if event_type == "agent_invocation":
            state.invocation_seen = True
        elif event_type in _REQUIRES_INVOCATION and not state.invocation_seen:
            reports.append(
                InvariantReport(
                    invariant=1,
                    session_id=session_id,
                    event_id=event_id,
                    detail=f"event {event_type} before agent_invocation for session {session_id}",
                )
            )

        # Invariant 2: turn monotonicity
        if event_type in _TURN_INDEXED:
            ti = event.get("turn_index")
            if isinstance(ti, int):
                if ti < state.last_turn_index:
                    reports.append(
                        InvariantReport(
                            invariant=2,
                            session_id=session_id,
                            event_id=event_id,
                            detail=f"turn_index {ti} < last {state.last_turn_index}",
                        )
                    )
                else:
                    state.last_turn_index = ti

        # Invariant 3: reference resolution (tool_source_reference -> tool_call)
        if event_type == "tool_call" and isinstance(event_id, str):
            state.tool_call_event_ids.add(event_id)
        elif event_type == "tool_source_reference":
            ref = event.get("paired_tool_call_event_id")
            if isinstance(ref, str) and ref not in state.tool_call_event_ids:
                reports.append(
                    InvariantReport(
                        invariant=3,
                        session_id=session_id,
                        event_id=event_id,
                        detail=f"paired_tool_call_event_id {ref} not resolved in session",
                        detector_id="schema.reference_resolution",
                    )
                )

        if isinstance(event_id, str) and isinstance(event_type, str):
            state.seen_event_ids[event_id] = event_type

        yield event, reports


def diagnostic_to_security_event(
    report: InvariantReport,
    *,
    customer_id: str,
    agent_id: str,
    manifest_sha: str,
    source_kind: str,
    t_received: str,
) -> dict:
    """Render an `InvariantReport` as a `security_event(pattern_family="other")`."""
    return {
        "schema_version": "log-event-schema-v1.0",
        "event_type": "security_event",
        "event_id": str(uuid.uuid4()),
        "customer_id": customer_id,
        "agent_id": agent_id,
        "session_id": report.session_id,
        "t_event": t_received,
        "t_received": t_received,
        "source_kind": source_kind,
        "manifest_sha": manifest_sha,
        "redaction_applied": [],
        "partial": False,
        "pattern_family": "other",
        "severity": "medium",
        "evidence_event_id": report.event_id or str(uuid.uuid4()),
        "evidence_sha": sha256_of(report.detail),
        "detector_id": report.detector_id,
    }
