"""Fail-closed browser transport for Kaltura's public Learning Genie."""
from __future__ import annotations

import re
import time
from typing import Any

from harness.runner import ModelRunner, RunnerError


PUBLIC_GENIE_URL = "https://learning.kaltura.com/"
_ROOT = "#genie-application-container"
_COMPOSER = 'textarea[placeholder="Ask me anything"]'
_MAX_RESULT_TEXT_CHARS = 4_000
_RESULT_TEXT_TRUNCATION_MARKER = "\n[TRUNCATED]"
_TOOL_ANNOUNCEMENT = re.compile(
    r"(?:let me|i(?:'ll| will)|allow me to)\s+(?:search|look(?: that| this)? up|find|check)"
    r"(?:\s+(?:for|the|our|your|those|it|that|this|now))*[.!…:]?\s*$",
    re.IGNORECASE,
)


def ends_with_tool_announcement(text: str) -> bool:
    """Return true when capture contains only the pre-tool hand-off."""
    return bool(_TOOL_ANNOUNCEMENT.search(text.strip()))


def extract_assistant_narrative(article: Any) -> str:
    """Extract only the assistant narrative from the observed article shape."""
    wrappers = article.locator(":scope > div")
    if wrappers.count() != 1:
        raise RunnerError("kaltura genie article wrapper structure changed")
    blocks = wrappers.nth(0).locator(":scope > div")
    if blocks.count() != 3:
        raise RunnerError("kaltura genie message structure changed")
    assistant = blocks.nth(1)
    children = assistant.locator(":scope > *")
    if children.count() < 3:
        raise RunnerError("kaltura genie assistant structure changed")
    # child 0 is the avatar; 1 and 2 are the narrative wrappers; later
    # children are social actions, next steps, and optional source widgets.
    fragments = [children.nth(index).inner_text().strip() for index in (1, 2)]
    answer = "\n".join(fragment for fragment in fragments if fragment).strip()
    if not answer:
        raise RunnerError("kaltura genie returned no assistant narrative")
    return answer


class KalturaGenieBrowserRunner(ModelRunner):
    """One browser session, one freshly-cleared Genie thread per probe."""

    def __init__(
        self,
        endpoint: str = PUBLIC_GENIE_URL,
        *,
        timeout_s: float = 90.0,
        endpoint_config: dict[str, Any] | None = None,
    ):
        if endpoint != PUBLIC_GENIE_URL:
            raise RunnerError("kaltura genie browser target is not the approved public URL")
        self._endpoint = endpoint
        self._timeout_s = timeout_s
        self._config = endpoint_config or {}
        self._result_rows_enabled = bool(self._config.get("_include_result_rows"))
        self._redaction_values = tuple(
            value
            for value in (self._config.get("system_prompt"),)
            if isinstance(value, str) and value
        )
        self._pending_exchange: tuple[str, str] | None = None
        self._starter_rows: list[dict[str, str]] = []
        self._pw = self._browser = self._context = self._page = None

    def model_id(self) -> str:
        return "kaltura-genie:public-learning-browser"

    def _open(self):
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise RunnerError("kaltura genie browser readiness failed at playwright import") from exc
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
        except Exception as exc:
            self.close()
            raise RunnerError("kaltura genie browser readiness failed at chromium launch") from exc
        try:
            self._context = self._browser.new_context(
                viewport={"width": 1366, "height": 900},
                user_agent="BackBond-Scanner/0.5 (authorized Kaltura public scan)",
            )
            self._page = self._context.new_page()
            self._page.goto(self._endpoint, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            self.close()
            raise RunnerError("kaltura genie connection failed at public target navigation") from exc
        try:
            self._page.locator(_ROOT).wait_for(state="visible", timeout=45_000)
        except Exception as exc:
            self.close()
            raise RunnerError("kaltura genie connection failed at application root") from exc
        try:
            self._page.locator(_COMPOSER).wait_for(state="visible", timeout=30_000)
            return self._page
        except Exception as exc:
            self.close()
            raise RunnerError("kaltura genie connection failed at composer readiness") from exc

    def _fresh_thread(self, page: Any) -> None:
        articles = page.locator(f"{_ROOT} #chat-messages article")
        if articles.count() == 0:
            return
        try:
            page.get_by_role("button", name="New thread", exact=True).click(timeout=10_000)
            dialog = page.get_by_role("dialog", name="Start a new thread", exact=True)
            dialog.get_by_role("button", name="Clear thread", exact=True).click(timeout=10_000)
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and articles.count() != 0:
                page.wait_for_timeout(200)
            if articles.count() != 0:
                raise RunnerError("kaltura genie thread did not clear")
        except RunnerError:
            raise
        except Exception as exc:
            raise RunnerError("kaltura genie fresh-thread reset failed") from exc

    def _fresh_page(self) -> Any:
        """Return a page with an empty thread, relaunching the browser once.

        A failed in-page reset (e.g. after an unrecognised answer shape) used
        to poison every later case.  A new browser context carries no thread
        state, so one relaunch recovers; a still-populated thread fails closed.
        """
        page = self._open()
        try:
            self._fresh_thread(page)
            return page
        except RunnerError:
            self.close()
        page = self._open()
        if page.locator(f"{_ROOT} #chat-messages article").count() != 0:
            raise RunnerError("kaltura genie fresh-thread reset failed after relaunch")
        return page

    def complete(self, prompt: str) -> str:
        page = self._fresh_page()
        return self._complete_turn(page, prompt)

    def complete_conversation(self, turns: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        """Run one case in one fresh thread, preserving context between turns."""
        if not turns:
            raise RunnerError("kaltura genie conversation requires at least one turn")
        page = self._fresh_page()
        return tuple(self._complete_turn(page, prompt) for prompt in turns)

    def _complete_turn(self, page: Any, prompt: str) -> str:
        articles = page.locator(f"{_ROOT} #chat-messages article")
        baseline = articles.count()
        composer = page.locator(_COMPOSER)
        if composer.count() != 1:
            raise RunnerError("kaltura genie composer contract changed")
        if not prompt.strip():
            raise RunnerError("kaltura genie cannot submit an empty prompt")
        if prompt.lstrip().startswith("System deployment:"):
            raise RunnerError("kaltura live prompt contains deployment persona in user turn")
        try:
            composer.fill(prompt)
            page.get_by_role("button", name="Ask", exact=True).click(timeout=10_000)
        except Exception as exc:
            raise RunnerError("kaltura genie ask control unavailable") from exc

        deadline = time.monotonic() + self._timeout_s
        last_text = ""
        stable_since: float | None = None
        while time.monotonic() < deadline:
            page.wait_for_timeout(400)
            error = page.get_by_role("alert")
            if error.count() and error.first.is_visible():
                raise RunnerError("kaltura genie displayed an error")
            busy = (
                page.get_by_text("Analyzing content", exact=True).count()
                or page.get_by_role("button", name="Stop", exact=True).count()
            )
            if articles.count() != baseline + 1 or busy:
                stable_since = None
                continue
            answer = extract_assistant_narrative(articles.nth(baseline))
            if answer != last_text:
                last_text = answer
                stable_since = time.monotonic()
                continue
            if stable_since is not None and time.monotonic() - stable_since >= 2.0:
                if ends_with_tool_announcement(answer):
                    stable_since = None
                    continue
                if self._result_rows_enabled:
                    self._pending_exchange = (
                        self._redact_result_text(prompt),
                        self._redact_result_text(answer),
                    )
                return answer
        raise RunnerError("kaltura genie response timed out before stable completion")

    def record_starter_verdict(
        self, *, test_id: str, family: str, verdict: str, reason: str = ""
    ) -> None:
        """Bind the walker's verdict to the real browser prompt/reply pair."""
        if not self._result_rows_enabled or self._pending_exchange is None:
            self._pending_exchange = None
            return
        prompt, reply = self._pending_exchange
        row = {
            "test_id": test_id,
            "family": family,
            "prompt": self._cap_result_text(self._redact_result_text(prompt)),
            "reply": self._cap_result_text(self._redact_result_text(reply)),
            "verdict": verdict,
        }
        if reason:
            row["reason"] = self._cap_result_text(self._redact_result_text(reason))
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

    def close(self) -> None:
        for obj in (self._context, self._browser, self._pw):
            if obj is not None:
                try:
                    obj.close() if hasattr(obj, "close") else obj.stop()
                except Exception:
                    pass
        self._pw = self._browser = self._context = self._page = None

    def __del__(self) -> None:
        self.close()
