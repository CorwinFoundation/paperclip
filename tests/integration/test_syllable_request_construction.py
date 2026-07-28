"""BEAAA-7791 — Syllable apichat request construction.

The production customer walker must not send the generic single-POST probe
shape to ``https://apichat.syllable.cloud/``. Syllable's app contract is a
two-step API under ``/api/v1/chats/*`` with Syllable public headers; a root
POST returns an app-layer 404 and must route back to request construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json

import pytest

from harness.customer_runner import customer_agent_http_runner_factory
from harness.runner import RunnerError


@dataclass
class _Response:
    status_code: int
    payload: dict[str, Any]
    text: str = ""
    headers: dict[str, str] | None = None
    reason_phrase: str = "OK"

    def __post_init__(self) -> None:
        if self.headers is None:
            self.headers = {}

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def iter_raw(self) -> list[bytes]:
        return [json.dumps(self.payload).encode("utf-8")]


def _install_fake_scope(monkeypatch, responder):
    calls: list[dict[str, Any]] = []

    class _Client:
        def __init__(
            self,
            timeout: float,
            follow_redirects: bool,
            session_id: str,
            live_bind_scope_id: str | None,
        ):
            self._timeout = timeout
            self._follow_redirects = follow_redirects
            self._session_id = session_id
            self._live_bind_scope_id = live_bind_scope_id

        def __enter__(self) -> "_Client":
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def stream(
            self,
            method: str,
            url: str,
            *,
            content: bytes,
            headers: dict[str, str],
        ) -> _Response:
            call = {
                "method": method,
                "url": url,
                "json": json.loads(content.decode("utf-8")),
                "headers": headers,
                "timeout": self._timeout,
                "follow_redirects": self._follow_redirects,
                "session_id": self._session_id,
                "live_bind_scope_id": self._live_bind_scope_id,
            }
            calls.append(call)
            return responder(url, call, calls)

    class _Scope:
        def __init__(
            self,
            allowed_host: str,
            *,
            live_bind_scope_id: str | None = None,
        ):
            self.allowed_host = allowed_host
            self.live_bind_scope_id = live_bind_scope_id

        def __enter__(self) -> "_Scope":
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def client(
            self,
            *,
            timeout: float,
            follow_redirects: bool,
            session_id: str,
        ) -> _Client:
            return _Client(
                timeout,
                follow_redirects,
                session_id,
                self.live_bind_scope_id,
            )

    monkeypatch.setattr("harness.customer_runner.ScopedEgressAllowlist", _Scope)
    return calls


def test_apichat_factory_uses_syllable_two_step_route(monkeypatch) -> None:
    monkeypatch.setenv("SYLLABLE_CLIENT_ID_KEY", "client-key-fixture")
    monkeypatch.setenv("SYLLABLE_HOST_KEY", "https://syllable.example")

    def responder(url: str, _call: dict[str, Any], _calls: list[dict[str, Any]]) -> _Response:
        if url.endswith("/api/v1/chats/start_session"):
            return _Response(200, {"id": "session-7791"})
        if url.endswith("/api/v1/chats/send_message"):
            return _Response(
                200,
                {"message": {"message": "constructed via syllable route"}},
            )
        return _Response(404, {}, "not found")

    calls = _install_fake_scope(monkeypatch, responder)

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
        assert call["method"] == "POST"
        assert call["follow_redirects"] is False
        headers = call["headers"]
        assert headers["Syllable-Client-Id-Key"] == "client-key-fixture"
        assert headers["Syllable-Host-Key"] == "https://syllable.example"
        assert headers["Origin"] == "https://webchat.syllable.cloud"
        assert headers["Accept-Encoding"] == "identity"
        assert "Authorization" not in headers
    assert calls[0]["json"] == {}
    assert calls[1]["json"] == {
        "session_id": "session-7791",
        "message": "probe prompt",
    }
    assert {call["session_id"] for call in calls} and len(
        {call["session_id"] for call in calls}
    ) == 1
    assert {call["live_bind_scope_id"] for call in calls} == {
        calls[0]["session_id"]
    }


def test_apichat_retries_transient_proxy_status(monkeypatch) -> None:
    monkeypatch.setenv("SYLLABLE_CLIENT_ID_KEY", "client-key-fixture")
    monkeypatch.setenv("SYLLABLE_HOST_KEY", "https://syllable.example")
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    def responder(url: str, _call: dict[str, Any], calls: list[dict[str, Any]]) -> _Response:
        if url.endswith("/api/v1/chats/start_session"):
            start_attempts = [
                c for c in calls if c["url"].endswith("/api/v1/chats/start_session")
            ]
            if len(start_attempts) < 3:
                return _Response(522, {}, "proxy tunnel failed", reason_phrase="Proxy Error")
            return _Response(200, {"id": "session-7846"})
        if url.endswith("/api/v1/chats/send_message"):
            return _Response(
                200,
                {"message": {"message": "retried through proxy"}},
            )
        return _Response(404, {}, "not found")

    calls = _install_fake_scope(monkeypatch, responder)

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


def test_apichat_uses_180_second_attempt_timeout_and_jittered_backoff(
    monkeypatch,
) -> None:
    sleeps: list[float] = []
    attempts: list[dict[str, Any]] = []
    monkeypatch.setattr("time.sleep", sleeps.append)

    def responder(_url, _call, calls):
        if len(calls) < 3:
            return _Response(503, {}, reason_phrase="Unavailable")
        return _Response(200, {"id": "session"})

    calls = _install_fake_scope(monkeypatch, responder)
    from harness.customer_runner import _SyllableCustomerAgentRunner

    runner = _SyllableCustomerAgentRunner(
        base_url="https://apichat.syllable.cloud",
        client_id_key="client",
        host_key="host",
        jitter_fn=lambda _low, _high: 1.25,
        http_attempt_observer=attempts.append,
    )
    response = runner._syllable_post(
        "/api/v1/chats/start_session",
        phase="start_session",
        headers=runner._headers(),
        payload={},
        egress_session_id="scope",
    )

    assert response.json() == {"id": "session"}
    assert [call["timeout"] for call in calls] == [180.0, 180.0, 180.0]
    assert sleeps == [1.25, 2.5]
    assert [item["attempt"] for item in attempts] == [1, 2, 3]


def test_apichat_retries_transient_proxy_exception(monkeypatch) -> None:
    monkeypatch.setenv("SYLLABLE_CLIENT_ID_KEY", "client-key-fixture")
    monkeypatch.setenv("SYLLABLE_HOST_KEY", "https://syllable.example")
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    def responder(url: str, _call: dict[str, Any], calls: list[dict[str, Any]]) -> _Response:
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

    calls = _install_fake_scope(monkeypatch, responder)

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

    def responder(
        _url: str,
        call: dict[str, Any],
        _calls: list[dict[str, Any]],
    ) -> _Response:
        now["t"] += float(call["timeout"])
        raise RuntimeError("ReadTimeout: request timed out")

    monkeypatch.setattr("time.monotonic", fake_monotonic)
    monkeypatch.setattr("time.sleep", fake_sleep)
    calls = _install_fake_scope(monkeypatch, responder)

    runner = customer_agent_http_runner_factory(
        "https://apichat.syllable.cloud",
        "scanner-vault-secret-must-not-be-used-as-syllable-client-id",
    )
    runner.set_scan_deadline(101.0)  # type: ignore[attr-defined]

    with pytest.raises(RunnerError, match="syllable scan deadline exceeded"):
        runner.complete("probe prompt")

    assert [call["timeout"] for call in calls] == [1.0]
    assert sleeps == []
