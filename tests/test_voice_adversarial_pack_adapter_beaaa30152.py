"""BEAAA-30152: bind every active voice-adversarial row to live dispatch."""
from __future__ import annotations

from collections import Counter

from harness.runner import MockRunner
from tools.active_test_registry import build_registry, count_active_executable
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _rows() -> list[dict]:
    return [row for row in build_registry() if row.get("status") == "active" and row["test_id"] == "voice_adversarial_pack"]


def test_exact_voice_adversarial_roster_is_executable() -> None:
    rows = _rows()
    assert len(rows) == 24
    assert Counter(row["runner"] for row in rows) == {"bench_wrapper": 12, "content_candidate_worker": 12}
    assert all(row["production_runner_executable"] is True for row in rows)
    assert count_active_executable(build_registry()) == 2_341


def test_live_dispatch_records_24_verdicts_and_target_calls(tmp_path) -> None:
    calls: list[str] = []

    def target(prompt: str) -> str:
        calls.append(prompt)
        return "I will follow only the authenticated user's authorized intent."

    executor = RegisteredCaseExecutor(
        subject=MockRunner("authorized-reference-target", target),
        judge=MockRunner("judge", lambda _prompt: '{"verdict":"pass","score":1.0}'),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )
    outcomes = [executor._execute_case(row["test_id"], row["case_id"], row) for row in _rows()]

    assert len(outcomes) == len(calls) == 24
    assert Counter(outcome["verdict"] for outcome in outcomes) == {"pass": 24}
    assert all(outcome["payloads"] and outcome["responses"] for outcome in outcomes)
