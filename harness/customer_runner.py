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
import gzip
import os
import re
from typing import Any
from urllib.parse import urlparse

from harness.egress import EgressNotAllowed, ScopedEgressAllowlist
from harness.runner import ModelRunner, RunnerError, SyllableRunner


_MAX_RESPONSE_BYTES: int = 256 * 1024
_MAX_RESULT_TEXT_CHARS: int = 4_000
_RESULT_TEXT_TRUNCATION_MARKER: str = "\n[TRUNCATED]"
"""Adversarial-input bound: max bytes read from the customer endpoint
per probe call. 256 KiB is the conservative ceiling for a single agent
response; any payload above this size is truncated at read time so an
adversarial endpoint can't exhaust the Prober's memory."""


_REQUEST_TIMEOUT_SECONDS: float = 30.0
"""Per-request HTTP timeout. The walker iterates probes serially; the
orchestrator's ``MAX_WALL_CLOCK_SECONDS`` cap wraps the entire
iteration. The per-request timeout prevents a single hung endpoint
from consuming the whole wall-clock budget."""

# BEAAA-38865: characters h11 refuses in a header value. h11's refusal message
# quotes the whole value (``Illegal header value b'Bearer <secret>'``), and
# that text used to reach the RunnerError message, httpcore's debug trace and
# the full-tier per-case evidence file. The runner refuses such a credential
# itself, before any request is built.
_ILLEGAL_HEADER_VALUE_CHARS: frozenset[str] = frozenset("\x00\n\r\x0b\x0c")
_INVALID_CREDENTIAL_MESSAGE: str = (
    "customer endpoint transport error: credential is not a valid HTTP header value"
)


def _is_sendable_header_value(value: str) -> bool:
    """Mirror h11's field-value check without ever quoting the value."""
    return value == value.strip(" \t") and not (
        _ILLEGAL_HEADER_VALUE_CHARS.intersection(value)
    )


_VAPI_HOST: str = "api.vapi.ai"
_VAPI_CHAT_ENDPOINT: str = "https://api.vapi.ai/chat"
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


class CustomerEndpointHTTPError(RunnerError):
    """Non-2xx customer response with bounded diagnostics for scan evidence."""

    def __init__(self, status_code: int, reason_phrase: str, body: str, headers: dict[str, str]):
        super().__init__(f"customer endpoint HTTP {status_code}: {reason_phrase}")
        self.status_code = status_code
        self.reason_phrase = reason_phrase
        self.body = body
        self.headers = headers


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

    def __init__(self, agent_endpoint: str, secret: str, endpoint_config: dict[str, Any] | None = None):
        self._agent_endpoint = agent_endpoint
        self._config = endpoint_config or {}
        auth = self._config.get("auth") or {"type": "bearer"}
        auth_type = auth.get("type", "bearer")
        self._auth_headers: dict[str, str] = {}
        if auth_type == "bearer":
            self._auth_headers["Authorization"] = f"Bearer {secret}"
        elif auth_type == "custom_header":
            self._auth_headers[str(auth["header_name"])] = secret
        self._allowed_host = _extract_host(agent_endpoint)
        # Derive a stable model_id from the endpoint host; the secret
        # is never embedded in model_id (would leak into eval logs).
        self._model_id = f"customer-agent:{self._allowed_host}"
        self._result_rows_enabled = bool(self._config.get("_include_result_rows"))
        self._redaction_values = tuple(
            value
            for value in (secret, self._config.get("system_prompt"))
            if isinstance(value, str) and value
        )
        self._pending_exchange: tuple[str, str] | None = None
        self._starter_rows: list[dict[str, str]] = []
        self._auth_headers_sendable = all(
            _is_sendable_header_value(value) for value in self._auth_headers.values()
        )
        if self._config.get("transport") == "kaltura_genie" and auth_type == "bearer":
            # Kaltura Session (KS) auth deliberately differs from OAuth bearer
            # auth.  Keep the scoped token in the existing ephemeral secret
            # channel; never place it in endpoint configuration or evidence.
            self._auth_headers["Authorization"] = f"KS {secret}"
            self._auth_headers_sendable = _is_sendable_header_value(
                self._auth_headers["Authorization"]
            )

    def model_id(self) -> str:
        return self._model_id

    def _request_payload(self, prompt: str) -> dict[str, Any]:
        messages_payload = getattr(self, "_messages_payload", None)
        if messages_payload is not None:
            return messages_payload
        transport = self._config.get("transport", "rest")
        if transport == "openai_chat":
            messages = []
            system_prompt = self._config.get("system_prompt")
            if isinstance(system_prompt, str) and system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            return {"model": self._config["model"], "messages": messages}
        if transport == "json_map":
            return _replace_prompt(self._config["request_template"], prompt)
        if transport == "kaltura_genie":
            # Omitting threadId creates a fresh conversation for every probe,
            # preventing one test from contaminating the next.  Do not persist
            # scanner conversations in the customer's Genie history.
            return {
                "userMessage": prompt,
                "sse": False,
                "model_type": "fast",
                "add_message_in_db": False,
            }
        return {"prompt": prompt}

    def complete_messages(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Complete one tool-visible chat turn through the customer endpoint.

        Native framework adapters need the full conversation and declared tools,
        rather than the legacy text-only ``prompt`` request.  The response may be
        either a direct assistant-message envelope or an OpenAI-compatible chat
        completion; both are normalized to the message shape consumed by those
        adapters.
        """
        self._messages_payload = {"messages": messages, "tools": tools}
        try:
            raw = self.complete("")
        finally:
            self._messages_payload = None
        try:
            payload = _json.loads(raw)
        except _json.JSONDecodeError:
            # Text-only customer endpoints remain valid for native adapters
            # that can consume an assistant message without tool calls.
            return {"content": raw}
        if not isinstance(payload, dict):
            raise RunnerError("customer endpoint messages response was not a JSON object")
        choices = payload.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                return message
        return payload

    def _request_body(self, prompt: str) -> tuple[bytes, dict[str, str]]:
        headers = {}
        if self._config.get("transport") == "kaltura_genie":
            headers["Accept"] = "application/x-ndjson"
        return _json.dumps(self._request_payload(prompt)).encode("utf-8"), headers

    def _parse_response(self, text: str) -> str:
        if self._config.get("transport") == "kaltura_genie":
            chunks: list[str] = []
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                try:
                    event = _json.loads(line)
                except _json.JSONDecodeError as exc:
                    raise RunnerError("kaltura genie returned invalid NDJSON") from exc
                if not isinstance(event, dict):
                    raise RunnerError("kaltura genie returned a non-object stream event")
                if event.get("type") == "error" or event.get("event") == "error":
                    raise RunnerError("kaltura genie stream reported an error")
                if event.get("role") == "assistant" and event.get("type") == "text":
                    content = event.get("content")
                    if isinstance(content, str):
                        chunks.append(content)
            answer = "".join(chunks).strip()
            if not answer:
                raise RunnerError("kaltura genie stream contained no assistant text")
            return answer
        try:
            parsed: Any = _json.loads(text)
        except _json.JSONDecodeError:
            return text
        if self._config.get("transport") == "openai_chat":
            try:
                return str(parsed["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError):
                return text
        if self._config.get("transport") == "json_map":
            value: Any = parsed
            for part in str(self._config["response_path"]).split("."):
                if isinstance(value, dict) and part in value:
                    value = value[part]
                elif isinstance(value, list) and part.isdecimal():
                    index = int(part)
                    if index >= len(value):
                        return text
                    value = value[index]
                else:
                    return text
            return value if isinstance(value, str) else text
        if isinstance(parsed, dict) and isinstance(parsed.get("response"), str):
            return parsed["response"]
        return text

    def complete(self, prompt: str) -> str:
        """Issue a single probe request to the customer endpoint.

        Returns the agent's response string. Raises :class:`RunnerError`
        on transport failure (timeout, non-2xx, oversized response, or
        an attempted redirect/request to a host outside the active
        allow-list — see BEAAA-5485).
        """
        import httpx  # type: ignore  # noqa: F401 (egress imports httpx)

        if not self._auth_headers_sendable:
            # BEAAA-38865: same RunnerError prefix as any other transport
            # failure, so API-edge error mapping is unchanged; the value
            # itself never reaches httpx, h11 or httpcore's logger.
            raise RunnerError(_INVALID_CREDENTIAL_MESSAGE)
        body_bytes, transport_headers = self._request_body(prompt)
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
            "User-Agent": "BackBond-Scanner/0.5",
            "Accept-Encoding": "identity",
            **self._auth_headers,
            **transport_headers,
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
                            error_buf = bytearray()
                            for chunk in resp.iter_raw():
                                remaining = _MAX_RESPONSE_BYTES - len(error_buf)
                                if remaining <= 0:
                                    break
                                error_buf.extend(chunk[:remaining])
                            raise CustomerEndpointHTTPError(
                                resp.status_code,
                                resp.reason_phrase,
                                bytes(error_buf).decode("utf-8", errors="replace"),
                                {str(k).lower(): str(v) for k, v in resp.headers.items()},
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
                "customer endpoint egress refused by scoped allow-list: "
                + self._redact_result_text(str(exc))
            ) from exc
        except httpx.TimeoutException as exc:
            raise RunnerError(
                f"customer endpoint timeout after {_REQUEST_TIMEOUT_SECONDS}s"
            ) from exc
        except httpx.HTTPError as exc:
            # BEAAA-38865: exception text is copied into scan errors and
            # full-tier evidence; never let it carry the credential.
            raise RunnerError(
                "customer endpoint transport error: "
                + self._redact_result_text(str(exc))
            ) from exc
        text = raw.decode("utf-8", errors="replace")
        parsed_response = self._parse_response(text)
        if self._result_rows_enabled:
            self._pending_exchange = (
                self._redact_result_text(prompt),
                self._redact_result_text(parsed_response),
            )
        return parsed_response

    def record_starter_verdict(
        self, *, test_id: str, family: str, verdict: str, reason: str = ""
    ) -> None:
        """Attach a scored starter identity to the last prompt/reply pair.

        BEAAA-38895: with ``reason`` (starter-100@2) a verdict-only row is kept
        even when result rows are off, so the Starter score always rides the
        payload. Prompt/reply text still needs ``include_result_rows``.
        """
        pack_id = getattr(self, "pack_id", None)
        variable_pack = pack_id == "starter-100@4"
        metadata = (
            {"pack_id": pack_id}
            if pack_id in {"starter-100@4", "starter-100@5", "starter-voice-50@1"}
            else {}
        )
        if variable_pack:
            metadata["form_id"] = getattr(self, "starter_form_summary", {}).get("form_id", "")
        if not self._result_rows_enabled or self._pending_exchange is None:
            if reason:
                self._starter_rows.append({
                    **metadata,
                    "test_id": test_id,
                    "family": family,
                    "verdict": verdict,
                    "reason": self._redact_result_text(reason),
                })
            self._pending_exchange = None
            return
        prompt, reply = self._pending_exchange
        row = {
            **metadata,
            "test_id": test_id,
            "family": family,
            "prompt": self._cap_result_text(self._redact_result_text(prompt)),
            "reply": self._cap_result_text(self._redact_result_text(reply)),
            "verdict": verdict,
        }
        if reason:
            row["reason"] = self._redact_result_text(reason)
        self._starter_rows.append(row)
        self._pending_exchange = None

    def starter_result_rows(self) -> tuple[dict[str, str], ...]:
        return tuple(dict(row) for row in self._starter_rows)

    def _redact_result_text(self, value: str) -> str:
        redacted = value
        for secret_value in self._redaction_values:
            redacted = redacted.replace(secret_value, "[REDACTED]")
        return redacted

    @staticmethod
    def _cap_result_text(value: str) -> str:
        if len(value) <= _MAX_RESULT_TEXT_CHARS:
            return value
        keep = _MAX_RESULT_TEXT_CHARS - len(_RESULT_TEXT_TRUNCATION_MARKER)
        return value[:keep] + _RESULT_TEXT_TRUNCATION_MARKER


class _VapiCustomerAgentRunner(_CustomerAgentHttpRunner):
    """Hardened Vapi Chat adapter bound to one exact assistant.

    Each completion starts a fresh chat because scanner probes are independent
    test cases and must not inherit state from a prior probe. The response
    assistant identity is checked fail-closed before text reaches the walker.
    """

    def __init__(
        self,
        *,
        secret: str,
        assistant_id: str,
        endpoint_config: dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(assistant_id, str) or not _UUID_RE.fullmatch(assistant_id):
            raise RunnerError("vapi target requires agent_id to be an assistant UUID")
        self._assistant_id = assistant_id.lower()
        super().__init__(_VAPI_CHAT_ENDPOINT, secret, endpoint_config)
        self._model_id = f"vapi:{self._assistant_id}@{_VAPI_HOST}"

    def _request_payload(self, prompt: str) -> dict[str, Any]:
        return {
            "assistantId": self._assistant_id,
            "input": prompt,
            "stream": False,
        }

    def _request_body(self, prompt: str) -> tuple[bytes, dict[str, str]]:
        """Compress Vapi JSON so edge XSS inspection cannot consume the probe.

        Vapi decompresses the HTTP entity before JSON parsing, so the assistant
        receives the exact markup-bearing prompt. ``mtime=0`` keeps identical
        probe requests byte-reproducible.
        """
        raw = _json.dumps(self._request_payload(prompt)).encode("utf-8")
        return gzip.compress(raw, mtime=0), {"Content-Encoding": "gzip"}

    def _parse_response(self, text: str) -> str:
        try:
            parsed: Any = _json.loads(text)
        except _json.JSONDecodeError as exc:
            raise RunnerError("vapi chat returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise RunnerError("vapi chat returned a non-object response")

        response_assistant_id = parsed.get("assistantId")
        if not isinstance(response_assistant_id, str):
            raise RunnerError("vapi chat response omitted assistantId")
        if response_assistant_id.lower() != self._assistant_id:
            raise RunnerError("vapi chat response assistantId mismatch")

        output = parsed.get("output")
        if not isinstance(output, list):
            raise RunnerError("vapi chat response omitted output")
        parts: list[str] = []
        for item in output:
            if isinstance(item, str):
                if item.strip():
                    parts.append(item.strip())
                continue
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role not in (None, "assistant") or not isinstance(content, str):
                continue
            if content.strip():
                parts.append(content.strip())
        if not parts:
            raise RunnerError("vapi chat response contained no assistant output")
        return " ".join(parts)


class _SyllableCustomerAgentRunner(SyllableRunner):
    """Hardened customer-path runner for Syllable apichat.

    Syllable's public apichat surface requires public Syllable headers and a
    two-step session protocol. This runner preserves the customer endpoint
    hardening in :class:`_CustomerAgentHttpRunner`: scoped egress, redirect
    rejection, raw streaming response caps, and Content-Encoding refusal.
    """

    TRANSIENT_STATUS_CODES = frozenset({502, 503, 522})
    MAX_TRANSIENT_ATTEMPTS = 3
    RETRY_BACKOFF_SECONDS = (0.5, 1.0)

    def __init__(
        self,
        *,
        base_url: str,
        client_id_key: str,
        host_key: str,
        origin: str = "https://webchat.syllable.cloud",
        label: str = "syllable:apichat",
        timeout_s: float = _REQUEST_TIMEOUT_SECONDS,
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
                with ScopedEgressAllowlist(
                    self._base_url,
                    live_bind_scope_id=egress_session_id,
                ) as scope:
                    with scope.client(
                        timeout=self._request_timeout_for_deadline(),
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
                                self._sleep_with_deadline(
                                    self.RETRY_BACKOFF_SECONDS[attempt]
                                )
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
                    self._sleep_with_deadline(self.RETRY_BACKOFF_SECONDS[attempt])
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
                    self._sleep_with_deadline(self.RETRY_BACKOFF_SECONDS[attempt])
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
                    self._sleep_with_deadline(self.RETRY_BACKOFF_SECONDS[attempt])
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
    agent_endpoint: str, secret: str, agent_id: str | None = None,
    endpoint_config: dict[str, Any] | None = None,
) -> ModelRunner:
    """Build a :class:`_CustomerAgentHttpRunner` bound to one endpoint + secret.

    Signature matches :class:`Callable[[str, str, str | None], ModelRunner]` so the
    factory is drop-in compatible with
    :func:`scanners.live_probe_orchestrator.run_scan`'s
    ``runner_factory`` parameter — same shape as
    :func:`api.scan_runtime_factory.v0_mock_runner_factory`.
    """
    host = _extract_host(agent_endpoint)
    if (endpoint_config or {}).get("transport") == "kaltura_genie_browser":
        from harness.kaltura_genie_browser import KalturaGenieBrowserRunner
        return KalturaGenieBrowserRunner(
            agent_endpoint,
            endpoint_config=endpoint_config,
        )
    if host == _VAPI_HOST:
        return _VapiCustomerAgentRunner(
            secret=secret,
            assistant_id=agent_id or "",
            endpoint_config=endpoint_config,
        )
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
    return _CustomerAgentHttpRunner(agent_endpoint, secret, endpoint_config)


def _replace_prompt(value: Any, prompt: str) -> Any:
    """Return a copy of a JSON template with exact ``{{prompt}}`` leaves replaced."""
    if isinstance(value, dict):
        return {key: _replace_prompt(item, prompt) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_prompt(item, prompt) for item in value]
    return prompt if value == "{{prompt}}" else value


__all__ = [
    "customer_agent_http_runner_factory",
]
