from datetime import datetime, timezone

import pytest

from post_bind_ingest.otel_driver import OTelDriverError, OTelSequenceGuard, map_otlp_json


NOW = datetime(2026, 8, 2, 22, 50, tzinfo=timezone.utc)


def attr(key, value):
    typed = "intValue" if isinstance(value, int) else "stringValue"
    return {"key": key, "value": {typed: value}}


def payload(*span_attrs, end="1785711000000000000"):
    return {"resourceSpans": [{"resource": {"attributes": [attr("service.name", "vendor-agent")]}, "scopeSpans": [{"spans": [{"traceId": "a" * 32, "spanId": "b" * 16, "name": "chat", "startTimeUnixNano": "1785710999000000000", "endTimeUnixNano": end, "attributes": list(span_attrs)}]}]}]}


def mapped(request=None, guard=None, sequence=1, batch_id="batch-1"):
    return map_otlp_json(request or payload(attr("gen_ai.operation.name", "chat")), source_id="source-a", sequence=sequence, batch_id=batch_id, guard=guard or OTelSequenceGuard(), now=NOW)


def test_valid_allowlist_mapping_records_source_and_tier():
    event = mapped()[0]
    assert event["payload"] == {"resource": {"service.name": "vendor-agent"}, "span": {"gen_ai.operation.name": "chat"}}
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
