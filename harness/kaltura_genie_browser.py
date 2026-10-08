"""Fail-closed browser transport for Kaltura's public Learning Genie."""
from __future__ import annotations

import time
from typing import Any

from harness.runner import ModelRunner, RunnerError


PUBLIC_GENIE_URL = "https://learning.kaltura.com/"
_ROOT = "#genie-application-container"
_COMPOSER = 'textarea[placeholder="Ask me anything"]'


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

    def __init__(self, endpoint: str = PUBLIC_GENIE_URL, *, timeout_s: float = 90.0):
        if endpoint != PUBLIC_GENIE_URL:
            raise RunnerError("kaltura genie browser target is not the approved public URL")
        self._endpoint = endpoint
        self._timeout_s = timeout_s
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

    def complete(self, prompt: str) -> str:
        page = self._open()
        self._fresh_thread(page)
        articles = page.locator(f"{_ROOT} #chat-messages article")
        baseline = articles.count()
        composer = page.locator(_COMPOSER)
        if composer.count() != 1:
            raise RunnerError("kaltura genie composer contract changed")
        composer.fill(prompt)
        page.get_by_role("button", name="Ask", exact=True).click(timeout=10_000)

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
                return answer
        raise RunnerError("kaltura genie response timed out before stable completion")

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
