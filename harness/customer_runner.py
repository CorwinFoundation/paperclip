"""BEAAA-5414 — Customer-agent HTTP runner factory for the Track B walker.

Provides :func:`customer_agent_http_runner_factory` — the
``runner_factory`` injected into
:func:`scanners.live_probe_orchestrator.run_scan` when
``ARE_WALKER_CUSTOMER_AGENT=1`` on the
:func:`api.scan_runtime_factory.make_default_production_scan_runtime`
path. Replaces :func:`api.scan_runtime_factory.v0_mock_runner_factory`
on that path; Tier-1 rollback (``ARE_WALKER_CUSTOMER_AGENT=0`` default)
keeps the MockRunner factory active byte-for-byte.

Tamper-resistance posture (preserved)
-------------------------------------
* The runner is constructed INSIDE the Prober subprocess's
  ``ScopedEgressAllowlist(agent_endpoint_host)`` scope by the
  orchestrator's ``runner_factory(agent_endpoint, secret)`` call. Every
  HTTP request issued from :meth:`_CustomerAgentHttpRunner.complete` is
  bound to the customer endpoint host at the transport layer; any
  attempted egress to a different host raises at the egress scope.
* The secret is bound into the runner's ``Authorization`` header at
  construction time and stored ONLY on the runner instance (in the
  Prober subprocess's memory). The runner does not log, persist, or
  re-emit the secret. The walker downstream of this runner never
  reads the secret back.
* Vault is fork-inherited from the API-edge parent into the Prober;
  no credential ever crosses the IPC into the Scorer subprocess.
"""
from __future__ import annotations

import json as _json
import urllib.error
import urllib.request
from typing import Any

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
        # Derive a stable model_id from the endpoint host; the secret
        # is never embedded in model_id (would leak into eval logs).
        host = agent_endpoint.replace("https://", "").replace("http://", "")
        host = host.split("/", 1)[0]
        self._model_id = f"customer-agent:{host}"

    def model_id(self) -> str:
        return self._model_id

    def complete(self, prompt: str) -> str:
        """Issue a single probe request to the customer endpoint.

        Returns the agent's response string. Raises :class:`RunnerError`
        on transport failure (timeout, non-2xx, oversized response).
        The egress scope (Prober's ``ScopedEgressAllowlist``) denies
        any request to a non-allow-listed host before this method's
        socket call returns.
        """
        body = _json.dumps({"prompt": prompt}).encode("utf-8")
        req = urllib.request.Request(
            self._agent_endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": self._auth_header,
            },
        )
        try:
            with urllib.request.urlopen(
                req, timeout=_REQUEST_TIMEOUT_SECONDS
            ) as resp:
                raw = resp.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise RunnerError(
                f"customer endpoint HTTP {exc.code}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RunnerError(
                f"customer endpoint transport error: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise RunnerError(
                f"customer endpoint timeout after {_REQUEST_TIMEOUT_SECONDS}s"
            ) from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise RunnerError(
                f"customer endpoint response exceeded "
                f"{_MAX_RESPONSE_BYTES} bytes"
            )
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
