"""Thin scanner client for the CI/CD plugin.

Wraps the three shipped routes the plugin needs behind an **injected**
transport so the acceptance matrix runs with an in-process double and the
container runtime uses a real ``urllib`` transport. No route logic is
re-implemented here — the client only speaks the shipped request/response
contract (``are-scanner-api-v1`` v1.7.0).

Transport failures are surfaced as :class:`ScanTransportError` carrying a
``kind`` that feeds the shipped WS-4 taxonomy (:func:`api.ci_scan_ext.
classify_error_class`) so "could not measure" never masquerades as a score.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from api.ci_scan_ext import verify_signature

# Band cut floors from the contract (are-scanner-api-v1 §Output: A 85+, B
# 70-84, C 55-69, D <55). Used only as a fallback when the scanner does not
# return an explicit numeric ``policy_band_floor`` for the vendor's
# contracted band. The authoritative threshold source is the API lookup
# (BEAAA-11725); this map is the documented last resort.
BAND_FLOOR: dict[str, float] = {"A": 85.0, "B": 70.0, "C": 55.0, "D": 0.0}


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))


class ScanTransportError(Exception):
    """A transport-layer failure reaching the scanner or agent endpoint.

    ``kind`` is one of the WS-4 infra kinds (``unreachable``, ``timeout``,
    ``http_5xx``, ``http_4xx``). Both 4xx and 5xx from the endpoint are
    transport signals that the scan could not be measured → ``infra``.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class Transport(Protocol):
    """Minimal request surface the client depends on (injectable)."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None = None,
    ) -> Response: ...


class ScanClient:
    """Speaks the shipped scanner routes over an injected transport."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        transport: Transport,
        webhook_secret: str | bytes | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport
        self._webhook_secret = webhook_secret

    def _auth_headers(self) -> dict[str, str]:
        # Scanner-side auth. The value is a secret and is never logged.
        return {"Authorization": f"Bearer {self._api_key}"}

    # -- §6 preflight ------------------------------------------------------
    def endpoint_self_identifies_production(
        self, agent_endpoint: str, auth_token: str | None
    ) -> bool:
        """Preflight the agent endpoint's WS-2 self-identification.

        Reads the standard-agent-interface metadata and returns ``True`` when
        the endpoint declares a production environment. Raises
        :class:`ScanTransportError` if the endpoint cannot be reached (the
        runner treats that as infra, not as a pass).
        """
        headers = {"Accept": "application/json"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        resp = self._transport.request(
            "GET", f"{agent_endpoint.rstrip('/')}/.well-known/agent",
            headers=headers,
        )
        if resp.status >= 400:
            raise ScanTransportError(
                "http_5xx" if resp.status >= 500 else "http_4xx",
                f"preflight returned {resp.status}",
            )
        meta = resp.json() or {}
        env = str(meta.get("environment", "")).strip().lower()
        return env in ("production", "prod", "live")

    # -- submit ------------------------------------------------------------
    def submit_scan(
        self, *, agent_endpoint: str, auth_token: str | None, scan_tier: str
    ) -> dict[str, Any]:
        """``POST /scanner/v1/scans`` → the 202 accept envelope (has scan_id)."""
        body: dict[str, Any] = {
            "agent_id": agent_endpoint,
            "agent_endpoint": agent_endpoint,
            "scan_tier": scan_tier,
            "scanner_invocation_context": {"purpose": "pre_bind"},
        }
        if auth_token:
            body["agent_auth_token_ref"] = "ci-secret"  # ref only, never the value
        payload = json.dumps(body).encode("utf-8")
        resp = self._transport.request(
            "POST", f"{self._base}/scanner/v1/scans",
            headers={**self._auth_headers(), "Content-Type": "application/json"},
            body=payload,
        )
        if resp.status >= 400:
            raise ScanTransportError(
                "http_5xx" if resp.status >= 500 else "http_4xx",
                f"submit returned {resp.status}",
            )
        data = resp.json() or {}
        if "scan_id" not in data:
            raise ScanTransportError("unreachable", "submit returned no scan_id")
        return data

    # -- poll --------------------------------------------------------------
    def poll_scan(
        self,
        scan_id: str,
        *,
        max_wait_s: float,
        sleep: Callable[[float], None] = time.sleep,
    ) -> dict[str, Any]:
        """``GET /scan/{scan_id}`` with bounded backoff to a terminal score.

        Returns the score envelope ``{scan_id, run_timestamp, are_total,
        band, ...}``. Raises :class:`ScanTransportError` on transport
        failure or on exhausting ``max_wait_s`` without a score.
        """
        waited = 0.0
        delay = 0.5
        attempts = 0
        while True:
            attempts += 1
            resp = self._transport.request(
                "GET", f"{self._base}/scan/{scan_id}",
                headers=self._auth_headers(),
            )
            if resp.status >= 500:
                raise ScanTransportError("http_5xx", f"poll {resp.status}")
            if resp.status == 200:
                data = resp.json() or {}
                if data.get("are_total") is not None:
                    return data
            elif resp.status >= 400 and resp.status != 404:
                raise ScanTransportError("http_4xx", f"poll {resp.status}")
            # pending (202 / 404-not-ready / 200-without-score): back off.
            if waited >= max_wait_s:
                raise ScanTransportError(
                    "timeout",
                    f"scan {scan_id} not terminal after {waited:.1f}s "
                    f"({attempts} polls)",
                )
            sleep(delay)
            waited += delay
            delay = min(delay * 2, 8.0)

    # -- webhook -----------------------------------------------------------
    def verify_webhook(
        self, body: Mapping[str, Any], signature_header: str
    ) -> bool:
        """Verify a delivered completion webhook (reuses WS-4 HMAC)."""
        if self._webhook_secret is None:
            # No shared secret configured → cannot verify → fail closed.
            return False
        return verify_signature(self._webhook_secret, body, signature_header)


class UrllibTransport:
    """Real ``urllib`` transport for the container runtime.

    Maps network/HTTP failures to :class:`ScanTransportError` kinds. Never
    logs URLs or headers (they may carry bearer secrets).
    """

    def __init__(self, timeout_s: float = 30.0) -> None:
        self._timeout = timeout_s

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None = None,
    ) -> Response:
        req = urllib.request.Request(
            url, data=body, headers=dict(headers), method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                return Response(
                    status=r.status,
                    body=r.read(),
                    headers={k.lower(): v for k, v in r.headers.items()},
                )
        except urllib.error.HTTPError as exc:  # 4xx/5xx
            return Response(
                status=exc.code,
                body=exc.read() if hasattr(exc, "read") else b"",
                headers={k.lower(): v for k, v in (exc.headers or {}).items()},
            )
        except TimeoutError as exc:
            raise ScanTransportError("timeout", "request timed out") from exc
        except urllib.error.URLError as exc:
            raise ScanTransportError("unreachable", "endpoint unreachable") from exc


__all__ = [
    "BAND_FLOOR",
    "Response",
    "ScanTransportError",
    "Transport",
    "ScanClient",
    "UrllibTransport",
]
