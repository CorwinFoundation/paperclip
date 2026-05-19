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

Per-scan allow-list (BEAAA-540)
--------------------------------
`ScopedEgressAllowlist(allowed_host=...)` restricts outbound HTTP to exactly
one declared host for the lifetime of a scan. Defense in depth against SSRF
via poisoned probe responses and lateral pivoting from a hijacked customer
endpoint toward IFA internals or third-party services. The check runs at the
httpx transport layer so it fires on the initial request AND on any redirect
re-issue — `Location: http://internal-service/` is refused before the second
request goes on the wire.

BEAAA-542 additive: the constructor also accepts a list of hosts, including
the empty list `[]` to express the Scorer-worker "deny all egress" posture.
Existing single-string callers are unchanged.
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


class EgressNotAllowed(RuntimeError):
    """Outbound request targeted a host outside the active scoped allow-list."""


class _AllowlistTransport:
    """httpx transport wrapper that enforces a host allow-list.

    Sits in front of the real transport so the host check fires on every
    request the client makes — initial calls AND any redirect re-issues
    (httpx routes redirected requests back through the transport). A
    rejected request raises before any bytes leave the process.

    Accepts either a single host string (legacy single-host scope) or an
    iterable of host strings (BEAAA-542 list form; empty iterable means
    deny-all and is used by the Scorer worker).
    """

    def __init__(self, allowed_host, inner: Any):
        if isinstance(allowed_host, str):
            self._allowed_hosts: tuple[str, ...] = (
                (allowed_host.lower(),) if allowed_host else ()
            )
        else:
            self._allowed_hosts = tuple(
                h.lower() for h in allowed_host if h
            )
        self._inner = inner

    @property
    def _allowed_host(self) -> str:
        # Back-compat accessor used by older callers / tests that inspect
        # the transport for the single declared host.
        return self._allowed_hosts[0] if self._allowed_hosts else ""

    def handle_request(self, request: Any) -> Any:
        host = (request.url.host or "").lower()
        if host not in self._allowed_hosts:
            permitted = (
                repr(self._allowed_hosts[0])
                if len(self._allowed_hosts) == 1
                else (
                    repr(list(self._allowed_hosts))
                    if self._allowed_hosts
                    else "[] (deny-all)"
                )
            )
            raise EgressNotAllowed(
                f"egress to host {host!r} not allowed; "
                f"scoped allow-list permits only {permitted}"
            )
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)


class ScopedEgressAllowlist:
    """Restrict outbound HTTP for a scan's lifetime.

    Built for the live-probe path described in ADR-0009 §5 #8: the Prober
    subprocess opens the scope around `agent_endpoint.host`, runs the
    probe corpus, and exits. Any non-allowed host — `127.0.0.1`, cloud
    metadata services (`169.254.169.254`, `metadata.google.internal`),
    arbitrary external hosts, or a follow-the-redirect target — raises
    `EgressNotAllowed` at the transport layer.

    Construction accepts either form:

    * ``ScopedEgressAllowlist("agent.customer.example.com")`` — single
      declared host. The original Prober use case.
    * ``ScopedEgressAllowlist([])`` — deny all egress. BEAAA-542 Scorer
      worker posture: the Scorer process has no credential read path AND
      no network surface, so any HTTP attempt raises.
    * ``ScopedEgressAllowlist(["host-a", "host-b"])`` — multi-host scope
      (currently unused by the live-probe path; reserved for future scans
      that legitimately need to reach more than one declared host).

    Usage::

        with ScopedEgressAllowlist("agent.customer.example.com") as scope:
            with scope.client(timeout=10.0) as c:
                r = c.post("https://agent.customer.example.com/probe", json=...)
    """

    def __init__(self, allowed_host):
        if isinstance(allowed_host, str):
            if not allowed_host:
                raise ValueError(
                    "ScopedEgressAllowlist requires a non-empty allowed_host "
                    "or a list (possibly empty) of hosts"
                )
            self._allowed_hosts: tuple[str, ...] = (allowed_host,)
        elif isinstance(allowed_host, (list, tuple)):
            # BEAAA-542: empty list expresses the Scorer deny-all posture.
            # Net-increase in tamper resistance, not a reduction.
            self._allowed_hosts = tuple(h for h in allowed_host if h)
        else:
            raise ValueError(
                "ScopedEgressAllowlist expects str or list[str], "
                f"got {type(allowed_host).__name__}"
            )

    @property
    def allowed_host(self) -> str:
        # Legacy single-host accessor. Empty string under the deny-all
        # (list = []) form, since there is no single declared host.
        return self._allowed_hosts[0] if self._allowed_hosts else ""

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        """All declared hosts. Empty tuple under deny-all (BEAAA-542)."""
        return self._allowed_hosts

    def __enter__(self) -> "ScopedEgressAllowlist":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def _wrap(self, *, session_id: str | None) -> Any:
        import httpx  # type: ignore

        p = proxy_url(session_id=session_id)
        inner = httpx.HTTPTransport(proxy=p) if p else httpx.HTTPTransport()
        return _AllowlistTransport(self._allowed_hosts, inner)

    def client(self, *, timeout: float = 30.0, session_id: str | None = None,
               **kwargs: Any):
        """Return an `httpx.Client` whose transport enforces the allow-list.

        Honors the `BEAAA_RESI_PROXY_*` env vars by routing the inner
        transport through the configured proxy when one is set.
        """
        import httpx  # type: ignore

        transport = self._wrap(session_id=session_id)
        kwargs.pop("proxies", None)
        kwargs.pop("proxy", None)
        return httpx.Client(timeout=timeout, transport=transport, **kwargs)


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
