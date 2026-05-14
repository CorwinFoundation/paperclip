"""Unit tests for harness.egress — BEAAA-334.

The adapter must be byte-equivalent to bare httpx when the env var is unset
(default OFF / rollback contract), and must inject sticky-session usernames
correctly when `BEAAA_RESI_PROXY_ROTATE=per_session` is set.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import pytest

from harness import egress


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(egress.PROXY_ENV, raising=False)
    monkeypatch.delenv(egress.ROTATE_ENV, raising=False)
    yield


def test_default_off_returns_none(monkeypatch):
    assert egress.proxy_url() is None
    assert egress.is_enabled() is False
    assert egress.playwright_proxy() is None


def test_enabled_no_rotation_returns_verbatim(monkeypatch):
    monkeypatch.setenv(egress.PROXY_ENV, "http://u:p@gate.example.com:7000")
    assert egress.proxy_url() == "http://u:p@gate.example.com:7000"
    assert egress.is_enabled() is True


def test_per_session_injection(monkeypatch):
    monkeypatch.setenv(egress.PROXY_ENV, "http://u:p@gate.example.com:7000")
    monkeypatch.setenv(egress.ROTATE_ENV, "per_session")
    url = egress.proxy_url(session_id="abc123")
    p = urlparse(url)
    assert p.scheme == "http"
    assert p.hostname == "gate.example.com"
    assert p.port == 7000
    assert p.password == "p"
    assert p.username == "u-session-abc123"


def test_per_session_without_session_id_is_passthrough(monkeypatch):
    # per_session requires a caller-supplied session_id; without one,
    # return the URL verbatim.
    monkeypatch.setenv(egress.PROXY_ENV, "http://u:p@gate.example.com:7000")
    monkeypatch.setenv(egress.ROTATE_ENV, "per_session")
    assert egress.proxy_url() == "http://u:p@gate.example.com:7000"


def test_per_request_injects_random_token(monkeypatch):
    monkeypatch.setenv(egress.PROXY_ENV, "http://u:p@gate.example.com:7000")
    monkeypatch.setenv(egress.ROTATE_ENV, "per_request")
    u1 = egress.proxy_url()
    u2 = egress.proxy_url()
    # Two consecutive calls produce different sessions (random per call).
    assert u1 != u2
    p1, p2 = urlparse(u1), urlparse(u2)
    assert p1.username.startswith("u-session-")
    assert p2.username.startswith("u-session-")
    assert p1.username != p2.username


def test_unknown_rotation_mode_is_passthrough(monkeypatch):
    monkeypatch.setenv(egress.PROXY_ENV, "http://u:p@gate.example.com:7000")
    monkeypatch.setenv(egress.ROTATE_ENV, "garbage_value")
    assert egress.proxy_url(session_id="abc") == "http://u:p@gate.example.com:7000"


def test_no_username_proxy_passthrough(monkeypatch):
    # Some proxy URLs are IP-allowlisted instead of user:pass authed.
    monkeypatch.setenv(egress.PROXY_ENV, "http://gate.example.com:7000")
    monkeypatch.setenv(egress.ROTATE_ENV, "per_session")
    assert egress.proxy_url(session_id="abc") == "http://gate.example.com:7000"


def test_playwright_proxy_shape(monkeypatch):
    monkeypatch.setenv(egress.PROXY_ENV, "http://u:p@gate.example.com:7000")
    pp = egress.playwright_proxy()
    assert pp == {
        "server": "http://gate.example.com:7000",
        "username": "u",
        "password": "p",
    }


def test_playwright_proxy_with_session_injection(monkeypatch):
    monkeypatch.setenv(egress.PROXY_ENV, "http://u:p@gate.example.com:7000")
    monkeypatch.setenv(egress.ROTATE_ENV, "per_session")
    pp = egress.playwright_proxy(session_id="xyz")
    assert pp["server"] == "http://gate.example.com:7000"
    assert pp["username"] == "u-session-xyz"
    assert pp["password"] == "p"


def test_client_passthrough_when_disabled(monkeypatch):
    # When the env var is unset, the wrapper returns a bare httpx.Client.
    import httpx
    c = egress.client(timeout=5.0)
    assert isinstance(c, httpx.Client)
    c.close()
