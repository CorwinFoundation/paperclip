from datetime import datetime, timezone

import pytest

from post_bind_ingest.otel_driver import OTelDriverError, OTelSequenceGuard, map_otlp_json


NOW = datetime(2026, 8, 2, 22, 50, tzinfo=timezone.utc)


def attr(key, value):
    typed = "intValue" if isinstance(value, int) else "stringValue"
    return {"key": key, "value": {typed: value}}


def payload(*span_attrs, end="1785711000000000000", status=None):
    span = {"traceId": "a" * 32, "spanId": "b" * 16, "name": "chat", "startTimeUnixNano": "1785710999000000000", "endTimeUnixNano": end, "attributes": list(span_attrs)}
    if status is not None:
        span["status"] = status
    return {"resourceSpans": [{"resource": {"attributes": [attr("service.name", "vendor-agent")]}, "scopeSpans": [{"spans": [span]}]}]}


def mapped(request=None, guard=None, sequence=1, batch_id="batch-1"):
    return map_otlp_json(request or payload(attr("gen_ai.operation.name", "chat")), source_id="source-a", sequence=sequence, batch_id=batch_id, guard=guard or OTelSequenceGuard(), now=NOW)


def test_valid_allowlist_mapping_records_source_and_tier():
    event = mapped()[0]
    assert event["payload"]["resource"] == {"service.name": "vendor-agent"}
    assert event["payload"]["span"] == {"gen_ai.operation.name": "chat"}
    assert event["payload"]["causal"]["operation"]["name"] == "chat"
    assert event["provenance"]["source_driver"] == "otel-collector-direct-v1"
    assert event["provenance"]["evidence_tier"] == "vendor-local-redacted"
    assert event["redaction_applied"] == []


@pytest.mark.parametrize("bad", [attr("custom.unknown", "x"), attr("authorization", "Bearer x"), attr("gen_ai.request.model", "api_key=sk-abcdefghijklmnop")])
def test_disallowed_or_secret_bearing_attributes_fail_closed(bad):
    with pytest.raises(OTelDriverError):
        mapped(payload(bad))


def test_content_capture_defaults_off_and_is_never_signed_without_profile():
    with pytest.raises(OTelDriverError, match="content capture disabled"):
        mapped(payload(attr("gen_ai.prompt", "private prompt")))


def test_replay_and_reorder_fail_closed():
    guard = OTelSequenceGuard()
    mapped(guard=guard)
    with pytest.raises(OTelDriverError, match="replay"):
        mapped(guard=guard)
    with pytest.raises(OTelDriverError, match="expected 2"):
        mapped(guard=guard, sequence=3, batch_id="batch-3")


def test_invalid_schema_clock_skew_and_tamper_shape_fail_closed():
    with pytest.raises(OTelDriverError, match="only resourceSpans"):
        mapped({"resourceSpans": [], "signature": "attacker"})
    with pytest.raises(OTelDriverError, match="clock skew"):
        mapped(payload(attr("gen_ai.operation.name", "chat"), end="1"))
    bad = payload(attr("gen_ai.operation.name", "chat"))
    bad["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["events"] = []
    with pytest.raises(OTelDriverError, match="schema"):
        mapped(bad)


@pytest.mark.parametrize(
    ("status", "decision", "expected"),
    [
        ({"code": 1}, None, "completed"),
        ({"code": 2, "message": "upstream refused"}, None, "failed"),
        ({"code": "STATUS_CODE_ERROR"}, "deny", "denied"),
        ({"code": 1}, "ESCALATE", "escalated"),
    ],
)
def test_span_status_and_authorization_decision_derive_outcome(status, decision, expected):
    attrs = [attr("gen_ai.operation.name", "chat")]
    if decision is not None:
        attrs.append(attr("backbond.authz.decision", decision))
    event = mapped(payload(*attrs, status=status))[0]
    assert event["payload"]["outcome_status"] == expected
    if decision is not None:
        assert event["payload"]["span"]["backbond.authz.decision"] == decision.lower()


def test_status_message_is_digest_only_and_approval_path_is_bounded():
    event = mapped(payload(
        attr("gen_ai.operation.name", "chat"),
        attr("backbond.authz.approval_path", "policy:treasury/dual-control"),
        status={"code": 2, "message": "upstream refused"},
    ))[0]
    status = event["payload"]["causal"]["status"]
    assert status["message_digest"].startswith("sha256:")
    assert "upstream refused" not in str(event)


@pytest.mark.parametrize("bad", [
    attr("backbond.authz.decision", "denied because risky"),
    attr("backbond.authz.approval_path", "queue with spaces"),
    attr("backbond.authz.approval_path", "x" * 129),
])
def test_authorization_attributes_reject_free_text(bad):
    with pytest.raises(OTelDriverError):
        mapped(payload(attr("gen_ai.operation.name", "chat"), bad))


@pytest.mark.parametrize("bad_status", [{"code": 7}, {"code": True}, {"code": 1, "extra": "x"}, {"code": 2, "message": 5}])
def test_invalid_status_shapes_fail_closed(bad_status):
    with pytest.raises(OTelDriverError):
        mapped(payload(attr("gen_ai.operation.name", "chat"), status=bad_status))
