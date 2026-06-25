from __future__ import annotations

import json

import pytest

from are_connector.crypto import b64url_encode
from are_connector.http import EgressAllowlist, call_agent
from are_connector.redaction import RedactionPolicy, redacted_excerpts
from are_connector.response_limits import MAX_RESPONSE_BYTES_ENV, OversizedResponseError
from are_connector.runtime import ConnectorRuntime, extract_response_text


class FakeResponse:
    def __init__(self, body: bytes, encoding: str = "utf-8"):
        self.body = body
        self.encoding = encoding
        self._content = None
        self._content_consumed = False

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 65536):
        for idx in range(0, len(self.body), chunk_size):
            yield self.body[idx:idx + chunk_size]

    def json(self):
        raw = self._content if self._content is not None else self.body
        return json.loads(raw)

    @property
    def text(self) -> str:
        raw = self._content if self._content is not None else self.body
        return raw.decode(self.encoding, errors="replace")


def _patch_post(monkeypatch, body: bytes) -> None:
    def fake_post(endpoint, json=None, timeout=None, stream=False, **kwargs):
        assert stream is True
        assert kwargs.get("allow_redirects") is False
        return FakeResponse(body)

    monkeypatch.setattr("are_connector.http.requests.post", fake_post)


def test_under_cap_pass_through(monkeypatch) -> None:
    monkeypatch.setenv(MAX_RESPONSE_BYTES_ENV, "64")
    body = b'{"response":"ok"}'
    _patch_post(monkeypatch, body)

    result = call_agent(
        "https://agent.example/staging",
        {"input": "x"},
        EgressAllowlist("https://portal.example", "https://agent.example/staging"),
        timeout=5,
    )

    assert result == {"response": "ok"}


def test_at_cap_edge_passes(monkeypatch) -> None:
    body = b"x" * 32
    monkeypatch.setenv(MAX_RESPONSE_BYTES_ENV, str(len(body)))
    _patch_post(monkeypatch, body)

    result = call_agent(
        "https://agent.example/staging",
        {"input": "x"},
        EgressAllowlist("https://portal.example", "https://agent.example/staging"),
        timeout=5,
    )

    assert result == {"response": "x" * 32}


def test_over_cap_rejected_with_oversized_failure_envelope(monkeypatch) -> None:
    monkeypatch.setenv(MAX_RESPONSE_BYTES_ENV, "16")
    task = {
        "task_id": "task-oversized",
        "task_type": "OPAQUE",
        "expected_assertions": ["schema_match"],
        "encrypted_task_content": b64url_encode(
            json.dumps({"task_id": "task-oversized", "input": "hello"}).encode("utf-8")
        ),
        "timeout_seconds": 5,
    }

    def oversized_call_agent(*args, **kwargs):
        raise OversizedResponseError(observed_byte_count=(2 * 1024 * 1024) + 1, max_response_bytes=16)

    monkeypatch.setattr("are_connector.runtime.call_agent", oversized_call_agent)
    runtime = ConnectorRuntime(
        portal_base_url="https://portal.example",
        agent_endpoint="https://agent.example/staging",
        config_path=__import__("pathlib").Path("/tmp/unused-config.json"),
    )

    envelope = runtime._execute_task(
        "scan-1",
        task,
        RedactionPolicy.trusted_for_tests(),
        EgressAllowlist("https://portal.example", "https://agent.example/staging"),
    )

    assert envelope["error"]["class"] == "oversized_response"
    assert "observed_byte_count=2097153" in envelope["error"]["detail"]
    assert "hello" not in str(envelope)
    assert envelope["redacted_excerpts"] == []


def test_configurable_cap_honored_with_multi_mib_body(monkeypatch) -> None:
    monkeypatch.setenv(MAX_RESPONSE_BYTES_ENV, str(1024 * 1024))
    _patch_post(monkeypatch, b"a" * ((2 * 1024 * 1024) + 7))

    with pytest.raises(OversizedResponseError) as exc_info:
        call_agent(
            "https://agent.example/staging",
            {"input": "x"},
            EgressAllowlist("https://portal.example", "https://agent.example/staging"),
            timeout=5,
        )

    assert exc_info.value.observed_byte_count > 1024 * 1024
    assert exc_info.value.max_response_bytes == 1024 * 1024


def test_runtime_and_redaction_defense_in_depth(monkeypatch) -> None:
    monkeypatch.setenv(MAX_RESPONSE_BYTES_ENV, "8")

    with pytest.raises(OversizedResponseError):
        extract_response_text({"response": "123456789"})

    with pytest.raises(OversizedResponseError):
        redacted_excerpts("123456789", RedactionPolicy.trusted_for_tests())
