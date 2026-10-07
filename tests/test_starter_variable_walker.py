"""Actual variable Starter payload dispatch with fixture replies, no API spend."""

from collections import Counter

from scanners import customer_agent_walker as walker
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
        runners.append(runner)
    assert not set(runners[0].prompts) & set(runners[1].prompts)


def test_variable_pack_result_rows_hide_private_prompts_even_when_debug_rows_enabled():
    from harness.customer_runner import _CustomerAgentHttpRunner

    runner = object.__new__(_CustomerAgentHttpRunner)
    runner.pack_id = "starter-100@4"
    runner.starter_form_summary = {"form_id": "private-form"}
    runner._result_rows_enabled = True
    runner._pending_exchange = ("private attack prompt", "private target response")
    runner._starter_rows = []
    runner._redaction_values = ()
    runner.record_starter_verdict(test_id="case", family="prompt_injection", verdict="failed", reason="An injected instruction was followed.")
    row, = runner.starter_result_rows()
    assert "prompt" not in row and "reply" not in row
    assert row["form_id"] == "private-form"
    assert starter_pack_id_for_rows([row]) == "starter-100@4"
