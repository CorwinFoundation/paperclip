"""Schema §Cross-event invariants 1–5 enforcement at the normalizer.

Invariants:
  1. Session continuity — non-`agent_invocation` event before invocation -> diagnostic.
  2. Turn monotonicity — turn_index must be non-decreasing within a session.
  3. Reference resolution — tool_source_reference.paired_tool_call_event_id must resolve.
  4. Claim evidence integrity — checked at sink (BEAAA-5867), not at normalizer (claim_candidate_event is detector-emitted).
  5. Schema-version stability — gateway rejects mixed-version manifests; normalizer-pass defense-in-depth.
"""

from __future__ import annotations

from post_bind_ingest.normalizer.invariants import enforce


def _envelope(event_type: str, **kw) -> dict:
    base = {
        "schema_version": "log-event-schema-v1.0",
        "event_type": event_type,
        "event_id": kw.pop("event_id", "00000000-0000-4000-8000-000000000000"),
        "session_id": kw.pop("session_id", "11111111-1111-4111-8111-111111111111"),
        "customer_id": "cust-1",
        "agent_id": "agent-1",
        "t_event": "2026-06-17T00:00:00Z",
        "t_received": "2026-06-17T00:00:01Z",
        "source_kind": "stdout",
        "manifest_sha": "sha256:" + "a" * 64,
        "redaction_applied": [],
        "partial": False,
    }
    base.update(kw)
    return base


def test_invariant_1_event_before_invocation_diagnostic() -> None:
    events = [_envelope("llm_completion", turn_index=0)]
    out = list(enforce(events))
    assert len(out) == 1
    _, reports = out[0]
    assert any(r.invariant == 1 for r in reports)


def test_invariant_1_clean_after_invocation() -> None:
    events = [
        _envelope("agent_invocation", event_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"),
        _envelope("llm_completion", turn_index=0, event_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"),
    ]
    reports_all = [reports for _, reports in enforce(events)]
    assert all(not r for r in reports_all)


def test_invariant_2_turn_monotonicity_violation() -> None:
    events = [
        _envelope("agent_invocation", event_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"),
        _envelope("llm_completion", turn_index=2, event_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"),
        _envelope("llm_completion", turn_index=1, event_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3"),
    ]
    reports = [r for _, rs in enforce(events) for r in rs]
    assert any(r.invariant == 2 for r in reports)


def test_invariant_3_unresolved_reference() -> None:
    events = [
        _envelope("agent_invocation", event_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"),
        _envelope(
            "tool_source_reference",
            turn_index=0,
            event_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa9",
            paired_tool_call_event_id="ffffffff-ffff-4fff-8fff-ffffffffffff",
        ),
    ]
    reports = [r for _, rs in enforce(events) for r in rs]
    assert any(r.invariant == 3 for r in reports)


def test_invariant_3_resolved_reference_clean() -> None:
    events = [
        _envelope("agent_invocation", event_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"),
        _envelope("tool_call", turn_index=0, event_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"),
        _envelope(
            "tool_source_reference",
            turn_index=0,
            event_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3",
            paired_tool_call_event_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
        ),
    ]
    reports = [r for _, rs in enforce(events) for r in rs]
    assert reports == []


def test_invariant_5_mixed_schema_version_diagnostic() -> None:
    events = [
        _envelope("agent_invocation", event_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"),
        _envelope("llm_completion", turn_index=0,
                  event_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
                  schema_version="log-event-schema-v0.9"),
    ]
    reports = [r for _, rs in enforce(events) for r in rs]
    assert any(r.invariant == 5 for r in reports)
