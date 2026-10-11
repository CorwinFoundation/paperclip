"""BEAAA-39988: the starter judge must grade starter-100@5 multi-turn cases.

Live acceptance run v3 (58a142be6) aborted every agent at ESC-01 with
``AttributeError: 'StarterCaseV5MT' object has no attribute 'prompt'`` inside
``judge_starter_case``; the walker re-raises judge exceptions, so every
starter-100@5 scan stopped at 86/100 rows.
"""

from __future__ import annotations

from scanners.customer_agent_walker import customer_agent_probe_run_fn
from scanners.starter_judge import (
    COULD_NOT_RUN,
    FAILED,
    PASSED,
    judge_starter_case,
)
from scanners.starter_pack_v5 import STARTER_PACK_V5_ID, STARTER_V5_CASES, StarterCaseV5MT

ESC = [c for c in STARTER_V5_CASES if isinstance(c, StarterCaseV5MT)]


class _Judge:
    def __init__(self, verdict: str = PASSED) -> None:
        self.verdict = verdict
        self.users: list[str] = []

    def complete(self, *, system: str, user: str) -> str:
        self.users.append(user)
        return '{"verdict": "%s", "reason": "ok"}' % self.verdict


def test_every_esc_case_reaches_the_judge_with_all_turns_and_expectation():
    assert len(ESC) == 14
    for case in ESC:
        judge = _Judge(FAILED)
        verdict, reason = judge_starter_case(case, "Sure, done.", judge_client=judge)
        assert (verdict, reason) == (FAILED, "ok"), case.case_id
        (user,) = judge.users
        assert "Test family: refusal_escalation" in user
        assert "no rubric" not in user
        for i, turn in enumerate(case.turns, 1):
            assert f"User turn {i}: {turn}" in user
        assert f"Expected behaviour for this case: {case.expect}" in user


def test_esc_echo_of_final_turn_is_could_not_run():
    case = ESC[0]
    verdict, _ = judge_starter_case(case, case.turns[-1], judge_client=_Judge())
    assert verdict == COULD_NOT_RUN


def test_single_turn_judge_prompt_is_unchanged():
    case = next(c for c in STARTER_V5_CASES if not isinstance(c, StarterCaseV5MT))
    judge = _Judge()
    judge_starter_case(case, "A real answer.", judge_client=judge)
    (user,) = judge.users
    assert f"<prompt>\n{case.prompt}\n</prompt>" in user
    assert "User turn" not in user and "Expected behaviour" not in user


class _ChatOnlyRunner:
    pack_id = STARTER_PACK_V5_ID
    has_tools = False
    _messages_payload = None

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def complete(self, prompt: str) -> str:
        return "I can't do that, but I can help with something else."

    def record_starter_verdict(self, **row) -> None:
        self.rows.append(row)


def test_walker_completes_all_100_v5_cases(monkeypatch):
    judge = _Judge()
    monkeypatch.setattr(
        "scanners.customer_agent_walker.judge_client_from_env", lambda: judge
    )
    runner = _ChatOnlyRunner()
    yielded = list(customer_agent_probe_run_fn(runner))
    assert len(yielded) == 100
    assert len({r["test_id"] for r in runner.rows}) == 100
    esc = [r for r in runner.rows if r["family"] == "refusal_escalation"]
    assert len(esc) == 14 and {r["verdict"] for r in esc} == {PASSED}
