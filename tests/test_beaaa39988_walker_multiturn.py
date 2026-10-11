"""BEAAA-39988: multi-turn starter-100@5 cases on conversation runners.

The live acceptance run aborted the Kaltura scan when the walker sent
``complete("")`` to the browser runner (disabled Ask button -> Playwright
TimeoutError escaping the walker).  Conversation runners must receive the real
turns, and browser control failures must surface as ``RunnerError``.
"""

from __future__ import annotations

import pytest

from harness.kaltura_genie_browser import KalturaGenieBrowserRunner
from harness.runner import RunnerError
from scanners.customer_agent_walker import _dispatch_multiturn
from scanners.starter_pack_v5 import STARTER_V5_CASES, StarterCaseV5MT

MT_CASE = next(case for case in STARTER_V5_CASES if isinstance(case, StarterCaseV5MT))


class _ConversationRunner:
    def __init__(self) -> None:
        self.conversations: list[tuple[str, ...]] = []

    def complete(self, prompt: str) -> str:  # pragma: no cover - must not be used
        raise AssertionError(f"complete() called with {prompt!r}")

    def complete_conversation(self, turns):
        self.conversations.append(tuple(turns))
        return tuple(f"reply:{turn}" for turn in turns)


class _PayloadRunner:
    _config = {"model": "pinned/model"}

    def __init__(self) -> None:
        self._messages_payload = None
        self.payloads: list[dict] = []

    def complete(self, prompt: str) -> str:
        assert prompt == ""
        self.payloads.append(self._messages_payload)
        return f"reply{len(self.payloads)}"


def test_conversation_runner_receives_real_turns_in_one_thread() -> None:
    runner = _ConversationRunner()
    assert _dispatch_multiturn(runner, MT_CASE) == f"reply:{MT_CASE.turns[-1]}"
    assert runner.conversations == [tuple(MT_CASE.turns)]
    assert all(turn.strip() for turn in runner.conversations[0])


def test_conversation_runner_reply_count_mismatch_is_runner_error() -> None:
    class Short(_ConversationRunner):
        def complete_conversation(self, turns):
            return ("only one",)

    with pytest.raises(RunnerError):
        _dispatch_multiturn(Short(), MT_CASE)


def test_http_runner_keeps_messages_payload_path() -> None:
    runner = _PayloadRunner()
    assert _dispatch_multiturn(runner, MT_CASE) == f"reply{len(MT_CASE.turns)}"
    assert len(runner.payloads) == len(MT_CASE.turns)
    final = runner.payloads[-1]
    assert final["model"] == "pinned/model"
    assert final["messages"][0] == {"role": "system", "content": MT_CASE.system}
    assert [m["content"] for m in final["messages"] if m["role"] == "user"] == list(MT_CASE.turns)
    assert runner._messages_payload is None


class _Locator:
    def __init__(self, count: int = 1, click_exc: Exception | None = None) -> None:
        self._count = count
        self._click_exc = click_exc
        self.filled: list[str] = []

    def count(self) -> int:
        return self._count

    def fill(self, value: str) -> None:
        self.filled.append(value)

    def click(self, timeout: int) -> None:
        if self._click_exc is not None:
            raise self._click_exc


class _Page:
    def __init__(self, click_exc: Exception | None = None) -> None:
        self.composer = _Locator()
        self.button = _Locator(click_exc=click_exc)

    def locator(self, selector: str) -> _Locator:
        return self.composer if "chat-messages" not in selector else _Locator(count=0)

    def get_by_role(self, role: str, name: str, exact: bool) -> _Locator:
        return self.button


def test_kaltura_empty_prompt_is_runner_error_not_typed() -> None:
    page = _Page()
    with pytest.raises(RunnerError, match="empty prompt"):
        KalturaGenieBrowserRunner()._complete_turn(page, "")
    assert page.composer.filled == []


def test_kaltura_disabled_ask_control_is_runner_error() -> None:
    class TimeoutError_(Exception):
        pass

    page = _Page(click_exc=TimeoutError_("Locator.click: Timeout 10000ms exceeded"))
    with pytest.raises(RunnerError, match="ask control unavailable"):
        KalturaGenieBrowserRunner()._complete_turn(page, "hello")


class _ResetRunner(KalturaGenieBrowserRunner):
    """Scripted pages: each _open() hands out the next (articles, reset_ok) page."""

    def __init__(self, pages) -> None:
        super().__init__()
        self._script = list(pages)
        self.opened = 0
        self.closed = 0

    def _open(self):
        if self._page is None:
            articles, reset_ok = self._script[self.opened]
            self.opened += 1
            self._page = _ResetPage(articles, reset_ok)
        return self._page

    def _fresh_thread(self, page) -> None:
        if not page.reset_ok:
            raise RunnerError("kaltura genie fresh-thread reset failed")

    def close(self) -> None:
        if self._page is not None:
            self.closed += 1
        self._page = None

    def _complete_turn(self, page, prompt):
        return f"reply:{prompt}"


class _ResetPage:
    def __init__(self, articles: int, reset_ok: bool) -> None:
        self.articles, self.reset_ok = articles, reset_ok

    def locator(self, selector: str) -> _Locator:
        return _Locator(count=self.articles)


def test_kaltura_failed_reset_relaunches_once_and_recovers() -> None:
    runner = _ResetRunner([(3, False), (0, True)])
    assert runner.complete("hi") == "reply:hi"
    assert (runner.opened, runner.closed) == (2, 1)
    # the next case reuses the healthy relaunched page
    assert runner.complete_conversation(["a", "b"]) == ("reply:a", "reply:b")
    assert runner.opened == 2


def test_kaltura_relaunch_with_populated_thread_fails_closed() -> None:
    runner = _ResetRunner([(3, False), (2, True)])
    with pytest.raises(RunnerError, match="after relaunch"):
        runner.complete("hi")


def test_kaltura_healthy_reset_does_not_relaunch() -> None:
    runner = _ResetRunner([(3, True)])
    assert runner.complete("hi") == "reply:hi"
    assert (runner.opened, runner.closed) == (1, 0)
