from __future__ import annotations

import pytest
import yaml

from determination.detection_latency import (
    DetectionLatencyError,
    DetectionLatencyFixture,
    SourceEventRef,
    derive_detection_latency,
)


@pytest.fixture
def latency_fixture() -> DetectionLatencyFixture:
    raw = yaml.safe_load(
        open(
            "determination/fixtures/detection_latency/breach_latency_v1.yaml",
            encoding="utf-8",
        )
    )
    assert raw["scope"] == "fixture_only"
    assert raw["reported_not_scored"] is True
    assert raw["freshness_policy"] == "excluded"
    return DetectionLatencyFixture(
        fixture_rule_id=raw["fixture_rule_id"],
        fixture_rule_version=raw["fixture_rule_version"],
        sla_threshold_ms=raw["sla_threshold_ms"],
    )


@pytest.fixture
def sources() -> tuple[SourceEventRef, ...]:
    return (
        SourceEventRef("evt-breach-1", "a" * 64),
        SourceEventRef("evt-context-1", "b" * 64),
    )


def derive(
    latency_fixture: DetectionLatencyFixture,
    sources: tuple[SourceEventRef, ...],
    *,
    routed: str = "2026-07-29T12:04:59Z",
    receipt: str | None = "2026-07-29T12:00:01Z",
):
    return derive_detection_latency(
        event_time="2026-07-29T12:00:00Z",
        collector_received_at=receipt,
        determined_at="2026-07-29T12:00:03Z",
        reviewer_routed_at=routed,
        source_events=sources,
        fixture=latency_fixture,
    )


def test_on_time_separates_all_stages_and_preserves_lineage(
    latency_fixture, sources
):
    record = derive(latency_fixture, sources)
    assert record.verdict == "on_time"
    assert record.reported_not_scored is True
    assert record.latency_ms == {
        "event_to_collector_ms": 1000,
        "collector_to_determination_ms": 2000,
        "determination_to_routing_ms": 296000,
        "event_to_routing_ms": 299000,
    }
    assert [(r.evidence_event_id, r.content_sha256) for r in record.source_events] == [
        ("evt-breach-1", "a" * 64),
        ("evt-context-1", "b" * 64),
    ]


def test_late_uses_fixture_sla_not_continuity_freshness(latency_fixture, sources):
    record = derive(latency_fixture, sources, routed="2026-07-29T12:05:00.001Z")
    assert record.verdict == "late"
    assert record.latency_ms["event_to_routing_ms"] == 300001


def test_missing_timestamp_is_indeterminate_with_lineage(latency_fixture, sources):
    record = derive(latency_fixture, sources, receipt=None)
    assert record.verdict == "indeterminate"
    assert record.reason == "missing_timestamp:collector_received_at"
    assert len(record.source_events) == 2
    assert all(value is None for value in record.latency_ms.values())


def test_clock_skew_is_explicitly_invalid(latency_fixture, sources):
    record = derive(
        latency_fixture,
        sources,
        receipt="2026-07-29T11:59:59Z",
    )
    assert record.verdict == "invalid_clock_skew"
    assert "event_to_collector_ms" in record.reason


def test_replay_is_byte_deterministic_and_duplicate_refs_are_deduplicated(
    latency_fixture, sources
):
    first = derive(latency_fixture, sources)
    replay = derive(latency_fixture, tuple(reversed(sources)) + (sources[0],))
    assert replay == first
    assert replay.to_dict() == first.to_dict()


def test_replay_with_conflicting_hash_is_rejected(latency_fixture, sources):
    conflicting = sources + (SourceEventRef("evt-breach-1", "c" * 64),)
    with pytest.raises(DetectionLatencyError, match="conflicting content hash"):
        derive(latency_fixture, conflicting)
