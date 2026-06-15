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
  ``ScopedEgressAllowlist(agent_endpoint_host)`` scope by the
  orchestrator's ``runner_factory(agent_endpoint, secret)`` call.
* Every HTTP request issued from :meth:`_CustomerAgentHttpRunner.complete`
  is routed through a host-scoped ``httpx`` transport — the runner
  builds its own :class:`harness.egress.ScopedEgressAllowlist` bound
  to the endpoint host and uses :meth:`ScopedEgressAllowlist.client`
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
orchestrator's outer scope: both scopes match the same declared host
(the customer's endpoint), so the inner scope is redundant when the
outer scope is correctly opened and additive defense-in-depth when it
is not.
"""
from __future__ import annotations

import json as _json
from typing import Any
from urllib.parse import urlparse

from harness.egress import EgressNotAllowed, ScopedEgressAllowlist
from harness.runner import ModelRunner, RunnerError


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
        headers = {
            "Content-Type": "application/json",
            "Authorization": self._auth_header,
        }
        # BEAAA-5528: stream the response and early-abort the moment
        # accumulated length exceeds ``_MAX_RESPONSE_BYTES``. The prior
        # ``client.post`` path fully buffered the body before the cap
        # check fired, so a misbehaving endpoint streaming multi-GB
        # chunked body would OOM the Prober before the cap evaluated.
        # ``client.stream`` returns headers immediately; ``iter_bytes``
        # surfaces chunks as they arrive over the wire, and a ``break``
        # on cap closes the connection at the next context exit.
        try:
            with ScopedEgressAllowlist(self._allowed_host) as scope:
                # ``follow_redirects=True`` is REQUIRED. httpx routes
                # each redirect re-issue back through the same
                # transport, so the allowlist's host check fires on
                # every hop. With redirects disabled, a 30x response
                # would surface as a non-2xx error without the
                # SSRF guard ever evaluating the Location target — the
                # transport-layer protection only protects requests
                # the transport actually sees. ``client.stream`` routes
                # redirects through the same transport as ``client.post``
                # did, so the SSRF guard remains intact under streaming.
                with scope.client(
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                    follow_redirects=True,
                ) as client:
                    with client.stream(
                        "POST",
                        self._agent_endpoint,
                        content=body_bytes,
                        headers=headers,
                    ) as resp:
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
                        buf = bytearray()
                        truncated = False
                        for chunk in resp.iter_bytes():
                            buf.extend(chunk)
                            if len(buf) > _MAX_RESPONSE_BYTES:
                                truncated = True
                                break
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
    return _CustomerAgentHttpRunner(agent_endpoint, secret)


__all__ = [
    "customer_agent_http_runner_factory",
]
