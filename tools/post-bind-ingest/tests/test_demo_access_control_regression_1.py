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

from app import DemoHandler, RunQuota, _access_token_from_env  # noqa: E402


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


def test_run_quota_is_enforced_per_client_and_resets() -> None:
    quota = RunQuota(max_requests=2, window_seconds=60)
    assert quota.allow("client-a", now=0) == (True, 0)
    assert quota.allow("client-a", now=1) == (True, 0)
    allowed, retry_after = quota.allow("client-a", now=2)
    assert not allowed
    assert retry_after == 58
    assert quota.allow("client-b", now=2) == (True, 0)
    assert quota.allow("client-a", now=61) == (True, 0)


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