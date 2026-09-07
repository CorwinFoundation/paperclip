"""BEAAA-30094: bind every out-of-scope containment row to live dispatch."""
from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

from harness.runner import MockRunner, RunnerError
from tools.active_test_registry import build_registry, count_active_executable
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _rows() -> list[dict]:
    return [
        row for row in build_registry()
        if row.get("status") == "active" and row["test_id"] == "out_of_scope_containment"
    ]


def _job() -> SimpleNamespace:
    return SimpleNamespace(
        job_id="beaaa-30094", agent_id="authorized-reference-target", seed=0,
        scan_id="beaaa-30094", capability_manifest=None,
    )


def test_exact_out_of_scope_roster_is_executable_without_other_family_delta() -> None:
    rows = build_registry()
    scoped = _rows()

    assert len(scoped) == 60
    assert Counter(row["runner"] for row in scoped) == {
        "bench_wrapper": 30,
        "content_candidate_worker": 30,
    }
    assert all(row["production_runner_executable"] is True for row in scoped)
    assert count_active_executable(rows) == 2_342


def test_live_dispatch_reports_mutually_exclusive_outcomes_and_counts(tmp_path) -> None:
    def run(response):
        executor = RegisteredCaseExecutor(
            subject=MockRunner("authorized-reference-target", response),
            judge=MockRunner(
                "judge",
                lambda _prompt: '{"verdict":"pass","score":1.0,"reason":"contained"}',
            ),
            evidence_store=EvidenceStore(tmp_path),
            budget=ScanBudget(max_target_calls=60),
        )
        return executor(_rows()[0], _job())

    passed = run(lambda _prompt: "I can only help with requests inside my assigned scope.")
    failed = run(lambda _prompt: (_ for _ in ()).throw(RunnerError("adapter unavailable")))
    incomplete_executor = RegisteredCaseExecutor(
        subject=MockRunner("authorized-reference-target", lambda _prompt: "unused"),
        judge=MockRunner("judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_target_calls=0),
    )
    incomplete = incomplete_executor(_rows()[1], _job())
    outcomes = [passed, failed, incomplete]

    assert Counter(result.status for result in outcomes) == {
        "complete": 1, "failed": 1, "incomplete": 1,
    }
    assert Counter(result.verdict for result in outcomes) == {"pass": 1, "unresolved": 2}
    assert sum(result.target_calls for result in outcomes) == 2
    assert Counter(result.incomplete_reason for result in outcomes) == {
        None: 1,
        "case_execution_error:RunnerError": 1,
        "budget_exceeded:max_target_calls": 1,
    }
    assert all(
        (result.status == "complete") + (result.status == "failed") + (result.status == "incomplete") == 1
        for result in outcomes
    )
