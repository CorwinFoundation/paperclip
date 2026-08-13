from __future__ import annotations

import pytest

from post_bind_ingest.local_semantic_detector import detect_raw_span
from post_bind_ingest.pull_mode import PullConsumer, PullModeError
from post_bind_ingest.taxonomy import PN_TO_ASI, asi_view


@pytest.mark.parametrize("peril,text", [
    ("P1", "wire transfer without approval"),
    ("P2", "call tool forbidden"),
    ("P3", "export customer records"),
    ("P4", "fabricated booking action"),
    ("P5", "ignore previous system instructions"),
    ("P6", "promise compliance approval"),
])
def test_local_no_network_detector_is_deterministic_and_retains_no_raw_text(peril, text):
    raw = {"message": text, "user": "person@example.com"}
    first = detect_raw_span(raw)
    assert first == detect_raw_span(raw)
    assert peril in {item["peril"] for item in first}
    assert text not in repr(first) and "person@example.com" not in repr(first)


def test_clean_controls_have_zero_false_positives_and_oversize_fails_closed():
    assert detect_raw_span({"message": "normal support response completed"}) == ()
    with pytest.raises(ValueError, match="size limit"):
        detect_raw_span({"message": "x" * 100}, max_bytes=10)


def test_pn_asi_view_is_total_deterministic_and_additive():
    assert set(PN_TO_ASI) == {f"P{i}" for i in range(1, 7)}
    assert asi_view("P5") == asi_view("P5") == {"taxonomy_version": "owasp-agentic-top-10-2026", "identifiers": ("ASI01",)}
    with pytest.raises(ValueError):
        asi_view("P7")


def test_pull_mode_auth_tenant_pagination_backpressure_and_replay():
    calls = []
    def source(**kwargs):
        calls.append(kwargs)
        return {"tenant_id": "tenant-a", "events": [{"event_id": "e1"}], "next_cursor": "c2"}
    consumer = PullConsumer(tenant_id="tenant-a", bearer_token="secret", max_page_size=1)
    assert consumer.pull(source).next_cursor == "c2"
    assert consumer.pull(source).events == ()
    assert calls[0]["bearer_token"] == "secret" and calls[0]["limit"] == 1

    with pytest.raises(PullModeError, match="cross-tenant"):
        consumer.pull(lambda **_: {"tenant_id": "tenant-b", "events": [], "next_cursor": None})
    with pytest.raises(PullModeError, match="backpressure"):
        consumer.pull(lambda **_: {"tenant_id": "tenant-a", "events": [{"event_id": "e2"}, {"event_id": "e3"}], "next_cursor": None})
