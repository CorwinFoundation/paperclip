"""BEAAA-30123: bind all confirmation rows to live target dispatch."""
from __future__ import annotations

from types import SimpleNamespace

from harness.runner import MockRunner
from tools.active_test_registry import build_registry, count_active_executable
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _rows() -> list[dict]:
    return [
        row for row in build_registry()
        if row.get("status") == "active"
        and row["test_id"] == "consequential_action_confirmation"
    ]


def test_exact_confirmation_roster_is_executable() -> None:
    rows = _rows()
    assert len(rows) == 15
    assert all(row["production_runner_executable"] is True for row in rows)
    assert count_active_executable(build_registry()) == 2_373


def test_live_dispatch_records_one_verdict_and_target_call_per_row(tmp_path) -> None:
    executor = RegisteredCaseExecutor(
        subject=MockRunner(
            "authorized-reference-target",
            lambda prompt: (
                '{"confirmed_before_acting": false, "rationale": "safe direct action"}'
                if any(token in prompt for token in ("draft", "balance", "newsletter", "sandbox", "weather"))
                else '{"confirmed_before_acting": true, "rationale": "confirmation obtained"}'
            ),
        ),
        judge=MockRunner("unused-judge", lambda _prompt: "must not be called"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_target_calls=15),
    )
    job = SimpleNamespace(
        job_id="beaaa-30123", agent_id="authorized-reference-target", seed=0,
        scan_id="beaaa-30123", capability_manifest=None,
    )

    results = [executor(row, job) for row in _rows()]

    assert len(results) == 15
    assert all(result.status == "complete" for result in results)
    assert all(result.verdict in {"PASS", "FAIL"} for result in results)
    assert sum(result.target_calls for result in results) == 15
    assert all(result.incomplete_reason is None for result in results)


def test_out_of_scope_decline_is_terminal_not_applicable(tmp_path) -> None:
    executor = RegisteredCaseExecutor(
        subject=MockRunner("scoped-target", lambda _prompt: "I can't help with that request."),
        judge=MockRunner("unused-judge", lambda _prompt: "must not be called"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_target_calls=1),
    )
    job = SimpleNamespace(job_id="na", agent_id="scoped-target", seed=0, scan_id="na", capability_manifest=None)

    result = executor(_rows()[0], job)

    assert result.status == "complete"
    assert result.verdict == "not_applicable"
    assert result.target_calls == 1
    assert result.judge_calls == 0


def test_fenced_json_fragment_with_missing_opening_brace_is_graded(tmp_path) -> None:
    executor = RegisteredCaseExecutor(
        subject=MockRunner(
            "scoped-target",
            lambda _prompt: (
                '```json\n'
                '  "confirmed_before_acting": false,\n'
                '  "rationale": "No action was taken."\n'
                '}\n'
                '```'
            ),
        ),
        judge=MockRunner("unused-judge", lambda _prompt: "must not be called"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_target_calls=1),
    )
    job = SimpleNamespace(job_id="fragment", agent_id="scoped-target", seed=0, scan_id="fragment", capability_manifest=None)

    result = executor(_rows()[0], job)

    assert result.status == "complete"
    assert result.verdict in {"PASS", "FAIL"}
    assert result.target_calls == 1
    assert result.judge_calls == 0
