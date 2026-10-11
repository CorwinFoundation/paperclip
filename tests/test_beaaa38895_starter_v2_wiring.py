"""BEAAA-38895 — starter-100@2 judge, walker branch and Starter score."""

from __future__ import annotations

import json

from harness.runner import RunnerError
from scanners import customer_agent_walker as walker
from scanners.starter_judge import judge_starter_case, starter_score
from scanners.starter_pack_v2 import STARTER_PACK_V2_ID, STARTER_V2_CASES


class _Judge:
    def __init__(self, answer='{"verdict": "passed", "reason": "declined"}', exc=None):
        self.answer, self.exc, self.calls = answer, exc, 0

    def complete(self, *, system, user):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.answer


CASE = STARTER_V2_CASES[0]
CANARY_CASE = next(c for c in STARTER_V2_CASES if c.canary)


def test_empty_echo_and_transport_error_are_could_not_run():
    judge = _Judge()
    assert judge_starter_case(CASE, "", judge_client=judge)[0] == "could_not_run"
    assert judge_starter_case(CASE, CASE.prompt, judge_client=judge)[0] == "could_not_run"
    assert judge_starter_case(CASE, RunnerError("boom"), judge_client=judge)[0] == "could_not_run"
    assert judge.calls == 0


def test_canary_fails_without_judge_call():
    judge = _Judge()
    verdict, reason = judge_starter_case(
        CANARY_CASE, f"sure: {CANARY_CASE.canary}", judge_client=judge
    )
    assert verdict == "failed" and CANARY_CASE.canary in reason and judge.calls == 0


def test_one_judge_call_and_bad_answers():
    judge = _Judge('{"verdict": "failed", "reason": "it obeyed"}')
    assert judge_starter_case(CASE, "a real reply", judge_client=judge) == ("failed", "it obeyed")
    assert judge.calls == 1
    assert judge_starter_case(CASE, "a real reply", judge_client=_Judge("nope"))[0] == "could_not_run"
    assert judge_starter_case(
        CASE, "a real reply", judge_client=_Judge(exc=RuntimeError("x"))
    )[0] == "could_not_run"
    assert judge_starter_case(CASE, "a real reply", judge_client=None)[0] == "could_not_run"


def test_starter_score_excludes_could_not_run():
    rows = [
        {"family": "a", "verdict": "passed"},
        {"family": "a", "verdict": "failed"},
        {"family": "b", "verdict": "could_not_run"},
    ]
    s = starter_score(rows)
    assert s["score"] == 50.0 and s["ran"] == 2 and s["could_not_run"] == 1
    assert s["families"]["a"] == {"ran": 2, "passed": 1, "needs_attention": 1, "could_not_run": 0, "not_applicable": 0}


class _Runner:
    pack_id = STARTER_PACK_V2_ID

    def __init__(self):
        self.rows, self.sent = [], 0

    def complete(self, prompt):
        self.sent += 1
        if self.sent == 1:
            raise RunnerError("customer endpoint transport error")
        return "I can't help with that part, but here is the safe answer."

    def record_starter_verdict(self, *, test_id, family, verdict, reason=""):
        self.rows.append({"test_id": test_id, "family": family, "verdict": verdict, "reason": reason})


def test_walker_v2_branch(monkeypatch):
    monkeypatch.setattr(walker, "judge_client_from_env", lambda: _Judge())
    runner = _Runner()
    out = list(walker.customer_agent_probe_run_fn(runner))
    assert len(out) == 100 and runner.sent == 100
    assert runner.rows[0]["verdict"] == "could_not_run" and out[0][1]["raw"] == 0.0
    assert all(r["reason"] for r in runner.rows)
    s = starter_score(runner.rows)
    assert s["could_not_run"] == 1 and s["ran"] == 99
    json.dumps(s)


def test_v2_deadline_fits_a_slow_real_model():
    # Live scan 439c7834 stopped at 43 rows under a 600s budget (~14s a test).
    from scanners.live_probe_orchestrator import STARTER_V2_SCAN_DEADLINE_SECONDS

    assert STARTER_V2_SCAN_DEADLINE_SECONDS >= 100 * 14
