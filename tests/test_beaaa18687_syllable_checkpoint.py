from pathlib import Path

import pytest

from scripts.syllable_checkpoint import (
    CheckpointLedger,
    ExecutionCapReached,
    Invocation,
    run_invocations,
)


def test_atomic_per_invocation_checkpoint_and_resume_without_cached_reuse(
    tmp_path: Path,
) -> None:
    path = tmp_path / "checkpoint.json"
    rows = [Invocation("case-a", 0, {}), Invocation("case-b", 0, {})]
    executed: list[str] = []

    def execute(row, observe):
        executed.append(row.key)
        observe({"phase": "start_session", "attempt": 1})
        return "pass", f"evidence://{row.key}"

    run_invocations(rows[:1], CheckpointLedger(path), execute)
    run_invocations(rows, CheckpointLedger(path), execute)
    assert executed == ["case-a::seed=0", "case-b::seed=0"]
    state = CheckpointLedger(path).state
    assert state["semantic_invocations_started"] == 2
    assert state["semantic_invocations_terminal"] == 2
    assert state["http_attempts"] == 2


def test_nonterminal_invocation_is_reexecuted_not_response_reused(
    tmp_path: Path,
) -> None:
    path = tmp_path / "checkpoint.json"
    row = Invocation("case-a", 0, {})
    ledger = CheckpointLedger(path)
    ledger.start(row)
    ledger.record_http_attempt({"phase": "start_session", "attempt": 1})
    calls = []

    def execute(invocation, observe):
        calls.append(invocation.key)
        observe({"phase": "start_session", "attempt": 1})
        return "pass", "evidence://fresh"

    run_invocations([row], CheckpointLedger(path), execute)
    assert calls == [row.key]
    assert CheckpointLedger(path).state["http_attempts"] == 2


def test_700_attempt_cap_fails_before_attempt_701(tmp_path: Path) -> None:
    ledger = CheckpointLedger(tmp_path / "checkpoint.json")
    ledger.state["http_attempts"] = 700
    with pytest.raises(ExecutionCapReached, match="700 HTTP-attempt cap"):
        ledger.record_http_attempt({"attempt": 701})
    assert ledger.state["http_attempts"] == 700


def test_36_hour_cap_blocks_new_semantic_invocation(tmp_path: Path) -> None:
    now = {"value": 10.0}
    ledger = CheckpointLedger(
        tmp_path / "checkpoint.json", clock=lambda: now["value"]
    )
    now["value"] += 36 * 60 * 60
    with pytest.raises(ExecutionCapReached, match="36-hour"):
        ledger.start(Invocation("case-a", 0, {}))
