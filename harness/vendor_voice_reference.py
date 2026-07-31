"""Synthetic vendor-side voice determination reference for BEAAA-19991.

The raw synthetic transcript is consumed in-process and is included only in a
deterministic commitment preimage.  The public result has a closed, content-free
schema.  This module intentionally contains no transport or platform client.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


SCHEMA = "vendor-voice-determination/v1"
DETERMINATIONS = frozenset({"allow", "review", "deny"})
EVENT_OUTCOMES = frozenset({"allowed", "flagged", "blocked"})
_OUTPUT_KEYS = frozenset({"determination", "action_class_counts", "evidence_commitment_hash"})


class VoiceReferenceError(ValueError):
    """Raised for invalid input without reflecting content in the message."""


@dataclass(frozen=True)
class NormalizedVoiceEvent:
    sequence: int
    event_name: str
    action_class: str
    outcome: str
    transcript: str


class VoiceEventAdapter(Protocol):
    """Pluggable boundary for future platform event/signature naming."""

    def normalize(self, payload: Mapping[str, Any]) -> Sequence[NormalizedVoiceEvent]:
        """Normalize a local vendor payload; implementations must not perform I/O."""


class SyntheticVoiceEventAdapter:
    """Adapter for the repository's synthetic reference-platform fixture only."""

    def normalize(self, payload: Mapping[str, Any]) -> Sequence[NormalizedVoiceEvent]:
        if not isinstance(payload, Mapping) or set(payload) != {"schema", "events"}:
            raise VoiceReferenceError("invalid synthetic voice envelope")
        if payload.get("schema") != "synthetic-voice-events/v1":
            raise VoiceReferenceError("unsupported synthetic voice schema")
        raw_events = payload.get("events")
        if not isinstance(raw_events, list) or not raw_events:
            raise VoiceReferenceError("synthetic voice events must be a non-empty list")

        events: list[NormalizedVoiceEvent] = []
        for index, raw in enumerate(raw_events, start=1):
            if not isinstance(raw, Mapping) or set(raw) != {
                "event_name", "action_class", "outcome", "transcript"
            }:
                raise VoiceReferenceError("invalid synthetic voice event")
            values = [raw.get("event_name"), raw.get("action_class"), raw.get("outcome"), raw.get("transcript")]
            if not all(isinstance(value, str) and value for value in values):
                raise VoiceReferenceError("synthetic voice event fields must be non-empty strings")
            if raw["outcome"] not in EVENT_OUTCOMES:
                raise VoiceReferenceError("unsupported synthetic voice event outcome")
            events.append(NormalizedVoiceEvent(
                sequence=index,
                event_name=raw["event_name"],
                action_class=raw["action_class"],
                outcome=raw["outcome"],
                transcript=raw["transcript"],
            ))
        return events


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _commitment(events: Sequence[NormalizedVoiceEvent]) -> str:
    preimage = {
        "schema": SCHEMA,
        "events": [
            {
                "sequence": event.sequence,
                "event_name": event.event_name,
                "action_class": event.action_class,
                "outcome": event.outcome,
                "transcript": event.transcript,
            }
            for event in events
        ],
    }
    return "sha256:" + hashlib.sha256(_canonical_bytes(preimage)).hexdigest()


def _determination(events: Sequence[NormalizedVoiceEvent]) -> str:
    outcomes = {event.outcome for event in events}
    if "blocked" in outcomes:
        return "deny"
    if "flagged" in outcomes:
        return "review"
    return "allow"


def validate_output(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Enforce the exact no-content egress schema and return a JSON-safe copy."""
    if not isinstance(candidate, Mapping) or set(candidate) != _OUTPUT_KEYS:
        raise VoiceReferenceError("output violates closed determination schema")
    determination = candidate.get("determination")
    counts = candidate.get("action_class_counts")
    commitment = candidate.get("evidence_commitment_hash")
    if determination not in DETERMINATIONS:
        raise VoiceReferenceError("invalid output determination")
    if not isinstance(counts, Mapping) or not counts:
        raise VoiceReferenceError("invalid output action-class counts")
    if not all(isinstance(key, str) and key and isinstance(value, int) and not isinstance(value, bool) and value > 0
               for key, value in counts.items()):
        raise VoiceReferenceError("invalid output action-class counts")
    if not isinstance(commitment, str) or len(commitment) != 71 or not commitment.startswith("sha256:"):
        raise VoiceReferenceError("invalid output commitment")
    try:
        int(commitment[7:], 16)
    except ValueError as exc:
        raise VoiceReferenceError("invalid output commitment") from exc
    return {
        "determination": determination,
        "action_class_counts": dict(sorted(counts.items())),
        "evidence_commitment_hash": commitment,
    }


def determine_voice_payload(
    payload: Mapping[str, Any], *, adapter: VoiceEventAdapter | None = None
) -> dict[str, Any]:
    """Consume local synthetic content and emit only the closed determination."""
    events = (adapter or SyntheticVoiceEventAdapter()).normalize(payload)
    counts: dict[str, int] = {}
    for event in events:
        counts[event.action_class] = counts.get(event.action_class, 0) + 1
    return validate_output({
        "determination": _determination(events),
        "action_class_counts": counts,
        "evidence_commitment_hash": _commitment(events),
    })
