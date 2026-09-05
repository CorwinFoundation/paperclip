from datetime import datetime, timezone
import sqlite3

from cryptography.hazmat.primitives.asymmetric import ed25519

from post_bind_ingest.causal_lineage import apply_causal_migrations, append_causal_events, reconstruct_trace
from post_bind_ingest.normalizer import normalize_receiver_events
from post_bind_ingest.otel_driver import OTelSequenceGuard, build_signed_receiver_batch, map_otlp_json


NOW = datetime(2026, 9, 5, 20, 0, 2, tzinfo=timezone.utc)


def _attr(key, value):
    typed = "intValue" if isinstance(value, int) else "stringValue"
    return {"key": key, "value": {typed: value}}


def test_causal_identity_timing_and_fallback_survive_mapping():
    request = {"resourceSpans": [{
        "resource": {"attributes": [_attr("service.name", "agent-runtime")]},
        "scopeSpans": [{"spans": [{
            "traceId": "a" * 32,
            "spanId": "b" * 16,
            "parentSpanId": "c" * 16,
            "name": "policy_check",
            "kind": 1,
            "startTimeUnixNano": "1788638400000000000",
            "endTimeUnixNano": "1788638401000000000",
            "status": {"code": 2, "message": "policy denied"},
            "attributes": [
                _attr("backbond.task.id", "task-1"),
                _attr("backbond.run.id", "run-1"),
                _attr("backbond.agent.id", "agent-1"),
                _attr("backbond.model.id", "model-1"),
                _attr("backbond.action.kind", "policy_check"),
                _attr("backbond.action.digest", "sha256:" + "d" * 64),
                _attr("backbond.verification.status", "failed"),
            ],
        }]}],
    }]}

    event, = map_otlp_json(
        request,
        source_id="source-1",
        sequence=1,
        batch_id="batch-1",
        guard=OTelSequenceGuard(),
        now=NOW,
    )

    assert event["event_type"] == "otel_span"
    assert event["payload"]["causal"] == {
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "parent_span_id": "c" * 16,
        "operation": {"name": "policy_check", "kind": 1},
        "timing": {
            "started_at": "2026-09-05T20:00:00Z",
            "ended_at": "2026-09-05T20:00:01Z",
            "duration_ns": 1_000_000_000,
        },
        "status": {"code": 2, "message_digest": "sha256:45756dc26a0dc6443b79ca8dd7fccca19747cc81fc0752bb9b312b6851a2d02d"},
        "correlation": {"task.id": "task-1", "run.id": "run-1"},
        "identities": {"agent.id": "agent-1", "model.id": "model-1"},
        "facts": {
            "action.kind": "policy_check",
            "action.digest": "sha256:" + "d" * 64,
            "verification.status": "failed",
        },
    }
    assert "policy denied" not in str(event)


def test_signed_redacted_batch_persists_and_reconstructs_exact_causal_graph(monkeypatch):
    names = ["request", "agent", "retrieval", "chat", "tool_proposal", "policy_check", "human_approval", "execute_tool", "state_change", "verification"]
    trace_id = "1" * 32
    spans = []
    for index, name in enumerate(names):
        attrs = [
            _attr("backbond.task.id", "task-30000"),
            _attr("backbond.run.id", "run-30000"),
            _attr("backbond.agent.id", "agent-a" if index < 2 else "agent-b"),
            _attr("backbond.action.kind", name),
            _attr("backbond.action.digest", "sha256:" + f"{index:x}" * 64),
        ]
        if name == "chat":
            attrs += [_attr("gen_ai.usage.input_tokens", 11), _attr("gen_ai.usage.output_tokens", 7)]
        if name == "human_approval":
            attrs += [_attr("backbond.handoff.from_agent_id", "agent-a")]
        if name == "verification":
            attrs += [_attr("backbond.verification.status", "failed")]
        start = 1788638400000000000 + index * 100000000
        spans.append({
            "traceId": trace_id,
            "spanId": f"{index + 1:016x}",
            **({"parentSpanId": f"{index:016x}"} if index else {}),
            "name": name,
            "startTimeUnixNano": str(start),
            "endTimeUnixNano": str(start + 50000000),
            "status": {"code": 2 if name == "verification" else 1},
            "attributes": attrs,
        })
    request = {"resourceSpans": [{"resource": {"attributes": [_attr("service.name", "agent-runtime")]}, "scopeSpans": [{"spans": spans}]}]}
    mapped = map_otlp_json(request, source_id="source-1", sequence=1, batch_id="batch-1", guard=OTelSequenceGuard(), now=NOW)
    private_key = ed25519.Ed25519PrivateKey.generate()
    batch = build_signed_receiver_batch(mapped, customer_id="tenant-a", agent_id="agent-a", batch_id="batch-1", issued_at=NOW.isoformat().replace("+00:00", "Z"), key_id="key-1", private_key=private_key)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr("post_bind_ingest.receiver.validator.datetime", FrozenDateTime)
    normalized = normalize_receiver_events(batch, jwt_keyring={"key-1": private_key.public_key()})
    db = sqlite3.connect(":memory:")
    apply_causal_migrations(db)
    append_causal_events(db, tenant_id="tenant-a", agent_id="agent-a", events=normalized)
    trace = reconstruct_trace(db, tenant_id="tenant-a", agent_id="agent-a", trace_id=trace_id)

    assert [event["payload"]["causal"]["operation"]["name"] for event in trace.events] == names
    assert [event["payload"]["causal"]["parent_span_id"] for event in trace.events] == [None] + [f"{i:016x}" for i in range(1, len(names))]
    assert trace.loop_depth == 9
    assert trace.retries == 0
    assert trace.handoffs == 1
    assert (trace.input_tokens, trace.output_tokens) == (11, 7)
    assert trace.duration_ns == 500_000_000
    assert trace.terminal_outcome == "error"
    assert trace.verification_failed is True
