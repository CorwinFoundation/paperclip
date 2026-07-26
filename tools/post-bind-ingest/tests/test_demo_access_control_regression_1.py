from __future__ import annotations

from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from urllib.parse import urlencode

import pytest


DEMO_DIR = Path(__file__).resolve().parents[1] / "demo"
sys.path.insert(0, str(DEMO_DIR))

from app import DemoHandler, RunQuota, _access_token_from_env, _bounded_content_length, _run_quota_from_env, _scenario_from_body  # noqa: E402


# Regression: ISSUE-002 — public demo exposed costly run execution without access control
# Found by /qa on 2026-07-12
# Report: .gstack/qa-reports/qa-report-hughes-combine-indiana-advisory-trycloudflare-com-2026-07-11.md

def test_public_mode_requires_a_strong_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_ENVIRONMENT", "public-demo")
    monkeypatch.delenv("DEMO_ACCESS_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="requires DEMO_ACCESS_TOKEN"):
        _access_token_from_env()

    monkeypatch.setenv("DEMO_ACCESS_TOKEN", "short")
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        _access_token_from_env()

    token = "partner-demo-token-with-at-least-32-characters"
    monkeypatch.setenv("DEMO_ACCESS_TOKEN", token)
    assert _access_token_from_env() == token


def test_request_body_length_is_bounded_before_reading() -> None:
    assert _bounded_content_length(None) == 0
    assert _bounded_content_length("1024") == 1024
    with pytest.raises(ValueError, match="invalid content length"):
        _bounded_content_length("not-a-number")
    with pytest.raises(ValueError, match="invalid content length"):
        _bounded_content_length("-1")
    with pytest.raises(OverflowError, match="request too large"):
        _bounded_content_length("1025")


def test_run_scenario_payload_is_strictly_validated() -> None:
    assert _scenario_from_body(b"") == "happy"
    assert _scenario_from_body(b"{}") == "happy"
    assert _scenario_from_body(b'{"scenario":"bind_reject"}') == "bind_reject"
    assert _scenario_from_body(b'{"scenario":"cicd_regression"}') == "cicd_regression"
    with pytest.raises(ValueError, match="valid UTF-8 JSON"):
        _scenario_from_body(b"not-json")
    with pytest.raises(ValueError, match="JSON object"):
        _scenario_from_body(b"[]")
    with pytest.raises(ValueError, match="unsupported scenario"):
        _scenario_from_body(b'{"scenario":"arbitrary-command"}')
    with pytest.raises(ValueError, match="unsupported scenario"):
        _scenario_from_body(b'{"scenario":[]}')
    with pytest.raises(ValueError, match="unsupported request fields"):
        _scenario_from_body(b'{"scenario":"happy","command":"rm"}')

def test_run_quota_is_enforced_per_client_and_resets() -> None:
    quota = RunQuota(max_requests=2, window_seconds=60)
    assert quota.allow("client-a", now=0) == (True, 0)
    assert quota.allow("client-a", now=1) == (True, 0)
    allowed, retry_after = quota.allow("client-a", now=2)
    assert not allowed
    assert retry_after == 58
    assert quota.allow("client-b", now=2) == (True, 0)
    assert quota.allow("client-a", now=61) == (True, 0)


def test_run_quota_is_enabled_only_for_public_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_RUN_QUOTA", "2")
    monkeypatch.setenv("DEMO_RUN_WINDOW_SECONDS", "60")

    assert _run_quota_from_env("local") is None
    public_quota = _run_quota_from_env("public-demo")
    assert public_quota is not None
    assert public_quota.allow("client", now=0) == (True, 0)
    assert public_quota.allow("client", now=1) == (True, 0)
    assert public_quota.allow("client", now=2)[0] is False

class _StubManager:
    def __init__(self) -> None:
        self.start_called = False

    def start(self):
        self.start_called = True
        raise AssertionError("anonymous request must not start a run")

    def latest(self):
        return None

    def get(self, _run_id: str):
        return None


def _request(port: int, method: str, path: str, *, body: str = "", headers: dict[str, str] | None = None):
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path, body=body.encode("utf-8"), headers=headers or {})
    response = connection.getresponse()
    payload = response.read()
    result = response.status, dict(response.getheaders()), payload
    connection.close()
    return result


def test_local_demo_allows_unlimited_rehearsal_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    class _LocalManager:
        def __init__(self) -> None:
            self.started = 0

        def start(self, scenario: str = "happy"):
            self.started += 1
            return type(
                "LocalJob",
                (),
                {"run_id": f"local-{self.started}", "status": "queued", "scenario": scenario},
            )()

    manager = _LocalManager()
    monkeypatch.setattr(DemoHandler, "manager", manager, raising=False)
    monkeypatch.setattr(DemoHandler, "access_token", None)
    monkeypatch.setattr(DemoHandler, "secure_cookie", False)
    monkeypatch.setattr(DemoHandler, "run_quota", None)

    server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        for _ in range(7):
            status, _headers, payload = _request(
                port,
                "POST",
                "/api/runs",
                body='{"scenario":"bind_reject"}',
                headers={"Content-Type": "application/json"},
            )
            assert status == 202, payload
        assert manager.started == 7
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

def test_http_boundary_requires_login_and_uses_an_httponly_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _StubManager()
    token = "partner-demo-token-with-at-least-32-characters"
    monkeypatch.setattr(DemoHandler, "manager", manager, raising=False)
    monkeypatch.setattr(DemoHandler, "access_token", token)
    monkeypatch.setattr(DemoHandler, "secure_cookie", True)
    monkeypatch.setattr(DemoHandler, "run_quota", RunQuota(max_requests=2, window_seconds=60))

    server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        status, _headers, payload = _request(port, "GET", "/api/health")
        assert status == 200
        assert json.loads(payload)["status"] == "ok"

        status, _headers, payload = _request(port, "GET", "/")
        assert status == 401
        assert b"partner-demo access token" in payload

        status, _headers, payload = _request(
            port,
            "POST",
            "/api/runs",
            body="{}",
            headers={"Content-Type": "application/json"},
        )
        assert status == 401
        assert json.loads(payload) == {"error": "authentication_required"}
        assert manager.start_called is False

        form = urlencode({"token": token})
        status, response_headers, _payload = _request(
            port,
            "POST",
            "/access",
            body=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert status == 303
        cookie = response_headers["Set-Cookie"]
        assert "backbond_demo_access=" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=Strict" in cookie
        assert "Secure" in cookie
        assert token not in cookie

        session = cookie.split(";", 1)[0]
        status, _headers, payload = _request(port, "GET", "/api/deployment", headers={"Cookie": session})
        assert status == 200
        assert json.loads(payload)["environment"]

        status, _headers, payload = _request(
            port,
            "GET",
            "/api/deployment",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert status == 200
        assert json.loads(payload)["environment"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
