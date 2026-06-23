"""BEAAA-7791 — Syllable apichat request construction.

The production customer walker must not send the generic single-POST probe
shape to ``https://apichat.syllable.cloud/``. Syllable's app contract is a
two-step API under ``/api/v1/chats/*`` with Syllable public headers; a root
POST returns an app-layer 404 and must route back to request construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from harness.customer_runner import customer_agent_http_runner_factory
from harness.runner import RunnerError


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


def test_apichat_retries_transient_proxy_status(monkeypatch) -> None:
    monkeypatch.setenv("SYLLABLE_CLIENT_ID_KEY", "client-key-fixture")
    monkeypatch.setenv("SYLLABLE_HOST_KEY", "https://syllable.example")
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> _Response:
        calls.append({"url": url, **kwargs})
        if url.endswith("/api/v1/chats/start_session"):
            start_attempts = [
                c for c in calls if c["url"].endswith("/api/v1/chats/start_session")
            ]
            if len(start_attempts) < 3:
                return _Response(522, {}, "proxy tunnel failed")
            return _Response(200, {"id": "session-7846"})
        if url.endswith("/api/v1/chats/send_message"):
            return _Response(
                200,
                {"message": {"message": "retried through proxy"}},
            )
        return _Response(404, {}, "not found")

    monkeypatch.setattr("harness.egress.post", fake_post)

    runner = customer_agent_http_runner_factory(
        "https://apichat.syllable.cloud",
        "scanner-vault-secret-must-not-be-used-as-syllable-client-id",
    )

    assert runner.complete("probe prompt") == "retried through proxy"
    assert [call["url"] for call in calls].count(
        "https://apichat.syllable.cloud/api/v1/chats/start_session"
    ) == 3
    session_ids = {call["session_id"] for call in calls}
    assert len(session_ids) == 1


def test_apichat_retries_transient_proxy_exception(monkeypatch) -> None:
    monkeypatch.setenv("SYLLABLE_CLIENT_ID_KEY", "client-key-fixture")
    monkeypatch.setenv("SYLLABLE_HOST_KEY", "https://syllable.example")
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> _Response:
        calls.append({"url": url, **kwargs})
        if url.endswith("/api/v1/chats/start_session"):
            return _Response(200, {"id": "session-7846"})
        if url.endswith("/api/v1/chats/send_message"):
            send_attempts = [
                c for c in calls if c["url"].endswith("/api/v1/chats/send_message")
            ]
            if len(send_attempts) == 1:
                raise RuntimeError(
                    "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"
                )
            return _Response(
                200,
                {"message": {"message": "retried send_message"}},
            )
        return _Response(404, {}, "not found")

    monkeypatch.setattr("harness.egress.post", fake_post)

    runner = customer_agent_http_runner_factory(
        "https://apichat.syllable.cloud",
        "scanner-vault-secret-must-not-be-used-as-syllable-client-id",
    )

    assert runner.complete("probe prompt") == "retried send_message"
    assert [call["url"] for call in calls].count(
        "https://apichat.syllable.cloud/api/v1/chats/send_message"
    ) == 2


def test_apichat_retry_respects_scan_deadline(monkeypatch) -> None:
    monkeypatch.setenv("SYLLABLE_CLIENT_ID_KEY", "client-key-fixture")
    monkeypatch.setenv("SYLLABLE_HOST_KEY", "https://syllable.example")

    now = {"t": 100.0}
    sleeps: list[float] = []

    def fake_monotonic() -> float:
        return now["t"]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now["t"] += seconds

    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> _Response:
        calls.append({"url": url, **kwargs})
        now["t"] += float(kwargs["timeout"])
        raise RuntimeError("ReadTimeout: request timed out")

    monkeypatch.setattr("time.monotonic", fake_monotonic)
    monkeypatch.setattr("time.sleep", fake_sleep)
    monkeypatch.setattr("harness.egress.post", fake_post)

    runner = customer_agent_http_runner_factory(
        "https://apichat.syllable.cloud",
        "scanner-vault-secret-must-not-be-used-as-syllable-client-id",
    )
    runner.set_scan_deadline(101.0)  # type: ignore[attr-defined]

    with pytest.raises(RunnerError, match="syllable scan deadline exceeded"):
        runner.complete("probe prompt")

    assert [call["timeout"] for call in calls] == [1.0]
    assert sleeps == []
