"""ModelRunner abstraction — pluggable model-API adapter for the eval harness.

`MockRunner` is the only adapter exercised by the test suite (deterministic,
no network). `AnthropicRunner` and `OpenAIRunner` are thin HTTP shims —
flip from mock to real by setting the appropriate API key env var and
selecting the runner on the CLI.

Each adapter must implement `complete(prompt) -> str` and `model_id()`.
"""
from __future__ import annotations

import json as _json
import os
import secrets
import time
from abc import ABC, abstractmethod
from typing import Any, Callable


class RunnerError(RuntimeError):
    pass


class ModelRunner(ABC):
    @abstractmethod
    def model_id(self) -> str: ...

    @abstractmethod
    def complete(self, prompt: str) -> str: ...


class MockRunner(ModelRunner):
    """Deterministic runner backed by a callable. Used in tests + POC demo."""

    def __init__(self, name: str, responder: Callable[[str], str]):
        self._name = name
        self._responder = responder

    def model_id(self) -> str:
        return f"mock:{self._name}"

    def complete(self, prompt: str) -> str:
        return self._responder(prompt)


class AnthropicRunner(ModelRunner):
    """Real Anthropic adapter. Lazy-imports the SDK; raises on missing key.

    Temperature pinned to 0 by default for reproducible eval runs — override
    with `temperature=...` if you want sampled output.
    """

    def __init__(self, model: str, *, max_tokens: int = 256, temperature: float = 0.0,
                 system_prompt: str | None = None):
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = float(temperature)
        self._system_prompt = system_prompt
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RunnerError("ANTHROPIC_API_KEY not set")

    def model_id(self) -> str:
        return f"anthropic:{self._model}"

    def complete(self, prompt: str) -> str:
        try:
            import anthropic  # type: ignore
        except ImportError as exc:
            raise RunnerError("anthropic SDK not installed; pip install anthropic") from exc
        client = anthropic.Anthropic()
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self._system_prompt:
            kwargs["system"] = self._system_prompt
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
        try:
            msg = client.messages.create(**kwargs)
        except anthropic.BadRequestError as exc:
            # Newer reasoning-class models (e.g. claude-opus-4-7) reject the
            # temperature kwarg outright. Retry without it.
            if "temperature" in str(exc).lower() and "temperature" in kwargs:
                kwargs.pop("temperature")
                msg = client.messages.create(**kwargs)
            else:
                raise
        return "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")


class OpenAIRunner(ModelRunner):
    """Real OpenAI adapter. Lazy-imports the SDK; raises on missing key.

    Temperature pinned to 0 + seed=42 by default for best-effort
    reproducibility. OpenAI documents seed as "best effort, not guaranteed"
    due to MoE routing across server replicas — expect ~1 of 10 items to
    drift between consecutive runs at this fixture size.
    """

    def __init__(
        self,
        model: str,
        *,
        max_tokens: int = 256,
        temperature: float = 0.0,
        seed: int | None = 42,
        system_prompt: str | None = None,
    ):
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = float(temperature)
        self._seed = seed
        self._system_prompt = system_prompt
        if not os.environ.get("OPENAI_API_KEY"):
            raise RunnerError("OPENAI_API_KEY not set")

    def model_id(self) -> str:
        return f"openai:{self._model}"

    def complete(self, prompt: str) -> str:
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:
            raise RunnerError("openai SDK not installed; pip install openai") from exc
        client = OpenAI()
        messages: list[dict[str, str]] = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.append({"role": "user", "content": prompt})
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "messages": messages,
        }
        if self._seed is not None:
            kwargs["seed"] = self._seed
        resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""


class GeminiRunner(ModelRunner):
    """Real Google Gemini adapter. Lazy-imports the SDK; raises on missing key.

    Uses the new google-genai SDK (`from google import genai`). Reads the
    key from GEMINI_API_KEY (preferred) or GOOGLE_API_KEY.

    Free tier limit on `gemini-2.5-flash` is 5 RPM. Enforces a minimum
    interval between calls (default 13s = 4.6 RPM) so a 15-item fixture
    completes without 429s. Override with `min_interval_s=0` on paid tier.
    """

    def __init__(
        self,
        model: str,
        *,
        max_output_tokens: int = 1024,
        min_interval_s: float = 13.0,
        disable_thinking: bool = True,
        temperature: float = 0.0,
        seed: int | None = 42,
        system_prompt: str | None = None,
    ):
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._min_interval_s = float(min_interval_s)
        self._disable_thinking = disable_thinking
        self._temperature = float(temperature)
        self._seed = seed
        self._system_prompt = system_prompt
        # Init to "now" so the first call also waits — protects against
        # quota-window bleed from a prior 429'd run.
        self._last_call_at: float = time.monotonic()
        self._api_key = (
            os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        )
        if not self._api_key:
            raise RunnerError("GEMINI_API_KEY (or GOOGLE_API_KEY) not set")

    def model_id(self) -> str:
        return f"gemini:{self._model}"

    def _throttle(self) -> None:
        if self._min_interval_s <= 0:
            return
        elapsed = time.monotonic() - self._last_call_at
        wait = self._min_interval_s - elapsed
        if wait > 0:
            time.sleep(wait)

    def complete(self, prompt: str) -> str:
        try:
            from google import genai  # type: ignore
            from google.genai import errors as genai_errors  # type: ignore
            from google.genai import types  # type: ignore
        except ImportError as exc:
            raise RunnerError(
                "google-genai SDK not installed; pip install google-genai"
            ) from exc
        client = genai.Client(api_key=self._api_key)

        def _build_config(disable_thinking: bool, max_tokens: int) -> Any:
            kwargs: dict[str, Any] = {
                "max_output_tokens": max_tokens,
                "temperature": self._temperature,
            }
            if self._seed is not None:
                kwargs["seed"] = self._seed
            if self._system_prompt:
                kwargs["system_instruction"] = self._system_prompt
            if disable_thinking:
                try:
                    kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
                except (AttributeError, TypeError):
                    pass  # older SDK — ignore
            return types.GenerateContentConfig(**kwargs)

        # Some 2.5-series models (e.g. gemini-2.5-pro) REQUIRE thinking and
        # reject thinking_budget=0 with a 400. If that happens, fall through
        # to thinking-on with a much larger output budget so the letter has
        # room after the reasoning step.
        attempt_plan = [
            (self._disable_thinking, self._max_output_tokens),
            (False, max(self._max_output_tokens, 4096)),
        ]

        backoff_s = 5.0
        attempts = 4
        last_exc: Exception | None = None
        plan_idx = 0
        for attempt in range(1, attempts + 1):
            self._throttle()
            disable, max_tokens = attempt_plan[plan_idx]
            try:
                resp = client.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config=_build_config(disable, max_tokens),
                )
                self._last_call_at = time.monotonic()
                return resp.text or ""
            except genai_errors.ServerError as exc:
                last_exc = exc
                self._last_call_at = time.monotonic()
                if attempt == attempts:
                    raise
                time.sleep(backoff_s)
                backoff_s *= 2
            except genai_errors.ClientError as exc:
                self._last_call_at = time.monotonic()
                # Specific case: model requires thinking mode and rejected
                # thinking_budget=0. Switch to the thinking-on plan and retry.
                if (
                    plan_idx == 0
                    and len(attempt_plan) > 1
                    and "thinking" in str(exc).lower()
                ):
                    plan_idx = 1
                    continue
                raise
            except Exception:
                self._last_call_at = time.monotonic()
                raise
        # unreachable, but type-checker friendly
        raise last_exc if last_exc else RunnerError("gemini failed without exception")


class OpenRouterRunner(ModelRunner):
    """OpenRouter adapter — proxy for hundreds of frontier + open models.

    Reads OPENROUTER_API_KEY from env. Uses the OpenAI-compat HTTP path
    under the hood. Pass the OpenRouter model id (e.g.
    `meta-llama/llama-3.3-70b-instruct`).
    """

    def __init__(self, model: str, *, max_tokens: int = 256, temperature: float = 0.0,
                 system_prompt: str | None = None):
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RunnerError("OPENROUTER_API_KEY not set")
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._temperature = float(temperature)
        self._system_prompt = system_prompt
        # Lazy build the HTTPEndpointRunner that does the work for the no-system
        # prompt path (preserves the existing request_template behavior).
        self._inner = HTTPEndpointRunner(
            model=model,
            url="https://openrouter.ai/api/v1/chat/completions",
            auth=api_key,
            preset="openai-compat",
            label=f"openrouter:{model}",
        )

    def model_id(self) -> str:
        return f"openrouter:{self._model}"

    def complete(self, prompt: str) -> str:
        if not self._system_prompt:
            return self._inner.complete(prompt)
        # System-prompted path: build the messages array directly so the
        # request_template substitution does not have to escape system text.
        try:
            import httpx  # type: ignore  # noqa: F401 (egress imports httpx)
        except ImportError as exc:
            raise RunnerError("httpx not installed; pip install httpx") from exc
        from harness import egress
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        max_retries = 6
        backoff = 1.0
        resp = None
        for _attempt in range(max_retries):
            resp = egress.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers, json=body, timeout=30.0,
            )
            if resp.status_code != 429:
                break
            retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
            wait_s = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else backoff
            time.sleep(wait_s)
            backoff = min(backoff * 2, 16.0)
        if resp is None:
            raise RunnerError("openrouter request failed without response")
        if resp.status_code >= 400:
            raise RunnerError(f"openrouter HTTP {resp.status_code}: {resp.text[:500]}")
        payload = resp.json()
        try:
            return str(payload["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError) as exc:
            raise RunnerError(
                f"openrouter response shape unexpected: {_json.dumps(payload)[:500]}"
            ) from exc


class InflectionRunner(ModelRunner):
    """Inflection API runner for the B1 Pi.ai Path-B follow-on.

    Uses Inflection's OpenAI-compatible chat-completions surface. Each call is a
    single stateless prompt with no conversation id or retained session state.
    """

    DEFAULT_URL = "https://api.inflection.ai/v1/chat/completions"
    DEFAULT_MODEL = "inflection_3_pi"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        url: str | None = None,
        max_tokens: int = 256,
        temperature: float = 0.0,
        label: str | None = None,
        timeout_s: float = 30.0,
    ):
        self._api_key = api_key or os.environ.get("INFLECTION_API_KEY")
        if not self._api_key:
            raise RunnerError("INFLECTION_API_KEY not set")
        self._url = url or os.environ.get("INFLECTION_API_URL") or self.DEFAULT_URL
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = float(temperature)
        self._label = label or f"inflection:{model}"
        self._timeout_s = timeout_s

    def model_id(self) -> str:
        return self._label

    def complete(self, prompt: str) -> str:
        try:
            import httpx  # type: ignore  # noqa: F401 (egress imports httpx)
        except ImportError as exc:
            raise RunnerError("httpx not installed; pip install httpx") from exc

        from harness import egress

        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        resp = egress.post(self._url, headers=headers, json=body, timeout=self._timeout_s)
        if resp.status_code >= 400:
            raise RunnerError(f"inflection HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise RunnerError(f"inflection response was not JSON: {resp.text[:500]}") from exc
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RunnerError(
                f"inflection response shape unexpected: {_json.dumps(payload)[:500]}"
            ) from exc
        if not text:
            raise RunnerError("inflection returned empty response")
        return str(text)


def _resolve_dotted(obj: Any, path: str) -> Any:
    """Resolve a dotted path with [N] index syntax — e.g. 'choices[0].message.content'."""
    cur: Any = obj
    for part in path.replace("][", "].[").split("."):
        if not part:
            continue
        # array index suffix [N]
        while "[" in part and part.endswith("]"):
            head, _, idx_part = part.rpartition("[")
            idx = int(idx_part.rstrip("]"))
            if head:
                cur = cur[head]
                head = ""
            cur = cur[idx]
            part = head
        if part:
            cur = cur[part]
    return cur


class BotpressChatRunner(ModelRunner):
    """Botpress Cloud public Chat API — 4-step protocol per chat.botpress.cloud docs.

    Per-prompt flow uses a fresh conversation so multi-turn memory does not
    contaminate scan independence. The user identity is created once and
    reused across all prompts in the same scan.

      POST {base}/users                        -> user.id, key
      POST {base}/conversations  (x-user-key)  -> conversation.id
      POST {base}/messages       (x-user-key)  -> message.id
      GET  {base}/conversations/{id}/messages  -> poll until bot reply

    `client_id` is the Botpress webchat clientId (from the published webchat
    config JSON). `auth` is None — this runner targets the public Chat
    integration.
    """

    def __init__(self, *, client_id: str, label: str | None = None,
                 timeout_s: float = 30.0, poll_max_s: float = 30.0,
                 poll_interval_s: float = 0.5):
        if not client_id:
            raise RunnerError("BotpressChatRunner requires client_id")
        self._client_id = client_id
        self._base = f"https://chat.botpress.cloud/{client_id}"
        self._label = label or f"botpress:{client_id}"
        self._timeout_s = timeout_s
        self._poll_max_s = poll_max_s
        self._poll_interval_s = poll_interval_s
        self._user_key: str | None = None
        self._user_id: str | None = None

    def model_id(self) -> str:
        return self._label

    def _ensure_user(self, client: Any) -> None:
        if self._user_key:
            return
        r = client.post(f"{self._base}/users", json={})
        if r.status_code >= 400:
            raise RunnerError(f"botpress POST /users failed: HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        self._user_key = data["key"]
        self._user_id = data["user"]["id"]

    def complete(self, prompt: str) -> str:
        try:
            import httpx  # type: ignore  # noqa: F401  (egress module imports httpx itself)
        except ImportError as exc:
            raise RunnerError("httpx not installed; pip install httpx") from exc
        from harness import egress
        with egress.client(timeout=self._timeout_s) as client:
            self._ensure_user(client)
            headers = {"Content-Type": "application/json", "x-user-key": self._user_key}
            r = client.post(f"{self._base}/conversations", headers=headers, json={})
            if r.status_code >= 400:
                raise RunnerError(f"botpress POST /conversations failed: HTTP {r.status_code}: {r.text[:300]}")
            conv_id = r.json()["conversation"]["id"]
            r = client.post(
                f"{self._base}/messages",
                headers=headers,
                json={"conversationId": conv_id, "payload": {"type": "text", "text": prompt}},
            )
            if r.status_code >= 400:
                raise RunnerError(f"botpress POST /messages failed: HTTP {r.status_code}: {r.text[:300]}")
            sent_id = r.json()["message"]["id"]
            deadline = time.monotonic() + self._poll_max_s
            while time.monotonic() < deadline:
                time.sleep(self._poll_interval_s)
                r = client.get(f"{self._base}/conversations/{conv_id}/messages", headers=headers)
                if r.status_code >= 400:
                    raise RunnerError(f"botpress GET messages failed: HTTP {r.status_code}: {r.text[:300]}")
                msgs = r.json().get("messages", [])
                bot_msgs = [
                    m for m in msgs
                    if m.get("id") != sent_id
                    and m.get("payload", {}).get("type") == "text"
                    and m.get("userId") != self._user_id
                ]
                if bot_msgs:
                    return str(bot_msgs[0].get("payload", {}).get("text", ""))
            raise RunnerError(f"botpress bot did not reply within {self._poll_max_s}s")


class VoiceflowRunner(ModelRunner):
    """Voiceflow Dialog Manager API runner — general-runtime.voiceflow.com.

    Voiceflow returns an array of trace objects per turn (dtmf, text, debug,
    no-reply, end, etc.); the bot's actual reply lives in the first text
    trace's `payload.message`. A static dotted response_path can't filter by
    trace type, so this runner does it explicitly.

    Per-prompt flow uses a fresh userId so multi-turn state does not bleed
    across prompts — Voiceflow keys conversation state on the URL userId.
    """

    def __init__(self, *, project_id: str, api_key: str,
                 base_url: str = "https://general-runtime.voiceflow.com",
                 label: str | None = None,
                 timeout_s: float = 30.0,
                 user_id_prefix: str = "beaai-scan"):
        if not project_id or not api_key:
            raise RunnerError("VoiceflowRunner requires project_id and api_key")
        self._project_id = project_id
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._label = label or f"voiceflow:{project_id}"
        self._timeout_s = timeout_s
        self._user_id_prefix = user_id_prefix
        self._counter = 0

    def model_id(self) -> str:
        return self._label

    def _next_user_id(self) -> str:
        self._counter += 1
        return f"{self._user_id_prefix}-{self._counter}-{int(time.monotonic() * 1000)}"

    def complete(self, prompt: str) -> str:
        try:
            import httpx  # type: ignore
        except ImportError as exc:
            raise RunnerError("httpx not installed; pip install httpx") from exc
        user_id = self._next_user_id()
        url = f"{self._base}/state/user/{user_id}/interact?logs=off"
        headers = {
            "Content-Type": "application/json",
            "Authorization": self._api_key,
            "projectID": self._project_id,
        }
        body = {"action": {"type": "text", "payload": prompt}}
        from harness import egress
        try:
            resp = egress.post(url, headers=headers, json=body, timeout=self._timeout_s, session_id=user_id)
        except Exception as exc:
            raise RunnerError(f"voiceflow request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise RunnerError(f"voiceflow HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            traces = resp.json()
        except ValueError as exc:
            raise RunnerError(f"voiceflow response not JSON: {resp.text[:300]}") from exc
        if not isinstance(traces, list):
            raise RunnerError(f"voiceflow response not a trace array: {str(traces)[:300]}")
        # Prefer the first text trace; concatenate consecutive text traces so
        # multi-message replies survive intact.
        text_chunks: list[str] = []
        for t in traces:
            if t.get("type") == "text":
                payload = t.get("payload", {})
                msg = payload.get("message")
                if msg:
                    text_chunks.append(str(msg))
        if not text_chunks:
            # Some flows respond via choice/visual/end traces only — return
            # the first non-text payload as a debug fallback so the harness
            # can score "no useful response" rather than crashing.
            for t in traces:
                payload = t.get("payload", {})
                for k in ("message", "text", "label"):
                    if isinstance(payload.get(k), str) and payload[k]:
                        return str(payload[k])
            return ""
        return "\n".join(text_chunks)


class SyllableRunner(ModelRunner):
    """Syllable.cloud public webchat API runner — apichat.syllable.cloud.

    Two-step protocol per turn:

      POST /api/v1/chats/start_session  -> { id }            (also returns the
                                                              agent's greeting,
                                                              which we discard)
      POST /api/v1/chats/send_message   -> { message: { message: str, ... } }

    The Syllable showcase agent (insurance-claims persona, BEAAA-217/219) gates
    headers on a public `Syllable-Client-Id-Key` extracted from the published
    webchat HTML. There is no per-customer auth — the surface is a public demo.

    Each prompt opens a fresh session so multi-turn state cannot bleed across
    scan items (Syllable keys conversation state on session_id; the session
    timeout is ~10m). Session creation cost is a single extra HTTP call per
    prompt, which we accept for clean scan independence.
    """

    DEFAULT_BASE_URL = "https://apichat.syllable.cloud"
    DEFAULT_ORIGIN = "https://webchat.syllable.cloud"
    TRANSIENT_STATUS_CODES = frozenset({502, 503, 522})
    MAX_TRANSIENT_ATTEMPTS = 3
    RETRY_BACKOFF_SECONDS = (0.5, 1.0)

    def __init__(self, *, client_id_key: str, host_key: str,
                 base_url: str = DEFAULT_BASE_URL,
                 origin: str = DEFAULT_ORIGIN,
                 label: str | None = None,
                 timeout_s: float = 30.0):
        if not client_id_key or not host_key:
            raise RunnerError("SyllableRunner requires client_id_key and host_key")
        self._client_id_key = client_id_key
        self._host_key = host_key
        self._base = base_url.rstrip("/")
        self._origin = origin
        self._label = label or f"syllable:{client_id_key[:8]}"
        self._timeout_s = timeout_s
        self._scan_deadline_monotonic: float | None = None

    def model_id(self) -> str:
        return self._label

    def set_scan_deadline(self, deadline_monotonic: float) -> None:
        """Set the earliest scan-wide deadline for transient retries.

        The customer walker is invoked once per replicate, so later calls must
        not extend the first budget. A fresh runner is constructed per scan.
        """
        if self._scan_deadline_monotonic is None:
            self._scan_deadline_monotonic = deadline_monotonic
            return
        self._scan_deadline_monotonic = min(
            self._scan_deadline_monotonic,
            deadline_monotonic,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Syllable-Client-Id-Key": self._client_id_key,
            "Syllable-Host-Key": self._host_key,
            "Origin": self._origin,
        }

    @staticmethod
    def _is_transient_transport_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            token in message
            for token in (
                "522",
                "502",
                "503",
                "unexpected_eof",
                "eof occurred in violation of protocol",
                "connection reset",
                "readtimeout",
                "connecttimeout",
                "pooltimeout",
                "timed out",
                "timeout",
            )
        )

    def _request_timeout_for_deadline(self) -> float:
        if self._scan_deadline_monotonic is None:
            return self._timeout_s
        remaining = self._scan_deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise RunnerError("syllable scan deadline exceeded")
        return max(0.1, min(self._timeout_s, remaining))

    def _sleep_with_deadline(self, seconds: float) -> None:
        if self._scan_deadline_monotonic is None:
            time.sleep(seconds)
            return
        remaining = self._scan_deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise RunnerError("syllable scan deadline exceeded")
        time.sleep(min(seconds, remaining))

    def _syllable_post(
        self,
        path: str,
        *,
        phase: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        egress_session_id: str,
    ):
        from harness import egress

        last_exc: Exception | None = None
        url = f"{self._base}{path}"
        for attempt in range(self.MAX_TRANSIENT_ATTEMPTS):
            try:
                response = egress.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self._request_timeout_for_deadline(),
                    session_id=egress_session_id,
                )
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

            if (
                response.status_code in self.TRANSIENT_STATUS_CODES
                and attempt < self.MAX_TRANSIENT_ATTEMPTS - 1
            ):
                self._sleep_with_deadline(self.RETRY_BACKOFF_SECONDS[attempt])
                continue
            return response

        if last_exc is not None:
            raise RunnerError(
                f"syllable {phase} request failed: {last_exc}"
            ) from last_exc
        raise RunnerError(f"syllable {phase} request failed without response")

    def complete(self, prompt: str) -> str:
        try:
            import httpx  # type: ignore
        except ImportError as exc:
            raise RunnerError("httpx not installed; pip install httpx") from exc
        headers = self._headers()
        # One Syllable conversation = one egress session; derive a stable id
        # for sticky-IP rotation modes (per_session) from a fresh token.
        egress_session_id = secrets.token_hex(8)
        r = self._syllable_post(
            "/api/v1/chats/start_session",
            phase="start_session",
            headers=headers,
            payload={},
            egress_session_id=egress_session_id,
        )
        if r.status_code >= 400:
            raise RunnerError(
                f"syllable start_session HTTP {r.status_code}: {r.text[:300]}"
            )
        try:
            session = r.json()
        except ValueError as exc:
            raise RunnerError(
                f"syllable start_session response not JSON: {r.text[:300]}"
            ) from exc
        session_id = session.get("id")
        if not session_id:
            raise RunnerError(
                f"syllable start_session missing id: {str(session)[:300]}"
            )
        r = self._syllable_post(
            "/api/v1/chats/send_message",
            phase="send_message",
            headers=headers,
            payload={"session_id": session_id, "message": prompt},
            egress_session_id=egress_session_id,
        )
        if r.status_code >= 400:
            raise RunnerError(
                f"syllable send_message HTTP {r.status_code}: {r.text[:300]}"
            )
        try:
            body = r.json()
        except ValueError as exc:
            raise RunnerError(
                f"syllable send_message response not JSON: {r.text[:300]}"
            ) from exc
        msg = body.get("message")
        if isinstance(msg, dict):
            text = msg.get("message")
            if isinstance(text, str):
                return text
        return ""


class HTTPEndpointRunner(ModelRunner):
    """Generic HTTP-endpoint runner — scan any deployed AI surface.

    The canonical ARE-Scanner customer flow:
      1. Customer signs scanning agreement (BEAAA-22 §3 hosted-eval-only intake).
      2. Customer provides endpoint URL + auth token.
      3. We point this runner at it; the harness produces a ARE for THEIR
         deployed agent — including their system prompt, RAG, tool calls,
         post-processing — not for the underlying base model.

    Configuration via constructor or env vars:
      ARE_HTTP_URL              — endpoint URL (POST)
      ARE_HTTP_AUTH             — full Authorization header value (e.g. 'Bearer xxxx', 'xi-api-key xxxx')
      ARE_HTTP_REQUEST_TEMPLATE — JSON body template; '{prompt}' is substituted
      ARE_HTTP_RESPONSE_PATH    — dotted path to text in response (e.g. 'choices[0].message.content')
      ARE_HTTP_HEADERS_JSON     — optional extra headers as JSON object
      ARE_HTTP_LABEL            — label shown in model_id() (e.g. 'elevenlabs:my-voice-agent')

    Presets exist for common shapes (openai-compat, anthropic-compat, elevenlabs-convai).
    """

    PRESETS: dict[str, dict[str, Any]] = {
        # OpenAI-compatible chat completions (also: Together, Groq, Fireworks, vLLM, Ollama).
        "openai-compat": {
            "request_template": '{"model": "{model}", "messages": [{"role": "user", "content": "{prompt}"}], "temperature": 0, "max_tokens": 256}',
            "response_path": "choices[0].message.content",
            "auth_prefix": "Bearer ",
        },
        # Mistral chat completions w/ wrapper-OFF (safe_prompt=false). Used for
        # base-model leg of the wrapper-vs-base A/B in BEAAA-130.
        "mistral-base": {
            "request_template": '{"model": "{model}", "messages": [{"role": "user", "content": "{prompt}"}], "temperature": 0, "max_tokens": 256, "safe_prompt": false}',
            "response_path": "choices[0].message.content",
            "auth_prefix": "Bearer ",
        },
        # Mistral chat completions w/ wrapper-ON (safe_prompt=true). The
        # safety-system-prompt-injection leg of the BEAAA-130 A/B.
        "mistral-wrapper": {
            "request_template": '{"model": "{model}", "messages": [{"role": "user", "content": "{prompt}"}], "temperature": 0, "max_tokens": 256, "safe_prompt": true}',
            "response_path": "choices[0].message.content",
            "auth_prefix": "Bearer ",
        },
        # Anthropic Messages API.
        "anthropic-compat": {
            "request_template": '{"model": "{model}", "max_tokens": 256, "temperature": 0, "messages": [{"role": "user", "content": "{prompt}"}]}',
            "response_path": "content[0].text",
            "auth_header": "x-api-key",
            "auth_prefix": "",
            "extra_headers": {"anthropic-version": "2023-06-01"},
        },
        # ElevenLabs Conversational AI text endpoint (illustrative — confirm
        # exact path/shape against their docs at scan-time; they revise).
        "elevenlabs-convai": {
            "request_template": '{"text": "{prompt}"}',
            "response_path": "agent_response",
            "auth_header": "xi-api-key",
            "auth_prefix": "",
        },
    }

    def __init__(
        self,
        *,
        url: str | None = None,
        auth: str | None = None,
        request_template: str | None = None,
        response_path: str | None = None,
        extra_headers: dict[str, str] | None = None,
        model: str = "remote",
        label: str | None = None,
        preset: str | None = None,
        timeout_s: float = 30.0,
    ):
        self._url = url or os.environ.get("ARE_HTTP_URL", "")
        if not self._url:
            raise RunnerError("ARE_HTTP_URL not set (or pass url=...)")
        preset_cfg = self.PRESETS.get(preset, {}) if preset else {}
        self._request_template = (
            request_template
            or os.environ.get("ARE_HTTP_REQUEST_TEMPLATE")
            or preset_cfg.get("request_template")
        )
        self._response_path = (
            response_path
            or os.environ.get("ARE_HTTP_RESPONSE_PATH")
            or preset_cfg.get("response_path")
        )
        if not self._request_template or not self._response_path:
            raise RunnerError(
                "request_template + response_path required (set via constructor, env, or preset)"
            )
        env_headers = os.environ.get("ARE_HTTP_HEADERS_JSON", "")
        env_extra = _json.loads(env_headers) if env_headers else {}
        self._extra_headers = {
            **preset_cfg.get("extra_headers", {}),
            **env_extra,
            **(extra_headers or {}),
        }
        auth_header = preset_cfg.get("auth_header", "Authorization")
        auth_prefix = preset_cfg.get("auth_prefix", "Bearer ")
        token = auth or os.environ.get("ARE_HTTP_AUTH", "")
        if token and not token.startswith(auth_prefix):
            self._extra_headers[auth_header] = f"{auth_prefix}{token}"
        elif token:
            self._extra_headers[auth_header] = token
        self._model = model
        self._label = label or os.environ.get("ARE_HTTP_LABEL", f"http:{self._url}")
        self._timeout_s = timeout_s

    def model_id(self) -> str:
        return self._label

    def complete(self, prompt: str) -> str:
        try:
            import httpx  # type: ignore
        except ImportError as exc:
            raise RunnerError("httpx not installed; pip install httpx") from exc

        # Substitute {prompt} (and {model}) into the JSON body template.
        # Escape JSON-sensitive chars in the prompt before substitution.
        safe_prompt = _json.dumps(prompt)[1:-1]  # strip surrounding quotes
        body_str = self._request_template.replace("{prompt}", safe_prompt).replace("{model}", self._model)
        try:
            body = _json.loads(body_str)
        except _json.JSONDecodeError as exc:
            raise RunnerError(f"request_template did not produce valid JSON: {exc}") from exc

        headers = {"Content-Type": "application/json", **self._extra_headers}
        # Retry on 429 (free-tier rate limit) with exponential backoff.
        # Honors Retry-After header when present.
        max_retries = 6
        backoff = 1.0
        resp = None
        from harness import egress
        for attempt in range(max_retries):
            try:
                resp = egress.post(self._url, headers=headers, json=body, timeout=self._timeout_s)
            except httpx.HTTPError as exc:
                raise RunnerError(f"HTTP request failed: {exc}") from exc
            if resp.status_code != 429:
                break
            retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
            wait_s = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else backoff
            time.sleep(wait_s)
            backoff = min(backoff * 2, 16.0)
        if resp is None:
            raise RunnerError("HTTP request failed without response")
        if resp.status_code >= 400:
            raise RunnerError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise RunnerError(f"response was not JSON: {resp.text[:500]}") from exc
        try:
            text = _resolve_dotted(payload, self._response_path)
        except (KeyError, IndexError, TypeError) as exc:
            raise RunnerError(
                f"response_path {self._response_path!r} did not resolve in payload: {exc}; "
                f"received: {_json.dumps(payload)[:500]}"
            ) from exc
        return str(text or "")


class HuggingChatWebRunner(ModelRunner):
    """HuggingChat web-app runner — exercises the FULL wrapper, not the base model.

    Difference vs `_build_huggingchat` in path_b_endpoints.py:
      - HF Router (`router.huggingface.co/v1/chat/completions`) hits the
        underlying model directly. No system prompt, no content filter,
        no refusal tuning beyond what the model itself shipped with.
      - This runner drives huggingface.co/chat — the actual web wrapper.
        Whatever HF layered on top (system prompt, content filter, refusal
        tuning, tool surface) is in the loop. Provenance: `measured` (not
        category-proxy) for the HuggingChat-the-product surface.

    Auth surfaces:
      - cookies dict / RequestsCookieJar — the cookies HF stores after web
        login. Most reliable; rotates by clicking "log out" in the browser.
      - cookie_path — path to a hugchat-format cookies file (json).
      - email + passwd — last resort; uses hugchat.Login flow internally.

    Per-prompt flow creates a fresh conversation, sends the prompt, captures
    the assistant text, and deletes the conversation. Mirrors VoiceflowRunner's
    fresh-userId pattern: no in-context contamination across probes.
    """

    DEFAULT_MODEL_NAME = "meta-llama/Llama-3.3-70B-Instruct"

    def __init__(
        self,
        *,
        cookies: Any = None,
        cookie_path: str | None = None,
        email: str | None = None,
        passwd: str | None = None,
        model_name: str | None = None,
        label: str | None = None,
        delete_after: bool = True,
    ):
        if cookies is None and not cookie_path and not (email and passwd):
            raise RunnerError(
                "HuggingChatWebRunner requires one of: cookies, cookie_path, "
                "or email+passwd"
            )
        self._cookies = cookies
        self._cookie_path = cookie_path
        self._email = email
        self._passwd = passwd
        self._model_name = model_name or self.DEFAULT_MODEL_NAME
        self._label = label or f"huggingchat-web:{self._model_name}"
        self._delete_after = delete_after
        self._chatbot: Any = None
        self._model_index: int | None = None

    def model_id(self) -> str:
        return self._label

    def _ensure_chatbot(self) -> Any:
        if self._chatbot is not None:
            return self._chatbot
        try:
            from hugchat import hugchat as hc  # type: ignore
            from hugchat.login import Login  # type: ignore
        except ImportError as exc:
            raise RunnerError(
                "hugchat not installed; pip install hugchat"
            ) from exc
        cookies = self._cookies
        if cookies is None and self._email and self._passwd:
            try:
                sign = Login(self._email, self._passwd)
                cookies = sign.login(save_cookies=False)
            except Exception as exc:
                raise RunnerError(f"hugchat login failed: {exc}") from exc
        try:
            if cookies is not None:
                bot = hc.ChatBot(cookies=cookies if isinstance(cookies, dict) else cookies.get_dict())
            elif self._cookie_path:
                bot = hc.ChatBot(cookie_path=self._cookie_path)
            else:
                raise RunnerError("no cookie material resolved")
        except Exception as exc:
            raise RunnerError(f"hugchat ChatBot init failed: {exc}") from exc

        try:
            available = bot.get_available_llm_models()
        except Exception as exc:
            raise RunnerError(f"hugchat get_available_llm_models failed: {exc}") from exc
        idx: int | None = None
        for i, m in enumerate(available):
            name = getattr(m, "name", None) or getattr(m, "id", None) or str(m)
            if name and self._model_name.lower() in str(name).lower():
                idx = i
                break
        if idx is None:
            names = [getattr(m, "name", None) or str(m) for m in available]
            raise RunnerError(
                f"model {self._model_name!r} not in HuggingChat available list; "
                f"got: {names}"
            )
        self._model_index = idx
        self._chatbot = bot
        return bot

    def complete(self, prompt: str) -> str:
        bot = self._ensure_chatbot()
        try:
            conv = bot.new_conversation(modelIndex=self._model_index, switch_to=True)
        except Exception as exc:
            raise RunnerError(f"hugchat new_conversation failed: {exc}") from exc
        try:
            try:
                msg = bot.chat(prompt, conversation=conv)
            except Exception as exc:
                raise RunnerError(f"hugchat chat() failed: {exc}") from exc
            try:
                text = msg.wait_until_done() if hasattr(msg, "wait_until_done") else None
            except Exception:
                text = None
            if not text:
                try:
                    text = str(msg)
                except Exception:
                    text = ""
            return text or ""
        finally:
            if self._delete_after:
                try:
                    bot.delete_conversation(conv)
                except Exception:
                    pass
