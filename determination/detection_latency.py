"""Deterministic, reported-not-scored Passive Monitor latency records.

BEAAA-18958.  Latency is derived only from caller-supplied immutable timestamps.
It is deliberately independent of continuity freshness and BackBond scoring.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from determination.rule_dsl import canonical_json_bytes

SCHEMA_VERSION = "passive-monitor-detection-latency@1.0"
EMIT_FLAG_ENV = "BACKBOND_DETECTION_LATENCY_EMIT"


class DetectionLatencyError(ValueError):
    """The fixture or source lineage cannot produce an auditable verdict."""


@dataclass(frozen=True)
class SourceEventRef:
    evidence_event_id: str
    content_sha256: str

    def __post_init__(self) -> None:
        if not self.evidence_event_id:
            raise DetectionLatencyError("source evidence_event_id is required")
        if len(self.content_sha256) != 64:
            raise DetectionLatencyError("source content_sha256 must be 64 hex characters")
        try:
            int(self.content_sha256, 16)
        except ValueError as exc:
            raise DetectionLatencyError(
                "source content_sha256 must be 64 hex characters"
            ) from exc


@dataclass(frozen=True)
class DetectionLatencyFixture:
    fixture_rule_id: str
    fixture_rule_version: str
    sla_threshold_ms: int

    def __post_init__(self) -> None:
        if not self.fixture_rule_id or not self.fixture_rule_version:
            raise DetectionLatencyError("fixture rule id and version are required")
        if self.sla_threshold_ms < 0:
            raise DetectionLatencyError("sla_threshold_ms must be non-negative")


@dataclass(frozen=True)
class DetectionLatencyRecord:
    record_id: str
    schema_version: str
    fixture_rule_id: str
    fixture_rule_version: str
    reported_not_scored: bool
    verdict: str
    reason: str
    timestamps: Mapping[str, str | None]
    latency_ms: Mapping[str, int | None]
    sla_threshold_ms: int
    source_events: tuple[SourceEventRef, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["source_events"] = [asdict(ref) for ref in self.source_events]
        return result


def emission_enabled() -> bool:
    """Rollback switch for the additive emitter; calculation remains available."""
    return os.getenv(EMIT_FLAG_ENV, "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _parse_timestamp(name: str, value: str | None) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    normalized = str(value).strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise DetectionLatencyError(f"{name} must be RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DetectionLatencyError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _elapsed_ms(start: datetime, end: datetime) -> int:
    # Integer arithmetic over datetime's exact microsecond resolution.
    delta = end - start
    return (
        delta.days * 86_400_000
        + delta.seconds * 1_000
        + delta.microseconds // 1_000
    )


def _canonical_sources(
    source_events: Sequence[SourceEventRef],
) -> tuple[SourceEventRef, ...]:
    if not source_events:
        raise DetectionLatencyError("at least one source event is required")
    by_id: dict[str, SourceEventRef] = {}
    for ref in source_events:
        existing = by_id.get(ref.evidence_event_id)
        if existing is not None and existing.content_sha256 != ref.content_sha256:
            raise DetectionLatencyError(
                f"replayed event {ref.evidence_event_id} has conflicting content hash"
            )
        by_id[ref.evidence_event_id] = ref
    return tuple(sorted(by_id.values(), key=lambda ref: ref.evidence_event_id))


def derive_detection_latency(
    *,
    event_time: str | None,
    collector_received_at: str | None,
    determined_at: str | None,
    reviewer_routed_at: str | None,
    source_events: Sequence[SourceEventRef],
    fixture: DetectionLatencyFixture,
) -> DetectionLatencyRecord:
    """Derive an auditable SLA verdict from four immutable stage timestamps."""
    timestamps = {
        "event_time": event_time,
        "collector_received_at": collector_received_at,
        "determined_at": determined_at,
        "reviewer_routed_at": reviewer_routed_at,
    }
    parsed = {name: _parse_timestamp(name, value) for name, value in timestamps.items()}
    sources = _canonical_sources(source_events)

    missing = [name for name, value in parsed.items() if value is None]
    latency: dict[str, int | None] = {
        "event_to_collector_ms": None,
        "collector_to_determination_ms": None,
        "determination_to_routing_ms": None,
        "event_to_routing_ms": None,
    }
    if missing:
        verdict = "indeterminate"
        reason = "missing_timestamp:" + ",".join(sorted(missing))
    else:
        event = parsed["event_time"]
        receipt = parsed["collector_received_at"]
        determined = parsed["determined_at"]
        routed = parsed["reviewer_routed_at"]
        assert event is not None and receipt is not None
        assert determined is not None and routed is not None
        latency = {
            "event_to_collector_ms": _elapsed_ms(event, receipt),
            "collector_to_determination_ms": _elapsed_ms(receipt, determined),
            "determination_to_routing_ms": _elapsed_ms(determined, routed),
            "event_to_routing_ms": _elapsed_ms(event, routed),
        }
        negative = [name for name, value in latency.items() if value < 0]
        if negative:
            verdict = "invalid_clock_skew"
            reason = "non_monotonic_timestamp:" + ",".join(sorted(negative))
        elif latency["event_to_routing_ms"] <= fixture.sla_threshold_ms:
            verdict = "on_time"
            reason = "event_to_routing_within_fixture_sla"
        else:
            verdict = "late"
            reason = "event_to_routing_exceeds_fixture_sla"

    preimage = {
        "schema_version": SCHEMA_VERSION,
        "fixture_rule_id": fixture.fixture_rule_id,
        "fixture_rule_version": fixture.fixture_rule_version,
        "reported_not_scored": True,
        "verdict": verdict,
        "reason": reason,
        "timestamps": timestamps,
        "latency_ms": latency,
        "sla_threshold_ms": fixture.sla_threshold_ms,
        "source_events": [asdict(ref) for ref in sources],
    }
    record_id = hashlib.sha256(canonical_json_bytes(preimage)).hexdigest()
    return DetectionLatencyRecord(
        record_id=record_id,
        schema_version=SCHEMA_VERSION,
        fixture_rule_id=fixture.fixture_rule_id,
        fixture_rule_version=fixture.fixture_rule_version,
        reported_not_scored=True,
        verdict=verdict,
        reason=reason,
        timestamps=timestamps,
        latency_ms=latency,
        sla_threshold_ms=fixture.sla_threshold_ms,
        source_events=sources,
    )


__all__ = [
    "DetectionLatencyError",
    "DetectionLatencyFixture",
    "DetectionLatencyRecord",
    "EMIT_FLAG_ENV",
    "SCHEMA_VERSION",
    "SourceEventRef",
    "derive_detection_latency",
    "emission_enabled",
]
