"""BEAAA-7791 — Syllable apichat request construction.

The production customer walker must not send the generic single-POST probe
shape to ``https://apichat.syllable.cloud/``. Syllable's app contract is a
two-step API under ``/api/v1/chats/*`` with Syllable public headers; a root
POST returns an app-layer 404 and must route back to request construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from harness.customer_runner import customer_agent_http_runner_factory


@dataclass
class _Response:
    status_code: int
    payload: dict[str, Any]
    text: str = ""

    def json(self) -> dict[str, Any]:
        return self.payload


def test_apichat_factory_uses_syllable_two_step_route(monkeypatch) -> None:
    monkeypatch.setenv("SYLLABLE_CLIENT_ID_KEY", "client-key-fixture")
    monkeypatch.setenv("SYLLABLE_HOST_KEY", "https://syllable.example")

    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> _Response:
        calls.append({"url": url, **kwargs})
        if url.endswith("/api/v1/chats/start_session"):
            return _Response(200, {"id": "session-7791"})
        if url.endswith("/api/v1/chats/send_message"):
            return _Response(
                200,
                {"message": {"message": "constructed via syllable route"}},
            )
        return _Response(404, {}, "not found")

    monkeypatch.setattr("harness.egress.post", fake_post)

    runner = customer_agent_http_runner_factory(
        "https://apichat.syllable.cloud",
        "scanner-vault-secret-must-not-be-used-as-syllable-client-id",
    )
    response = runner.complete("probe prompt")

    assert response == "constructed via syllable route"
    assert [call["url"] for call in calls] == [
        "https://apichat.syllable.cloud/api/v1/chats/start_session",
        "https://apichat.syllable.cloud/api/v1/chats/send_message",
    ]
    for call in calls:
        headers = call["headers"]
        assert headers["Syllable-Client-Id-Key"] == "client-key-fixture"
        assert headers["Syllable-Host-Key"] == "https://syllable.example"
        assert "Authorization" not in headers
    assert calls[0]["json"] == {}
    assert calls[1]["json"] == {
        "session_id": "session-7791",
        "message": "probe prompt",
    }
