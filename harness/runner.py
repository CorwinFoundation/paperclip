"""ModelRunner abstraction — pluggable model-API adapter for the eval harness.

`MockRunner` is the only adapter exercised by the test suite (deterministic,
no network). `AnthropicRunner` and `OpenAIRunner` are thin HTTP shims —
flip from mock to real by setting the appropriate API key env var and
selecting the runner on the CLI.

Each adapter must implement `complete(prompt) -> str` and `model_id()`.
"""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from typing import Callable


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

    Not exercised in tests — flip to this by setting ANTHROPIC_API_KEY and
    `--runner=anthropic --model=claude-...` on scripts/poc_real_score.py.
    """

    def __init__(self, model: str, *, max_tokens: int = 256):
        self._model = model
        self._max_tokens = max_tokens
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
        msg = client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")


class OpenAIRunner(ModelRunner):
    """Real OpenAI adapter. Lazy-imports the SDK; raises on missing key."""

    def __init__(self, model: str, *, max_tokens: int = 256):
        self._model = model
        self._max_tokens = max_tokens
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
        resp = client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
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
        max_output_tokens: int = 256,
        min_interval_s: float = 13.0,
    ):
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._min_interval_s = float(min_interval_s)
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
            from google.genai import types  # type: ignore
        except ImportError as exc:
            raise RunnerError(
                "google-genai SDK not installed; pip install google-genai"
            ) from exc
        self._throttle()
        client = genai.Client(api_key=self._api_key)
        try:
            resp = client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(max_output_tokens=self._max_output_tokens),
            )
        finally:
            self._last_call_at = time.monotonic()
        return resp.text or ""
