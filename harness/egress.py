"""Egress adapter — env-var-gated outbound HTTP proxy for the ARE Scanner.

Wraps `httpx` so any caller can opt into residential-proxy egress without
changing call shape. Default behavior (env var unset) is byte-equivalent
to bare `httpx`. Enabled by setting `BEAAA_RESI_PROXY_URL`; the rollback
is a single env-var flip per BEAAA-334 AC.

The adapter is infrastructure, not scoring math: no rubric, scanner_version,
corpus_version, or seed semantics change. The reproducibility band on the
golden set must hold whether the proxy is on or off (BEAAA-334 AC #5).

Env vars
--------
BEAAA_RESI_PROXY_URL
    Proxy URL, e.g. `http://user:pass@gate.smartproxy.com:7000`. When unset,
    every wrapper call below is a pass-through to bare `httpx`.

BEAAA_RESI_PROXY_ROTATE
    `per_session` — inject the caller-supplied `session_id` into the proxy
                    username (provider sticky-session convention) so each
                    scanner session is bound to one exit IP for its lifetime.
    `per_request` — inject a fresh random session token per outbound call.
    unset / other  — pass the configured proxy URL through verbatim
                     (provider default rotation, typically per-request).

The username-injection format follows the Smartproxy/Decodo and Oxylabs
sticky-session convention: `user-session-<id>:pass@host:port`. BrightData
uses `user-session-<id>` as well. Verified shape, not vendor-specific code.
"""
from __future__ import annotations

import os
import secrets
from typing import Any
from urllib.parse import urlparse, urlunparse

PROXY_ENV = "BEAAA_RESI_PROXY_URL"
ROTATE_ENV = "BEAAA_RESI_PROXY_ROTATE"


def _inject_session(proxy_url: str, session_id: str) -> str:
    """Bind a sticky session by suffixing the proxy username with `-session-<id>`.

    Returns the original URL unchanged if it has no username component.
    """
    p = urlparse(proxy_url)
    if not p.username:
        return proxy_url
    new_user = f"{p.username}-session-{session_id}"
    auth = new_user if not p.password else f"{new_user}:{p.password}"
    host = p.hostname or ""
    netloc = f"{auth}@{host}" if host else auth
    if p.port:
        netloc = f"{netloc}:{p.port}"
    return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))


def proxy_url(*, session_id: str | None = None) -> str | None:
    """Return the proxy URL to use for an outbound request, or None for direct egress.

    The `session_id` parameter is consulted only when `BEAAA_RESI_PROXY_ROTATE`
    is `per_session`. With `per_request`, a fresh random token is used and the
    caller-supplied session_id is ignored. With the env var unset, the URL is
    returned verbatim (provider-default rotation).
    """
    base = os.environ.get(PROXY_ENV)
    if not base:
        return None
    rotate = os.environ.get(ROTATE_ENV, "")
    if rotate == "per_session" and session_id:
        return _inject_session(base, session_id)
    if rotate == "per_request":
        return _inject_session(base, secrets.token_hex(8))
    return base


def is_enabled() -> bool:
    return bool(os.environ.get(PROXY_ENV))


def client(*, timeout: float = 30.0, session_id: str | None = None, **kwargs: Any):
    """Return an `httpx.Client` honoring the egress env vars.

    Pass-through to `httpx.Client(timeout=...)` when no proxy is configured.
    """
    import httpx  # type: ignore

    p = proxy_url(session_id=session_id)
    if p:
        return httpx.Client(timeout=timeout, proxies=p, **kwargs)
    return httpx.Client(timeout=timeout, **kwargs)


def post(url: str, *, session_id: str | None = None, **kwargs: Any):
    """Drop-in replacement for `httpx.post(...)` that honors the egress env vars."""
    import httpx  # type: ignore

    p = proxy_url(session_id=session_id)
    if p:
        timeout = kwargs.pop("timeout", 30.0)
        with httpx.Client(timeout=timeout, proxies=p) as c:
            return c.post(url, **kwargs)
    return httpx.post(url, **kwargs)


def get(url: str, *, session_id: str | None = None, **kwargs: Any):
    """Drop-in replacement for `httpx.get(...)` that honors the egress env vars."""
    import httpx  # type: ignore

    p = proxy_url(session_id=session_id)
    if p:
        timeout = kwargs.pop("timeout", 30.0)
        with httpx.Client(timeout=timeout, proxies=p) as c:
            return c.get(url, **kwargs)
    return httpx.get(url, **kwargs)


def playwright_proxy(*, session_id: str | None = None) -> dict[str, str] | None:
    """Return a Playwright-shape proxy dict, or None for direct egress.

    Playwright accepts `proxy={"server": "...", "username": "...", "password": "..."}`
    on `browser_type.launch(...)`. This helper parses the configured URL into
    that shape and applies sticky-session injection per the same env-var rules
    used by the httpx wrappers.
    """
    p_str = proxy_url(session_id=session_id)
    if not p_str:
        return None
    p = urlparse(p_str)
    server = f"{p.scheme}://{p.hostname}"
    if p.port:
        server = f"{server}:{p.port}"
    out: dict[str, str] = {"server": server}
    if p.username:
        out["username"] = p.username
    if p.password:
        out["password"] = p.password
    return out
