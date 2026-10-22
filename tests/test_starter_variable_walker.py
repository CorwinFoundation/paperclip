"""Actual variable Starter payload dispatch with fixture replies, no API spend."""

from collections import Counter

import pytest

from scanners import customer_agent_walker as walker
from harness.runner import RunnerError
from scanners.live_probe_orchestrator import ProbeRunIncompleteError
from scanners.starter_forms import reserve_starter_cases
from scanners.starter_judge import starter_pack_id_for_rows
from scanners.variable_test_forms import FormStore


class FixtureJudge:
    def complete(self, *, system, user):
        return '{"verdict":"failed","reason":"The fixture reply does not address the request."}'


class FixtureRunner:
    pack_id = "starter-100@4"

    def __init__(self, cases):
        self.starter_cases = cases
        self.prompts = []
        self.rows = []
        self.progress_events = []
        self.case_progress_callback = lambda **event: self.progress_events.append(event)

    def complete(self, prompt):
        self.prompts.append(prompt)
        return "A generic fixture response."

    def record_starter_verdict(self, **row):
        self.rows.append(row)


def test_two_dispatches_send_different_questions_and_grade_bound_cases(tmp_path, monkeypatch):
    monkeypatch.setattr(walker, "judge_client_from_env", FixtureJudge)
    store = FormStore(tmp_path / "private.sqlite")
    runners = []
    for run_id in ("run-a", "run-b"):
        cases, summary = reserve_starter_cases(scan_id=run_id, tenant_id="t", agent_id="a", store=store)
        runner = FixtureRunner(cases)
        rows = list(walker.customer_agent_probe_run_fn(runner))
        assert len(rows) == len(runner.prompts) == len(runner.rows) == 100
        assert runner.prompts == [c.prompt for c in cases]
        assert Counter(r["family"] for r in runner.rows) == {f: 10 for f in {c.family_id for c in cases}}
        assert summary["test_count"] == 100
        assert all("starter-100@4" in row["evidence_uri"] for _, row in rows)
        assert len(runner.progress_events) == 300
        assert [event["state"] for event in runner.progress_events[:3]] == [
            "executing", "evaluating", "finished"
        ]
        assert all(set(event) == {"case_id", "family", "state"} for event in runner.progress_events)
        runners.append(runner)
    assert not set(runners[0].prompts) & set(runners[1].prompts)


def test_variable_pack_result_rows_keep_only_redacted_sent_exchange():
    from harness.customer_runner import _CustomerAgentHttpRunner

    runner = object.__new__(_CustomerAgentHttpRunner)
    runner.pack_id = "starter-100@4"
    runner.starter_form_summary = {"form_id": "private-form"}
    runner._result_rows_enabled = True
    runner._pending_exchange = (
        "private attack prompt with credential-value",
        "private target response with credential-value" + "x" * 4_100,
    )
    runner._starter_rows = []
    runner._redaction_values = ("credential-value",)
    runner.record_starter_verdict(test_id="case", family="prompt_injection", verdict="failed", reason="An injected instruction was followed.")
    row, = runner.starter_result_rows()
    assert row["prompt"] == "private attack prompt with [REDACTED]"
    assert "credential-value" not in row["reply"]
    assert len(row["reply"]) == 4_000 and row["reply"].endswith("[TRUNCATED]")
    assert row["form_id"] == "private-form"
    assert starter_pack_id_for_rows([row]) == "starter-100@4"


def test_progress_counts_mixed_transport_errors_as_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(walker, "judge_client_from_env", FixtureJudge)
    cases, _ = reserve_starter_cases(
        scan_id="mixed", tenant_id="t", agent_id="a",
        store=FormStore(tmp_path / "forms.sqlite"),
    )
    runner = FixtureRunner(cases)
    calls = 0

    def complete(prompt):
        nonlocal calls
        calls += 1
        if calls % 2 == 0:
            raise RunnerError("target unavailable")
        return "successful but potentially risky reply"

    runner.complete = complete
    assert len(list(walker.customer_agent_probe_run_fn(runner))) == 100
    terminal = [event for event in runner.progress_events if event["state"] in {"finished", "failed"}]
    assert Counter(event["state"] for event in terminal) == {"finished": 50, "failed": 50}
    assert all(event.get("error_category") == "transport" for event in terminal if event["state"] == "failed")


def test_progress_closes_judge_exception_as_evaluation_failure(tmp_path, monkeypatch):
    class BrokenJudge:
        def complete(self, **kwargs):
            raise RuntimeError("judge unavailable")

    monkeypatch.setattr(walker, "judge_client_from_env", BrokenJudge)
    cases, _ = reserve_starter_cases(
        scan_id="judge-error", tenant_id="t", agent_id="a",
        store=FormStore(tmp_path / "forms.sqlite"),
    )
    runner = FixtureRunner(cases)
    list(walker.customer_agent_probe_run_fn(runner))
    assert [event["state"] for event in runner.progress_events[:3]] == [
        "executing", "evaluating", "failed"
    ]
    assert runner.progress_events[2]["error_category"] == "evaluation"


def test_progress_closes_pre_case_deadline_as_timeout(tmp_path, monkeypatch):
    cases, _ = reserve_starter_cases(
        scan_id="deadline", tenant_id="t", agent_id="a",
        store=FormStore(tmp_path / "forms.sqlite"),
    )
    runner = FixtureRunner(cases)
    runner.operational_scan_deadline_seconds = 1
    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(walker.time, "monotonic", lambda: next(ticks))
    with pytest.raises(ProbeRunIncompleteError, match="deadline exceeded"):
        list(walker.customer_agent_probe_run_fn(runner))
    assert runner.progress_events[-1]["state"] == "failed"
    assert runner.progress_events[-1]["error_category"] == "timeout"


def test_ordinary_v4_needs_no_controlled_budget_configuration(tmp_path, monkeypatch):
    """Ordinary saved forms retain the no-argument judge factory contract."""
    monkeypatch.delenv("SCANNER_EVALUATOR_BUDGET_DB", raising=False)
    constructed = []

    def ordinary_judge():
        constructed.append(True)
        return FixtureJudge()

    monkeypatch.setattr(walker, "judge_client_from_env", ordinary_judge)
    cases, _ = reserve_starter_cases(
        scan_id="ordinary-no-budget", tenant_id="t", agent_id="a",
        store=FormStore(tmp_path / "forms.sqlite"),
    )
    runner = FixtureRunner(cases)
    assert not hasattr(runner, "evaluator_max_cost_usd")
    assert not hasattr(runner, "evaluator_run_id")
    assert len(list(walker.customer_agent_probe_run_fn(runner))) == 100
    assert len(runner.prompts) == len(runner.rows) == 100
    assert constructed == [True]
