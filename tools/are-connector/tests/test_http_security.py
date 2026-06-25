"""F-06 + F-10 security tests for are_connector.http — no nacl dependency."""
from __future__ import annotations

import pytest

from are_connector.http import EgressAllowlist, PortalClient, call_agent


# ---------------------------------------------------------------------------
# F-06: redirect rejection in PortalClient._request
# ---------------------------------------------------------------------------

def test_portal_client_request_raises_on_redirect(monkeypatch) -> None:
    import requests

    class RedirectResponse:
        status_code = 302
        headers = {"Location": "https://portal.example/other"}

        def raise_for_status(self) -> None:
            raise requests.exceptions.HTTPError("302", response=self)

    def fake_request(method, url, headers=None, **kwargs):
        return RedirectResponse()

    allowlist = EgressAllowlist("https://portal.example")
    client = PortalClient("https://portal.example", allowlist)
    monkeypatch.setattr(client.session, "request", fake_request)

    with pytest.raises(PermissionError, match="redirect blocked"):
        client.complete("scan-1")


def test_portal_client_request_passes_allow_redirects_false(monkeypatch) -> None:
    """_request must pass allow_redirects=False to session.request."""
    calls = []

    class OkResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {}

    def fake_request(method, url, headers=None, **kwargs):
        calls.append(kwargs)
        return OkResponse()

    allowlist = EgressAllowlist("https://portal.example")
    client = PortalClient("https://portal.example", allowlist)
    monkeypatch.setattr(client.session, "request", fake_request)
    client.complete("scan-1")

    assert calls[0].get("allow_redirects") is False


# ---------------------------------------------------------------------------
# F-06: redirect rejection in call_agent
# ---------------------------------------------------------------------------

def test_call_agent_raises_on_redirect(monkeypatch) -> None:
    import requests as req_mod
    from are_connector import http as http_mod

    class RedirectResponse:
        status_code = 302
        headers = {"Location": "https://agent.example/other"}

        def raise_for_status(self) -> None:
            raise req_mod.exceptions.HTTPError("302", response=self)

    def fake_post(url, json=None, timeout=None, stream=False, **kwargs):
        return RedirectResponse()

    monkeypatch.setattr(http_mod.requests, "post", fake_post)

    allowlist = EgressAllowlist("https://portal.example", "https://agent.example/run")
    with pytest.raises(PermissionError, match="redirect blocked"):
        call_agent("https://agent.example/run", {}, allowlist, timeout=10)


# ---------------------------------------------------------------------------
# F-10: https-only enforcement in _origin / EgressAllowlist
# ---------------------------------------------------------------------------

def test_origin_rejects_http_scheme_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ALLOW_PLAINTEXT_AGENT_ENDPOINT", raising=False)
    from are_connector import http as http_mod
    import importlib
    importlib.reload(http_mod)
    from are_connector.http import EgressAllowlist as EAR
    allowlist = EAR("https://portal.example", "http://agent.example/run")
    with pytest.raises((PermissionError, ValueError)):
        allowlist.assert_allowed("http://agent.example/run")


def test_origin_permits_http_scheme_with_env_flag(monkeypatch) -> None:
    monkeypatch.setenv("ALLOW_PLAINTEXT_AGENT_ENDPOINT", "1")
    from are_connector import http as http_mod
    import importlib
    importlib.reload(http_mod)
    from are_connector.http import EgressAllowlist as EAR
    allowlist = EAR("https://portal.example", "http://agent.example/run")
    allowlist.assert_allowed("http://agent.example/run")
