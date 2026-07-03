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
import ipaddress
import json
import secrets
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse, urlunparse

PROXY_ENV = "BEAAA_RESI_PROXY_URL"
ROTATE_ENV = "BEAAA_RESI_PROXY_ROTATE"
LIVE_BIND_NFT_ENV = "ARE_LIVE_BIND_NFT"
DIRECT_EGRESS_NFT_ENV = "ARE_DIRECT_EGRESS_NFT"
LIVE_BIND_NFT_TTL_ENV = "ARE_LIVE_BIND_NFT_TTL_SECONDS"
NFT_BIN_ENV = "ARE_LIVE_BIND_NFT_BIN"
NFT_FAMILY_ENV = "ARE_LIVE_BIND_NFT_FAMILY"
NFT_TABLE_ENV = "ARE_LIVE_BIND_NFT_TABLE"
NFT_CHAIN_ENV = "ARE_LIVE_BIND_NFT_CHAIN"
DEFAULT_LIVE_BIND_NFT_TTL_SECONDS = 3600
DEFAULT_NFT_FAMILY = "inet"
DEFAULT_NFT_TABLE = "are_egress_filter"
DEFAULT_NFT_CHAIN = "output"
LIVE_BIND_COMMENT_PREFIX = "BEAAA-8263-direct-live-bind"
LIVE_EGRESS_NEGATIVE_CONTROL_ARTIFACT_ENV = (
    "BACKBOND_LIVE_EGRESS_NEGATIVE_CONTROL_ARTIFACT"
)
LIVE_EGRESS_NEGATIVE_CONTROL_TARGET_ENV = (
    "BACKBOND_LIVE_EGRESS_NEGATIVE_CONTROL_TARGET"
)
DEFAULT_LIVE_EGRESS_NEGATIVE_CONTROL_TARGET = "http://example.com/"


class LiveEgressPostureError(RuntimeError):
    """Live-probe egress fail-closed posture could not be proven."""


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
    caller-supplied session_id is ignored. With `sticky_port`, the URL is
    returned verbatim — the configured endpoint port is itself the session
    handle (Decodo sticky-session entries, e.g. us.decodo.com:10001+, where
    `user-session-<id>` suffix is rejected with 407). With the env var unset,
    the URL is returned verbatim (provider-default rotation).
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
        return httpx.Client(timeout=timeout, proxy=p, **kwargs)
    return httpx.Client(timeout=timeout, **kwargs)


def post(url: str, *, session_id: str | None = None, **kwargs: Any):
    """Drop-in replacement for `httpx.post(...)` that honors the egress env vars."""
    import httpx  # type: ignore

    p = proxy_url(session_id=session_id)
    if p:
        timeout = kwargs.pop("timeout", 30.0)
        with httpx.Client(timeout=timeout, proxy=p) as c:
            return c.post(url, **kwargs)
    return httpx.post(url, **kwargs)


def get(url: str, *, session_id: str | None = None, **kwargs: Any):
    """Drop-in replacement for `httpx.get(...)` that honors the egress env vars."""
    import httpx  # type: ignore

    p = proxy_url(session_id=session_id)
    if p:
        timeout = kwargs.pop("timeout", 30.0)
        with httpx.Client(timeout=timeout, proxy=p) as c:
            return c.get(url, **kwargs)
    return httpx.get(url, **kwargs)


class EgressNotAllowed(RuntimeError):
    """Outbound request targeted a host outside the active scoped allow-list."""


Resolver = Callable[[str, int], Iterable[str]]
CommandExecutor = Callable[[list[str]], str]


def verify_live_egress_fail_closed(
    *,
    scanner_version: str,
    corpus_version: str,
    seed: int,
    execution_environment: str = "local",
    artifact_path: str | os.PathLike[str] | None = None,
    timestamp_factory: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Run the live-egress negative control and optionally persist evidence."""
    target = os.environ.get(
        LIVE_EGRESS_NEGATIVE_CONTROL_TARGET_ENV,
        DEFAULT_LIVE_EGRESS_NEGATIVE_CONTROL_TARGET,
    )
    now = (timestamp_factory or (lambda: datetime.now(timezone.utc)))()
    record: dict[str, Any] = {
        "control": "live_egress_negative_control",
        "timestamp": now.astimezone(timezone.utc).isoformat(),
        "scanner_version": scanner_version,
        "corpus_version": corpus_version,
        "seed": seed,
        "execution_environment": execution_environment,
        "target_url": target,
        "blocked": False,
        "network_success": False,
    }

    try:
        with ScopedEgressAllowlist([]).client(timeout=2.0, follow_redirects=False) as c:
            response = c.get(target)
        record.update({
            "network_success": True,
            "status_code": response.status_code,
            "failure": "forbidden_target_reached_network",
        })
    except EgressNotAllowed as exc:
        record.update({
            "blocked": True,
            "block_type": type(exc).__name__,
            "block_message": str(exc),
        })
    except Exception as exc:  # pragma: no cover - depends on local transport stack
        record.update({
            "blocked": True,
            "block_type": type(exc).__name__,
            "block_message": str(exc),
        })

    path = artifact_path or os.environ.get(LIVE_EGRESS_NEGATIVE_CONTROL_ARTIFACT_ENV)
    if path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if record["network_success"]:
        raise LiveEgressPostureError(
            "live_egress_negative_control_failed: forbidden target reached network"
        )
    if not record["blocked"]:
        raise LiveEgressPostureError(
            "live_egress_negative_control_failed: default-deny posture unproven"
        )
    return record


@dataclass(frozen=True)
class _AllowedEndpoint:
    host: str
    scheme: str | None
    port: int | None
    host_is_ip_literal: bool

    @property
    def pins_scheme_port(self) -> bool:
        return self.scheme is not None and self.port is not None


def _default_port(scheme: str) -> int | None:
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _parse_allowed_endpoint(value: str) -> _AllowedEndpoint:
    raw = value.strip()
    if "://" not in raw:
        host = raw.lower()
        return _AllowedEndpoint(
            host=host,
            scheme=None,
            port=None,
            host_is_ip_literal=_is_ip_literal(host),
        )
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(
            "ScopedEgressAllowlist endpoint URLs must use http/https; "
            f"got {parsed.scheme!r}"
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(
            f"ScopedEgressAllowlist endpoint URL has no host: {value!r}"
        )
    port = parsed.port or _default_port(scheme)
    if port is None:
        raise ValueError(
            f"ScopedEgressAllowlist endpoint URL has no effective port: {value!r}"
        )
    return _AllowedEndpoint(
        host=host,
        scheme=scheme,
        port=port,
        host_is_ip_literal=_is_ip_literal(host),
    )


def _default_resolver(host: str, port: int) -> tuple[str, ...]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    resolved: list[str] = []
    seen: set[str] = set()
    for info in infos:
        address = info[4][0]
        if address not in seen:
            resolved.append(address)
            seen.add(address)
    return tuple(resolved)


def _default_command_executor(cmd: list[str]) -> str:
    completed = subprocess.run(  # noqa: S603 - argv is fixed by this module.
        cmd,
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout


def _is_forbidden_resolved_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _live_bind_ttl_seconds() -> int:
    raw = os.environ.get(LIVE_BIND_NFT_TTL_ENV, "")
    if not raw:
        return DEFAULT_LIVE_BIND_NFT_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_LIVE_BIND_NFT_TTL_SECONDS
    return max(60, min(value, 24 * 60 * 60))


def _parse_live_bind_target(
    allowed_endpoints: tuple[_AllowedEndpoint, ...],
) -> tuple[str, int] | None:
    if len(allowed_endpoints) != 1:
        return None
    endpoint = allowed_endpoints[0]
    return endpoint.host, endpoint.port or 443


def _comment_expr_value(expr: Any) -> str | None:
    if isinstance(expr, dict):
        value = expr.get("comment")
        if isinstance(value, str):
            return value
    return None


def _comment_field(rule: dict[str, Any]) -> str:
    comment = rule.get("comment")
    if isinstance(comment, str):
        return comment
    for expr in rule.get("expr", []):
        comment = _comment_expr_value(expr)
        if comment is not None:
            return comment
    return ""


def _iter_live_bind_rule_handles(
    list_output: str,
    *,
    family: str,
    table: str,
    chain: str,
    predicate: Callable[[str], bool],
) -> Iterable[int]:
    try:
        payload = json.loads(list_output or "{}")
    except json.JSONDecodeError:
        return ()
    handles: list[int] = []
    for item in payload.get("nftables", []):
        rule = item.get("rule") if isinstance(item, dict) else None
        if not isinstance(rule, dict):
            continue
        if (
            rule.get("family") != family
            or rule.get("table") != table
            or rule.get("chain") != chain
        ):
            continue
        comment = _comment_field(rule)
        if not predicate(comment):
            continue
        handle = rule.get("handle")
        if isinstance(handle, int):
            handles.append(handle)
    return tuple(handles)


class _LiveBindNftScope:
    """Short-lived host nft rule for direct customer endpoint egress."""

    def __init__(
        self,
        *,
        allowed_endpoints: tuple[_AllowedEndpoint, ...],
        scope_id: str | None,
        resolver: Resolver = _default_resolver,
        command_executor: CommandExecutor | None = None,
    ):
        self._scope_id = scope_id or ""
        self._enabled = bool(self._scope_id) and (
            _env_truthy(DIRECT_EGRESS_NFT_ENV) or _env_truthy(LIVE_BIND_NFT_ENV)
        )
        self._target = _parse_live_bind_target(allowed_endpoints)
        self._resolver = resolver
        self._exec = command_executor or _default_command_executor
        self._nft = os.environ.get(NFT_BIN_ENV, "nft")
        self._family = os.environ.get(NFT_FAMILY_ENV, DEFAULT_NFT_FAMILY)
        self._table = os.environ.get(NFT_TABLE_ENV, DEFAULT_NFT_TABLE)
        self._chain = os.environ.get(NFT_CHAIN_ENV, DEFAULT_NFT_CHAIN)
        self._comments: list[str] = []

    def __enter__(self) -> "_LiveBindNftScope":
        if not self._enabled:
            return self
        if self._target is None:
            raise EgressNotAllowed(
                "direct live-bind nft scope requires exactly one declared endpoint"
            )
        self._sweep_expired()
        host, port = self._target
        addresses = tuple(self._resolver(host, port))
        if not addresses:
            raise EgressNotAllowed(
                f"direct live-bind nft scope resolved no addresses for {host!r}"
            )
        expires = int(time.time()) + _live_bind_ttl_seconds()
        for address in addresses:
            if _is_forbidden_resolved_ip(address):
                raise EgressNotAllowed(
                    f"direct live-bind nft scope resolved forbidden address {address!r}"
                )
            family = "ip6" if ipaddress.ip_address(address).version == 6 else "ip"
            comment = (
                f"{LIVE_BIND_COMMENT_PREFIX} scan_id={self._scope_id} "
                f"host={host} port={port} expires={expires}"
            )
            self._exec([
                self._nft,
                "add",
                "rule",
                self._family,
                self._table,
                self._chain,
                family,
                "daddr",
                address,
                "tcp",
                "dport",
                str(port),
                "meta",
                "skuid",
                "0",
                "accept",
                "comment",
                json.dumps(comment),
            ])
            self._comments.append(comment)
        return self

    def __exit__(self, *exc) -> None:
        if self._enabled:
            self._delete_matching(lambda comment: comment in set(self._comments))
        return None

    def _sweep_expired(self) -> None:
        now = int(time.time())

        def expired(comment: str) -> bool:
            if not comment.startswith(LIVE_BIND_COMMENT_PREFIX):
                return False
            for part in comment.split():
                if part.startswith("expires="):
                    try:
                        return int(part.split("=", 1)[1]) <= now
                    except ValueError:
                        return False
            return False

        self._delete_matching(expired)

    def _delete_matching(self, predicate: Callable[[str], bool]) -> None:
        output = self._exec([
            self._nft,
            "-j",
            "-a",
            "list",
            "chain",
            self._family,
            self._table,
            self._chain,
        ])
        handles = _iter_live_bind_rule_handles(
            output,
            family=self._family,
            table=self._table,
            chain=self._chain,
            predicate=predicate,
        )
        for handle in handles:
            self._exec([
                self._nft,
                "delete",
                "rule",
                self._family,
                self._table,
                self._chain,
                "handle",
                str(handle),
            ])


class _AllowlistTransport:
    """httpx transport wrapper that enforces a host allow-list.

    Sits in front of the real transport so the host check fires on every
    request the client makes — initial calls AND any redirect re-issues
    (httpx routes redirected requests back through the transport). A
    rejected request raises before any bytes leave the process.

    Accepts either a single host string (legacy single-host scope), a full
    http(s) endpoint URL (scheme + effective port pinned), or an iterable of
    either form (BEAAA-542 list form; empty iterable means deny-all and is used
    by the Scorer worker).
    """

    def __init__(
        self,
        allowed_host,
        inner: Any,
        *,
        resolver: Resolver = _default_resolver,
    ):
        if isinstance(allowed_host, str):
            self._allowed_endpoints: tuple[_AllowedEndpoint, ...] = (
                (_parse_allowed_endpoint(allowed_host),) if allowed_host else ()
            )
        else:
            self._allowed_endpoints = tuple(
                _parse_allowed_endpoint(h) for h in allowed_host if h
            )
        self._inner = inner
        self._resolver = resolver

    @property
    def _allowed_host(self) -> str:
        # Back-compat accessor used by older callers / tests that inspect
        # the transport for the single declared host.
        return self._allowed_endpoints[0].host if self._allowed_endpoints else ""

    def _matching_endpoints(self, host: str) -> tuple[_AllowedEndpoint, ...]:
        return tuple(e for e in self._allowed_endpoints if e.host == host)

    def _permitted_repr(self) -> str:
        if not self._allowed_endpoints:
            return "[] (deny-all)"
        if len(self._allowed_endpoints) == 1:
            return repr(self._allowed_endpoints[0].host)
        return repr([e.host for e in self._allowed_endpoints])

    def _effective_request_port(self, request: Any) -> int | None:
        if request.url.port:
            return int(request.url.port)
        return _default_port((request.url.scheme or "").lower())

    def _enforce_scheme_port(
        self,
        *,
        host: str,
        scheme: str,
        port: int | None,
        matches: tuple[_AllowedEndpoint, ...],
    ) -> _AllowedEndpoint:
        host_only = tuple(e for e in matches if not e.pins_scheme_port)
        if host_only:
            return host_only[0]
        for endpoint in matches:
            if endpoint.scheme == scheme and endpoint.port == port:
                return endpoint
        allowed = ", ".join(
            f"{endpoint.scheme}://{endpoint.host}:{endpoint.port}"
            for endpoint in matches
        )
        raise EgressNotAllowed(
            f"egress to {scheme}://{host}:{port} not allowed; "
            f"scoped allow-list permits only {allowed}"
        )

    def _enforce_resolved_address(
        self,
        endpoint: _AllowedEndpoint,
        *,
        effective_port: int | None,
    ) -> None:
        port = endpoint.port if endpoint.port is not None else effective_port
        if port is None:
            raise EgressNotAllowed(
                f"egress host {endpoint.host!r} has no effective port"
            )
        if endpoint.host_is_ip_literal:
            if _is_forbidden_resolved_ip(endpoint.host):
                raise EgressNotAllowed(
                    f"egress host {endpoint.host!r} resolved to forbidden "
                    f"address {endpoint.host!r}"
                )
            return
        try:
            addresses = tuple(self._resolver(endpoint.host, port))
        except OSError as exc:
            raise EgressNotAllowed(
                f"egress host {endpoint.host!r} could not be resolved"
            ) from exc
        for address in addresses:
            if _is_forbidden_resolved_ip(address):
                raise EgressNotAllowed(
                    f"egress host {endpoint.host!r} resolved to forbidden "
                    f"address {address!r}"
                )

    def handle_request(self, request: Any) -> Any:
        host = (request.url.host or "").lower()
        matches = self._matching_endpoints(host)
        if not matches:
            raise EgressNotAllowed(
                f"egress to host {host!r} not allowed; "
                f"scoped allow-list permits only {self._permitted_repr()}"
            )
        effective_port = self._effective_request_port(request)
        endpoint = self._enforce_scheme_port(
            host=host,
            scheme=(request.url.scheme or "").lower(),
            port=effective_port,
            matches=matches,
        )
        self._enforce_resolved_address(endpoint, effective_port=effective_port)
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
      declared host. The original Prober use case; legacy host-only scopes
      are host-scoped for compatibility while still rejecting resolved private,
      loopback, link-local, multicast, unspecified, and reserved addresses.
    * ``ScopedEgressAllowlist("https://agent.customer.example.com/probe")`` —
      endpoint-scoped form. Scheme and effective port are pinned, and DNS
      resolution to private, loopback, link-local, multicast, unspecified,
      or reserved address ranges is rejected before bytes leave the process.
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

    def __init__(
        self,
        allowed_host,
        *,
        resolver: Resolver = _default_resolver,
        live_bind_scope_id: str | None = None,
        live_bind_resolver: Resolver | None = None,
        live_bind_command_executor: CommandExecutor | None = None,
    ):
        if isinstance(allowed_host, str):
            if not allowed_host:
                raise ValueError(
                    "ScopedEgressAllowlist requires a non-empty allowed_host "
                    "or a list (possibly empty) of hosts"
                )
            self._allowed_endpoints: tuple[_AllowedEndpoint, ...] = (
                _parse_allowed_endpoint(allowed_host),
            )
        elif isinstance(allowed_host, (list, tuple)):
            # BEAAA-542: empty list expresses the Scorer deny-all posture.
            # Net-increase in tamper resistance, not a reduction.
            self._allowed_endpoints = tuple(
                _parse_allowed_endpoint(h) for h in allowed_host if h
            )
        else:
            raise ValueError(
                "ScopedEgressAllowlist expects str or list[str], "
                f"got {type(allowed_host).__name__}"
            )
        self._resolver = resolver
        self._live_bind = _LiveBindNftScope(
            allowed_endpoints=self._allowed_endpoints,
            scope_id=live_bind_scope_id,
            resolver=live_bind_resolver or resolver,
            command_executor=live_bind_command_executor,
        )

    @property
    def allowed_host(self) -> str:
        # Legacy single-host accessor. Empty string under the deny-all
        # (list = []) form, since there is no single declared host.
        return self._allowed_endpoints[0].host if self._allowed_endpoints else ""

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        """All declared hosts. Empty tuple under deny-all (BEAAA-542)."""
        return tuple(endpoint.host for endpoint in self._allowed_endpoints)

    @property
    def allowed_targets(self) -> tuple[str, ...]:
        """Declared allow-list targets as transport-parseable strings."""
        out: list[str] = []
        for endpoint in self._allowed_endpoints:
            if endpoint.pins_scheme_port:
                out.append(f"{endpoint.scheme}://{endpoint.host}:{endpoint.port}")
            else:
                out.append(endpoint.host)
        return tuple(out)

    def __enter__(self) -> "ScopedEgressAllowlist":
        self._live_bind.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        self._live_bind.__exit__(*exc)
        return None

    def _wrap(self, *, session_id: str | None) -> Any:
        import httpx  # type: ignore

        # BEAAA-7910: live-bind scoped customer egress must not consult or use
        # the residential-proxy env path. The shared launch guard refuses if
        # BEAAA_RESI_PROXY_URL is set; this direct transport is the bypass
        # proof for callers that construct a scope directly in tests.
        _ = session_id
        inner = httpx.HTTPTransport()
        return _AllowlistTransport(
            self.allowed_targets,
            inner,
            resolver=self._resolver,
        )

    def client(self, *, timeout: float = 30.0, session_id: str | None = None,
               **kwargs: Any):
        """Return an `httpx.Client` whose transport enforces the allow-list.

        BEAAA-7910: does not honor the `BEAAA_RESI_PROXY_*` env vars. Scoped
        live-probe traffic is direct-by-construction; generic non-scoped egress
        wrappers retain proxy support for non-bind use.
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
