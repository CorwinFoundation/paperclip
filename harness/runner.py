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

    def __init__(self, model: str, *, max_tokens: int = 256, temperature: float = 0.0):
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = float(temperature)
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
    ):
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = float(temperature)
        self._seed = seed
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
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "messages": [{"role": "user", "content": prompt}],
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
    ):
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._min_interval_s = float(min_interval_s)
        self._disable_thinking = disable_thinking
        self._temperature = float(temperature)
        self._seed = seed
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

    def __init__(self, model: str, *, max_tokens: int = 256, temperature: float = 0.0):
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RunnerError("OPENROUTER_API_KEY not set")
        self._model = model
        # Lazy build the HTTPEndpointRunner that does the work.
        self._inner = HTTPEndpointRunner(
            model=model,
            url="https://openrouter.ai/api/v1/chat/completions",
            auth=api_key,
            preset="openai-compat",
            label=f"openrouter:{model}",
        )
        # Stash temp/max so we can patch the request_template.
        self._max_tokens = max_tokens
        self._temperature = float(temperature)

    def model_id(self) -> str:
        return f"openrouter:{self._model}"

    def complete(self, prompt: str) -> str:
        return self._inner.complete(prompt)


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
            import httpx  # type: ignore
        except ImportError as exc:
            raise RunnerError("httpx not installed; pip install httpx") from exc
        with httpx.Client(timeout=self._timeout_s) as client:
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
        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=self._timeout_s)
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

    def model_id(self) -> str:
        return self._label

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Syllable-Client-Id-Key": self._client_id_key,
            "Syllable-Host-Key": self._host_key,
            "Origin": self._origin,
        }

    def complete(self, prompt: str) -> str:
        try:
            import httpx  # type: ignore
        except ImportError as exc:
            raise RunnerError("httpx not installed; pip install httpx") from exc
        headers = self._headers()
        try:
            r = httpx.post(
                f"{self._base}/api/v1/chats/start_session",
                headers=headers, json={}, timeout=self._timeout_s,
            )
        except Exception as exc:
            raise RunnerError(f"syllable start_session request failed: {exc}") from exc
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
        try:
            r = httpx.post(
                f"{self._base}/api/v1/chats/send_message",
                headers=headers,
                json={"session_id": session_id, "message": prompt},
                timeout=self._timeout_s,
            )
        except Exception as exc:
            raise RunnerError(f"syllable send_message request failed: {exc}") from exc
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
        try:
            resp = httpx.post(self._url, headers=headers, json=body, timeout=self._timeout_s)
        except httpx.HTTPError as exc:
            raise RunnerError(f"HTTP request failed: {exc}") from exc
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

