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


# --- BEAAA-540: per-scan egress allow-list -----------------------------------


def _record_transport(record: list, status: int = 200, headers: dict | None = None,
                      body: bytes = b"ok"):
    """Build an httpx.MockTransport that records every request it sees.

    Plugged in as the *inner* transport of `_AllowlistTransport` so the
    allow-list check runs first; anything that gets here is what would
    have hit the network.
    """
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        record.append(request)
        return httpx.Response(status, headers=headers or {}, content=body)

    return httpx.MockTransport(handler)


def _public_resolver(host: str, port: int) -> tuple[str, ...]:
    assert host == "agent.customer.example.com"
    assert port == 443
    return ("93.184.216.34",)


def test_scoped_allowlist_requires_host():
    with pytest.raises(ValueError):
        egress.ScopedEgressAllowlist("")


def test_scoped_allowlist_allows_declared_host():
    import httpx
    sent: list = []
    scope = egress.ScopedEgressAllowlist("agent.customer.example.com")
    wrapped = egress._AllowlistTransport(scope.allowed_host, _record_transport(sent))
    with httpx.Client(transport=wrapped) as c:
        # Arbitrary path, port, https scheme — all allowed on the declared host.
        r = c.get("https://agent.customer.example.com:8443/probe/v1?x=1")
        assert r.status_code == 200
    assert len(sent) == 1
    assert sent[0].url.host == "agent.customer.example.com"


def test_endpoint_scoped_allowlist_allows_declared_scheme_and_port():
    import httpx

    sent: list = []
    scope = egress.ScopedEgressAllowlist(
        "https://agent.customer.example.com/probe",
        resolver=_public_resolver,
    )
    wrapped = egress._AllowlistTransport(
        scope.allowed_targets,
        _record_transport(sent),
        resolver=_public_resolver,
    )
    with httpx.Client(transport=wrapped) as c:
        r = c.get("https://agent.customer.example.com/probe/v1?x=1")
        assert r.status_code == 200
    assert len(sent) == 1
    assert sent[0].url.host == "agent.customer.example.com"


def test_endpoint_scoped_allowlist_blocks_http_downgrade():
    import httpx

    sent: list = []
    scope = egress.ScopedEgressAllowlist(
        "https://agent.customer.example.com/probe",
        resolver=_public_resolver,
    )
    wrapped = egress._AllowlistTransport(
        scope.allowed_targets,
        _record_transport(sent),
        resolver=_public_resolver,
    )
    with httpx.Client(transport=wrapped) as c:
        with pytest.raises(egress.EgressNotAllowed):
            c.get("http://agent.customer.example.com/probe")
    assert sent == []


def test_endpoint_scoped_allowlist_blocks_wrong_port():
    import httpx

    sent: list = []
    scope = egress.ScopedEgressAllowlist(
        "https://agent.customer.example.com/probe",
        resolver=_public_resolver,
    )
    wrapped = egress._AllowlistTransport(
        scope.allowed_targets,
        _record_transport(sent),
        resolver=_public_resolver,
    )
    with httpx.Client(transport=wrapped) as c:
        with pytest.raises(egress.EgressNotAllowed):
            c.get("https://agent.customer.example.com:8443/probe")
    assert sent == []


def test_endpoint_scoped_allowlist_blocks_dns_rebind_to_private_ip():
    import httpx

    def private_resolver(host: str, port: int) -> tuple[str, ...]:
        assert host == "agent.customer.example.com"
        assert port == 443
        return ("10.0.0.7",)

    sent: list = []
    scope = egress.ScopedEgressAllowlist(
        "https://agent.customer.example.com/probe",
        resolver=private_resolver,
    )
    wrapped = egress._AllowlistTransport(
        scope.allowed_targets,
        _record_transport(sent),
        resolver=private_resolver,
    )
    with httpx.Client(transport=wrapped) as c:
        with pytest.raises(egress.EgressNotAllowed):
            c.get("https://agent.customer.example.com/probe")
    assert sent == []


def test_endpoint_scoped_allowlist_allows_explicit_loopback_endpoint():
    import httpx

    sent: list = []
    scope = egress.ScopedEgressAllowlist("http://127.0.0.2:50123/probe")
    wrapped = egress._AllowlistTransport(scope.allowed_targets, _record_transport(sent))
    with httpx.Client(transport=wrapped) as c:
        r = c.post("http://127.0.0.2:50123/probe")
        assert r.status_code == 200
    assert len(sent) == 1
    assert sent[0].url.host == "127.0.0.2"


@pytest.mark.parametrize("bad_host", [
    "127.0.0.1",
    "localhost",
    "metadata.google.internal",
    "169.254.169.254",
    "evil.example.org",
    "AGENT.customer.example.COM.evil.org",
])
def test_scoped_allowlist_blocks_other_hosts(bad_host):
    import httpx
    sent: list = []
    scope = egress.ScopedEgressAllowlist("agent.customer.example.com")
    wrapped = egress._AllowlistTransport(scope.allowed_host, _record_transport(sent))
    with httpx.Client(transport=wrapped) as c:
        with pytest.raises(egress.EgressNotAllowed):
            c.get(f"http://{bad_host}/")
    # Allow-list refused before the inner transport ever saw the request.
    assert sent == []


def test_scoped_allowlist_is_case_insensitive_on_host():
    import httpx
    sent: list = []
    scope = egress.ScopedEgressAllowlist("Agent.Customer.Example.COM")
    wrapped = egress._AllowlistTransport(scope.allowed_host, _record_transport(sent))
    with httpx.Client(transport=wrapped) as c:
        r = c.get("https://agent.customer.example.com/path")
        assert r.status_code == 200
    assert len(sent) == 1


def test_scoped_allowlist_blocks_redirect_to_internal_service():
    """Redirect to a non-allowed host must be refused at the allow-list layer.

    The first response is a 302 with `Location: http://internal-service/`.
    httpx, when `follow_redirects=True`, builds a new Request and re-runs
    it through the transport — which is exactly where we want to catch it.
    Bytes for the redirect target never leave the process.
    """
    import httpx

    sent: list = []
    scope = egress.ScopedEgressAllowlist("agent.customer.example.com")
    inner = _record_transport(
        sent,
        status=302,
        headers={"Location": "http://internal-service/"},
    )
    wrapped = egress._AllowlistTransport(scope.allowed_host, inner)
    with httpx.Client(transport=wrapped, follow_redirects=True) as c:
        with pytest.raises(egress.EgressNotAllowed):
            c.get("https://agent.customer.example.com/probe")
    # The initial request reached the inner transport (recorded). The
    # redirect re-issue was refused before reaching it.
    assert len(sent) == 1
    assert sent[0].url.host == "agent.customer.example.com"


def test_scoped_allowlist_context_manager_protocol():
    scope = egress.ScopedEgressAllowlist("agent.customer.example.com")
    with scope as s:
        assert s is scope
        assert s.allowed_host == "agent.customer.example.com"


def test_scoped_allowlist_client_factory_returns_httpx_client():
    import httpx
    scope = egress.ScopedEgressAllowlist("agent.customer.example.com")
    c = scope.client(timeout=5.0)
    try:
        assert isinstance(c, httpx.Client)
    finally:
        c.close()


def test_scoped_allowlist_bypasses_residential_proxy_env(monkeypatch):
    """BEAAA-7910: bind-path scoped egress is direct-by-construction and must
    not consult the residential-proxy env path, even when it is set."""
    import httpx

    sent: list = []
    monkeypatch.setenv(egress.PROXY_ENV, "http://u:p@gate.example.com:7000")
    monkeypatch.setattr(
        egress,
        "proxy_url",
        lambda **_kwargs: pytest.fail("ScopedEgressAllowlist consulted proxy_url"),
    )
    scope = egress.ScopedEgressAllowlist("agent.customer.example.com")
    transport = egress._AllowlistTransport(
        scope.allowed_host,
        _record_transport(sent),
    )
    with httpx.Client(transport=transport) as c:
        r = c.get("https://agent.customer.example.com/probe")
    assert r.status_code == 200

    # scope.client() builds its transport via _wrap(); the monkeypatched
    # proxy_url() fail() above trips if the scoped path consults the proxy env.
    with scope.client(timeout=5.0) as c:
        assert isinstance(c, httpx.Client)
