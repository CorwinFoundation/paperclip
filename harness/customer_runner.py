"""BEAAA-5414 — Customer-agent HTTP runner factory for the Track B walker.

Provides :func:`customer_agent_http_runner_factory` — the
``runner_factory`` injected into
:func:`scanners.live_probe_orchestrator.run_scan` when
``ARE_WALKER_CUSTOMER_AGENT=1`` on the
:func:`api.scan_runtime_factory.make_default_production_scan_runtime`
path. Replaces :func:`api.scan_runtime_factory.v0_mock_runner_factory`
on that path; Tier-1 rollback (``ARE_WALKER_CUSTOMER_AGENT=0`` default)
keeps the MockRunner factory active byte-for-byte.

Tamper-resistance posture
-------------------------
* The runner is constructed INSIDE the Prober subprocess's
  ``ScopedEgressAllowlist(agent_endpoint)`` scope by the
  orchestrator's ``runner_factory(agent_endpoint, secret)`` call.
* Every HTTP request issued from :meth:`_CustomerAgentHttpRunner.complete`
  is routed through an endpoint-scoped ``httpx`` transport — the runner
  builds its own :class:`harness.egress.ScopedEgressAllowlist` bound
  to the endpoint URL and uses :meth:`ScopedEgressAllowlist.client`
  so the allowlist's ``_AllowlistTransport`` intercepts both the
  initial request AND every redirect re-issue. A redirect ``Location``
  pointing at a non-allow-listed host (``127.0.0.1``, cloud-metadata
  IPs, arbitrary external hosts) is refused at the transport layer
  before any bytes leave the process. (BEAAA-5485 — the prior
  implementation used ``urllib.request.urlopen``, which the allowlist
  did not intercept; a 302 to ``127.0.0.1`` was followed verbatim.)
* The secret is bound into the runner's ``Authorization`` header at
  construction time and stored ONLY on the runner instance (in the
  Prober subprocess's memory). The runner does not log, persist, or
  re-emit the secret. The walker downstream of this runner never
  reads the secret back.
* Vault is fork-inherited from the API-edge parent into the Prober;
  no credential ever crosses the IPC into the Scorer subprocess.

The internal ``ScopedEgressAllowlist`` nests cleanly under the
orchestrator's outer scope: both scopes match the same declared endpoint,
so the inner scope is redundant when the outer scope is correctly opened
and additive defense-in-depth when it is not.
"""
from __future__ import annotations

import json as _json
import os
import random
from typing import Any
from urllib.parse import urlparse

from harness.egress import EgressNotAllowed, ScopedEgressAllowlist
from harness.runner import ModelRunner, RunnerError, SyllableRunner


_MAX_RESPONSE_BYTES: int = 256 * 1024
"""Adversarial-input bound: max bytes read from the customer endpoint
per probe call. 256 KiB is the conservative ceiling for a single agent
response; any payload above this size is truncated at read time so an
adversarial endpoint can't exhaust the Prober's memory."""


_REQUEST_TIMEOUT_SECONDS: float = 30.0
"""Per-request HTTP timeout. The walker iterates probes serially; the
orchestrator's ``MAX_WALL_CLOCK_SECONDS`` cap wraps the entire
iteration. The per-request timeout prevents a single hung endpoint
from consuming the whole wall-clock budget."""


def _extract_host(agent_endpoint: str) -> str:
    """Parse the host component out of ``agent_endpoint`` for the allow-list.

    Returns the lowercased hostname. Raises :class:`RunnerError` if
    the URL has no host or a non-http(s) scheme — both indicate a
    wiring bug from the API edge (the request validator should have
    rejected the input before the orchestrator constructed the
    runner).
    """
    parsed = urlparse(agent_endpoint)
    if parsed.scheme not in ("http", "https"):
        raise RunnerError(
            f"customer endpoint scheme must be http/https; got {parsed.scheme!r}"
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise RunnerError(
            f"customer endpoint URL has no host component: {agent_endpoint!r}"
        )
    return host


class _CustomerAgentHttpRunner(ModelRunner):
    """HTTP runner that POSTs prompts to the customer's agent endpoint.

    Wire shape: ``POST <agent_endpoint>`` with
    ``Content-Type: application/json`` and
    ``Authorization: Bearer <secret>``; request body
    ``{"prompt": <str>}``; response body parsed as JSON; the field
    ``response`` (string) is returned. If the response is not valid
    JSON or lacks a ``response`` field, the raw response text is
    returned.

    The runner is bound to ONE agent endpoint + ONE credential for its
    lifetime. The orchestrator constructs a fresh runner per scan.
    """

    def __init__(self, agent_endpoint: str, secret: str):
        self._agent_endpoint = agent_endpoint
        self._auth_header = f"Bearer {secret}"
        self._allowed_host = _extract_host(agent_endpoint)
        # Derive a stable model_id from the endpoint host; the secret
        # is never embedded in model_id (would leak into eval logs).
        self._model_id = f"customer-agent:{self._allowed_host}"

    def model_id(self) -> str:
        return self._model_id

    def complete(self, prompt: str) -> str:
        """Issue a single probe request to the customer endpoint.

        Returns the agent's response string. Raises :class:`RunnerError`
        on transport failure (timeout, non-2xx, oversized response, or
        an attempted redirect/request to a host outside the active
        allow-list — see BEAAA-5485).
        """
        import httpx  # type: ignore  # noqa: F401 (egress imports httpx)

        body_bytes = _json.dumps({"prompt": prompt}).encode("utf-8")
        # BEAAA-5532: pin ``Accept-Encoding: identity`` so a well-behaved
        # customer endpoint returns uncompressed bytes. httpx's default
        # is ``gzip, deflate, br, zstd``; under that default a small
        # compressed wire payload can inflate to multi-GB into the
        # Prober's memory through ``iter_bytes`` (the decoded-content
        # iterator) before the size cap fires on the inflated bytes.
        # The Content-Encoding response-gate below is defence-in-depth
        # against a non-compliant endpoint that ignores this header.
        headers = {
            "Content-Type": "application/json",
            "Authorization": self._auth_header,
            "Accept-Encoding": "identity",
        }
        # BEAAA-5528: stream the response and early-abort the moment
        # accumulated length exceeds ``_MAX_RESPONSE_BYTES``. The prior
        # ``client.post`` path fully buffered the body before the cap
        # check fired, so a misbehaving endpoint streaming multi-GB
        # chunked body would OOM the Prober before the cap evaluated.
        # ``client.stream`` returns headers immediately; ``iter_raw``
        # surfaces raw on-the-wire chunks as they arrive, and a
        # ``break`` on cap closes the connection at the next context
        # exit. BEAAA-5532: ``iter_raw`` (not ``iter_bytes``) is
        # mandatory — ``iter_bytes`` decodes any Content-Encoding the
        # response carries, which would re-open the compression-bomb
        # hazard the Content-Encoding gate below is defending against.
        try:
            with ScopedEgressAllowlist(self._agent_endpoint) as scope:
                # BEAAA-5552: redirects rejected outright. Customer
                # endpoint is registered as a single URL; auto-redirect
                # handling in httpx drains intermediate 30x bodies via
                # ``Response.read()`` (the decoded-content path), which
                # would bypass the BEAAA-5532 compression-bomb gates
                # below — those gates apply only to the FINAL response
                # shape. Reject 3xx; do not implement a manual redirect
                # loop without a co-sign ticket (UW + Founder for the
                # expanded surface). BEAAA-5485 SSRF host check still
                # holds: with no auto-redirect there is no off-allow-
                # list re-issue to validate; the host check fires on
                # the initial (and only) request through
                # ``_AllowlistTransport``.
                with scope.client(
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                    follow_redirects=False,
                ) as client:
                    with client.stream(
                        "POST",
                        self._agent_endpoint,
                        content=body_bytes,
                        headers=headers,
                    ) as resp:
                        # BEAAA-5552: 3xx rejection. Fire BEFORE the
                        # >=400 check so a redirect status surfaces as
                        # a redirect-rejection error (not a generic
                        # HTTP error). The check runs on header
                        # receipt; the response body generator is
                        # never iterated, and the next context exit
                        # closes the underlying connection without
                        # draining the body.
                        if 300 <= resp.status_code < 400:
                            raise RunnerError(
                                f"customer endpoint returned redirect: "
                                f"{resp.status_code} {resp.reason_phrase}"
                            )
                        if resp.status_code >= 400:
                            # Read reason_phrase before exiting the
                            # stream context (response object is still
                            # usable for headers/status after exit, but
                            # surface the error eagerly so the body
                            # generator is closed without consuming
                            # potentially-oversized chunks).
                            raise RunnerError(
                                f"customer endpoint HTTP {resp.status_code}: "
                                f"{resp.reason_phrase}"
                            )
                        # BEAAA-5532: Content-Encoding gate. Reject any
                        # response that carries a non-identity
                        # Content-Encoding BEFORE iterating the body so
                        # a compression bomb is never decoded into the
                        # Prober's memory. Allowed values: missing
                        # header, empty string, or ``identity``. This
                        # gate fires regardless of what
                        # ``Accept-Encoding`` the request advertised —
                        # defence-in-depth against a customer endpoint
                        # that does not honour ``Accept-Encoding:
                        # identity``.
                        encoding = (
                            resp.headers.get("Content-Encoding", "")
                            .strip()
                            .lower()
                        )
                        if encoding not in ("", "identity"):
                            raise RunnerError(
                                f"customer endpoint returned unsupported "
                                f"Content-Encoding: {encoding}"
                            )
                        buf = bytearray()
                        truncated = False
                        # BEAAA-5532: pre-extension cap check. The
                        # check fires BEFORE ``buf.extend(chunk)`` so
                        # the buffer never grows past the cap even by
                        # one chunk-worth of slack. The BEAAA-5528
                        # post-extension check allowed peak buffer to
                        # reach ``cap + len(chunk)`` before the break;
                        # under adversarial chunk sizing that's an
                        # extra-large memory residue. Pre-extension
                        # check guarantees ``len(buf) <= cap`` always.
                        for chunk in resp.iter_raw():
                            if len(buf) + len(chunk) > _MAX_RESPONSE_BYTES:
                                truncated = True
                                break
                            buf.extend(chunk)
                        if truncated:
                            raise RunnerError(
                                f"customer endpoint response exceeded "
                                f"{_MAX_RESPONSE_BYTES} bytes"
                            )
                        raw = bytes(buf)
        except EgressNotAllowed as exc:
            # SSRF guard tripped — either the initial host slipped past
            # the API-edge validator, or a redirect target tried to
            # pivot off the declared host. Surface as RunnerError so the
            # probe attempt fails cleanly without leaking the loopback /
            # internal target back to the walker.
            raise RunnerError(
                f"customer endpoint egress refused by scoped allow-list: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise RunnerError(
                f"customer endpoint timeout after {_REQUEST_TIMEOUT_SECONDS}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise RunnerError(
                f"customer endpoint transport error: {exc}"
            ) from exc
        text = raw.decode("utf-8", errors="replace")
        try:
            parsed: Any = _json.loads(text)
        except _json.JSONDecodeError:
            return text
        if isinstance(parsed, dict) and isinstance(parsed.get("response"), str):
            return parsed["response"]
        return text


class _SyllableCustomerAgentRunner(SyllableRunner):
    """Hardened customer-path runner for Syllable apichat.

    Syllable's public apichat surface requires public Syllable headers and a
    two-step session protocol. This runner preserves the customer endpoint
    hardening in :class:`_CustomerAgentHttpRunner`: scoped egress, redirect
    rejection, raw streaming response caps, and Content-Encoding refusal.
    """

    TRANSIENT_STATUS_CODES = frozenset({502, 503, 522})
    MAX_TRANSIENT_ATTEMPTS = 3
    RETRY_BACKOFF_BASE_SECONDS = 1.0
    RETRY_JITTER_MIN = 0.5
    RETRY_JITTER_MAX = 1.5
    DEFAULT_TIMEOUT_SECONDS = 180.0

    def __init__(
        self,
        *,
        base_url: str,
        client_id_key: str,
        host_key: str,
        origin: str = "https://webchat.syllable.cloud",
        label: str = "syllable:apichat",
        timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
        http_attempt_observer: Callable[[dict[str, Any]], None] | None = None,
        jitter_fn: Callable[[float, float], float] = random.uniform,
    ) -> None:
        super().__init__(
            client_id_key=client_id_key,
            host_key=host_key,
            base_url=base_url,
            origin=origin,
            label=label,
            timeout_s=timeout_s,
        )
        self._base_url = base_url.rstrip("/")
        self._http_attempt_observer = http_attempt_observer
        self._jitter_fn = jitter_fn

    def _retry_delay(self, completed_attempt: int) -> float:
        """Return exponential backoff with bounded multiplicative jitter."""
        base = self.RETRY_BACKOFF_BASE_SECONDS * (2**completed_attempt)
        return base * self._jitter_fn(
            self.RETRY_JITTER_MIN,
            self.RETRY_JITTER_MAX,
        )

    def _observe_http_attempt(
        self, *, phase: str, attempt: int, timeout_s: float
    ) -> None:
        if self._http_attempt_observer is not None:
            self._http_attempt_observer(
                {
                    "phase": phase,
                    "attempt": attempt + 1,
                    "timeout_s": timeout_s,
                }
            )

    def _headers(self) -> dict[str, str]:
        return {**super()._headers(), "Accept-Encoding": "identity"}

    def _syllable_post(
        self,
        path: str,
        *,
        phase: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        egress_session_id: str,
    ) -> Any:
        import httpx  # type: ignore

        url = f"{self._base_url}{path}"
        body_bytes = _json.dumps(payload).encode("utf-8")
        last_exc: Exception | None = None

        for attempt in range(self.MAX_TRANSIENT_ATTEMPTS):
            try:
                timeout_s = self._request_timeout_for_deadline()
                self._observe_http_attempt(
                    phase=phase,
                    attempt=attempt,
                    timeout_s=timeout_s,
                )
                with ScopedEgressAllowlist(
                    self._base_url,
                    live_bind_scope_id=egress_session_id,
                ) as scope:
                    with scope.client(
                        timeout=timeout_s,
                        follow_redirects=False,
                        session_id=egress_session_id,
                    ) as client:
                        with client.stream(
                            "POST",
                            url,
                            content=body_bytes,
                            headers=headers,
                        ) as resp:
                            if 300 <= resp.status_code < 400:
                                raise RunnerError(
                                    f"syllable {phase} returned redirect: "
                                    f"{resp.status_code} {resp.reason_phrase}"
                                )
                            if (
                                resp.status_code in self.TRANSIENT_STATUS_CODES
                                and attempt < self.MAX_TRANSIENT_ATTEMPTS - 1
                            ):
                                self._sleep_with_deadline(self._retry_delay(attempt))
                                continue
                            if resp.status_code >= 400:
                                raise RunnerError(
                                    f"syllable {phase} HTTP {resp.status_code}: "
                                    f"{resp.reason_phrase}"
                                )
                            encoding = (
                                resp.headers.get("Content-Encoding", "")
                                .strip()
                                .lower()
                            )
                            if encoding not in ("", "identity"):
                                raise RunnerError(
                                    f"syllable {phase} returned unsupported "
                                    f"Content-Encoding: {encoding}"
                                )
                            buf = bytearray()
                            for chunk in resp.iter_raw():
                                if len(buf) + len(chunk) > _MAX_RESPONSE_BYTES:
                                    raise RunnerError(
                                        f"syllable {phase} response exceeded "
                                        f"{_MAX_RESPONSE_BYTES} bytes"
                                    )
                                buf.extend(chunk)
                            raw = bytes(buf)
            except RunnerError:
                raise
            except EgressNotAllowed as exc:
                raise RunnerError(
                    f"syllable {phase} egress refused by scoped allow-list: {exc}"
                ) from exc
            except httpx.TimeoutException as exc:
                last_exc = exc
                if (
                    attempt < self.MAX_TRANSIENT_ATTEMPTS - 1
                    and self._is_transient_transport_error(exc)
                ):
                    self._sleep_with_deadline(self._retry_delay(attempt))
                    continue
                raise RunnerError(
                    f"syllable {phase} request failed: {exc}"
                ) from exc
            except httpx.HTTPError as exc:
                last_exc = exc
                if (
                    attempt < self.MAX_TRANSIENT_ATTEMPTS - 1
                    and self._is_transient_transport_error(exc)
                ):
                    self._sleep_with_deadline(self._retry_delay(attempt))
                    continue
                raise RunnerError(
                    f"syllable {phase} request failed: {exc}"
                ) from exc
            except Exception as exc:
                last_exc = exc
                if (
                    attempt < self.MAX_TRANSIENT_ATTEMPTS - 1
                    and self._is_transient_transport_error(exc)
                ):
                    self._sleep_with_deadline(self._retry_delay(attempt))
                    continue
                raise RunnerError(
                    f"syllable {phase} request failed: {exc}"
                ) from exc

            return _BufferedResponse(resp.status_code, raw)

        if last_exc is not None:
            raise RunnerError(f"syllable {phase} request failed: {last_exc}") from last_exc
        raise RunnerError(f"syllable {phase} request failed without response")

class _BufferedResponse:
    """Small response facade consumed by the retained SyllableRunner."""

    def __init__(self, status_code: int, raw: bytes) -> None:
        self.status_code = status_code
        self._raw = raw
        self.text = raw.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return _json.loads(self.text)


def customer_agent_http_runner_factory(
    agent_endpoint: str, secret: str
) -> ModelRunner:
    """Build a :class:`_CustomerAgentHttpRunner` bound to one endpoint + secret.

    Signature matches :class:`Callable[[str, str], ModelRunner]` so the
    factory is drop-in compatible with
    :func:`scanners.live_probe_orchestrator.run_scan`'s
    ``runner_factory`` parameter — same shape as
    :func:`api.scan_runtime_factory.v0_mock_runner_factory`.
    """
    host = _extract_host(agent_endpoint)
    if host == "apichat.syllable.cloud":
        # Syllable's public apichat surface is not a single root POST
        # endpoint. It requires the two-step /api/v1/chats protocol and
        # Syllable-specific public headers. Keep the customer-path HTTP
        # hardening here rather than routing through harness.runner.SyllableRunner.
        return _SyllableCustomerAgentRunner(
            client_id_key=os.environ.get(
                "SYLLABLE_CLIENT_ID_KEY", "hlw3qyTAx5inYzm2"
            ),
            host_key=os.environ.get("SYLLABLE_HOST_KEY", "https://syllable.ai"),
            base_url=agent_endpoint,
            label="syllable:apichat",
        )
    return _CustomerAgentHttpRunner(agent_endpoint, secret)


__all__ = [
    "customer_agent_http_runner_factory",
]
