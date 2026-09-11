"""Native Vapi Chat transport for production customer-agent scans."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
import json

import pytest

from harness.customer_runner import customer_agent_http_runner_factory
from harness.runner import RunnerError


ASSISTANT_ID = "09aea361-04b3-4a62-bcb0-2f2582f11ec8"


@dataclass
class _Response:
    status_code: int
    payload: Any
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
        if isinstance(self.payload, bytes):
            return [self.payload]
        return [json.dumps(self.payload).encode("utf-8")]


def _install_fake_scope(
    monkeypatch: pytest.MonkeyPatch,
    responder: Callable[[dict[str, Any]], _Response],
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    class _Client:
        def __init__(self, *, timeout: float, follow_redirects: bool) -> None:
            self.timeout = timeout
            self.follow_redirects = follow_redirects

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
                "timeout": self.timeout,
                "follow_redirects": self.follow_redirects,
            }
            calls.append(call)
            return responder(call)

    class _Scope:
        def __init__(self, allowed_url: str, **_kwargs: Any) -> None:
            self.allowed_url = allowed_url

        def __enter__(self) -> "_Scope":
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def client(self, *, timeout: float, follow_redirects: bool) -> _Client:
            return _Client(timeout=timeout, follow_redirects=follow_redirects)

    monkeypatch.setattr("harness.customer_runner.ScopedEgressAllowlist", _Scope)
    return calls


def _success(content: str = "safe response") -> _Response:
    return _Response(
        200,
        {
            "id": "chat-fixture-1",
            "assistantId": ASSISTANT_ID,
            "output": [{"role": "assistant", "content": content}],
        },
    )


def test_vapi_factory_builds_exact_fresh_chat_request(monkeypatch) -> None:
    calls = _install_fake_scope(monkeypatch, lambda _call: _success())
    runner = customer_agent_http_runner_factory(
        "https://api.vapi.ai",
        "private-key-fixture",
        ASSISTANT_ID,
    )

    assert runner.complete("first probe") == "safe response"
    assert runner.complete("second probe") == "safe response"
    assert runner.model_id() == f"vapi:{ASSISTANT_ID}@api.vapi.ai"
    assert len(calls) == 2
    assert [call["url"] for call in calls] == [
        "https://api.vapi.ai/chat",
        "https://api.vapi.ai/chat",
    ]
    assert [call["json"] for call in calls] == [
        {"assistantId": ASSISTANT_ID, "input": "first probe", "stream": False},
        {"assistantId": ASSISTANT_ID, "input": "second probe", "stream": False},
    ]
    assert all("previousChatId" not in call["json"] for call in calls)
    assert calls[0]["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer private-key-fixture",
        "User-Agent": "BackBond-Scanner/0.5",
        "Accept-Encoding": "identity",
    }
    assert calls[0]["follow_redirects"] is False


@pytest.mark.parametrize("agent_id", [None, "", "not-a-uuid"])
def test_vapi_factory_requires_uuid_agent_id(agent_id: str | None) -> None:
    with pytest.raises(RunnerError, match="assistant UUID"):
        customer_agent_http_runner_factory(
            "https://api.vapi.ai", "private-key-fixture", agent_id
        )


def test_vapi_response_requires_exact_assistant_identity(monkeypatch) -> None:
    wrong = {
        "assistantId": "6183d152-2843-445f-b274-5e3aba2c1ca1",
        "output": [{"role": "assistant", "content": "wrong target"}],
    }
    _install_fake_scope(monkeypatch, lambda _call: _Response(200, wrong))
    runner = customer_agent_http_runner_factory(
        "https://api.vapi.ai", "private-key-fixture", ASSISTANT_ID
    )

    with pytest.raises(RunnerError, match="assistantId mismatch"):
        runner.complete("probe")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"not-json", "invalid JSON"),
        ({"assistantId": ASSISTANT_ID}, "omitted output"),
        ({"assistantId": ASSISTANT_ID, "output": []}, "no assistant output"),
    ],
)
def test_vapi_response_shape_fails_closed(
    monkeypatch, payload: Any, message: str
) -> None:
    _install_fake_scope(monkeypatch, lambda _call: _Response(200, payload))
    runner = customer_agent_http_runner_factory(
        "https://api.vapi.ai", "private-key-fixture", ASSISTANT_ID
    )

    with pytest.raises(RunnerError, match=message):
        runner.complete("probe")


def test_non_vapi_factory_keeps_generic_request_shape(monkeypatch) -> None:
    calls = _install_fake_scope(
        monkeypatch,
        lambda _call: _Response(200, {"response": "generic response"}),
    )
    runner = customer_agent_http_runner_factory(
        "https://agent.example.com/respond",
        "customer-token",
        ASSISTANT_ID,
    )

    assert runner.complete("probe") == "generic response"
    assert calls[0]["url"] == "https://agent.example.com/respond"
    assert calls[0]["json"] == {"prompt": "probe"}
