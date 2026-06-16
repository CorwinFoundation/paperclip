"""N=5 byte-identical runs of the normalizer on the golden-event fixture.

Schema §Reproducibility + CTO brief §5 AC#4. The normalizer takes no clocks,
no RNG, no live LLM — output bytes must be identical across N runs given the
same input and the same NormalizationContext.
"""

from __future__ import annotations

import json
from pathlib import Path

from post_bind_ingest.normalizer import NormalizationContext, normalize_stream
from post_bind_ingest.normalizer.adapters.raw_stdout import parse_lines


FIXTURE = Path(__file__).parent / "fixtures" / "golden_events" / "raw_records.jsonl"
CTX = NormalizationContext(
    manifest_sha="sha256:" + "f" * 64,
    customer_id="11111111-1111-4111-8111-111111111111",
    agent_id="22222222-2222-4222-8222-222222222222",
    source_kind="stdout",
    t_received="2026-06-17T00:00:10Z",
)


def _run_once() -> list[dict]:
    with FIXTURE.open() as fh:
        raw = list(parse_lines(fh))
    return list(normalize_stream(raw, CTX))


def test_n5_byte_identical() -> None:
    runs = [_run_once() for _ in range(5)]
    serialized = [
        json.dumps(run, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for run in runs
    ]
    first = serialized[0]
    for index, body in enumerate(serialized[1:], start=1):
        assert body == first, f"run {index} diverged from run 0"


def test_golden_fixture_yields_expected_families() -> None:
    """Sanity: the fixture exercises 5 event families end-to-end."""
    events = _run_once()
    types = [e["event_type"] for e in events]
    assert "agent_invocation" in types
    assert "llm_completion" in types
    assert "tool_call" in types
    assert "error" in types
    assert "escalation" in types


def test_every_event_carries_common_envelope() -> None:
    events = _run_once()
    common = {"schema_version", "event_type", "event_id", "customer_id",
              "agent_id", "session_id", "t_event", "t_received",
              "source_kind", "manifest_sha", "redaction_applied", "partial"}
    for event in events:
        missing = common - set(event.keys())
        assert not missing, f"event missing common envelope keys: {missing}"
        assert event["schema_version"] == "log-event-schema-v1.0"
