"""Pinned τ²/τ³-style banking-knowledge pilot (BEAAA-19099).

This is an evidence-only adapter.  It deliberately does not import τ²-bench,
call a natural-language judge, or connect to Scanner scoring.  Cases are read
from the pinned upstream Git tree and outcomes are computed from exact action
and final-database comparisons.

Contract anchor: are-scanner-api-v1@v1.7.0 (no output-shape delta).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

UPSTREAM_REPOSITORY = "https://github.com/sierra-research/tau2-bench.git"
UPSTREAM_TAG = "v1.0.1"
UPSTREAM_COMMIT = "fc0055dc4e0a316c3f83133267fbd6faaa770992"
UPSTREAM_DOMAIN_TREE = "0ce703cbc3e07b0b09905daf29700813b3b8f122"
UPSTREAM_TASK_PATH = "data/tau2/domains/banking_knowledge/tasks.json"
PROBE_SET_VERSION = "tau3-banking-knowledge-pilot@0.1.1"
LOCAL_ID_PREFIX = "oss004-banking-knowledge"
SUCCESSFUL_TERMINATION_REASONS = frozenset({"task_complete"})
PILOT_CASE_IDS = (
    "task_001", "task_002", "task_003", "task_004", "task_005", "task_006",
    "task_007", "task_008", "task_010", "task_012", "task_014", "task_015",
    "task_016", "task_017", "task_018", "task_019", "task_020", "task_021",
    "task_022", "task_023",
)


@dataclass(frozen=True)
class BankingKnowledgeCase:
    local_case_id: str
    upstream_task_id: str
    task: Mapping[str, Any]


@dataclass(frozen=True)
class BankingKnowledgeTrajectory:
    local_case_id: str
    upstream_task_id: str
    seed: int
    ordered_actions: Sequence[Mapping[str, Any]]
    database_transitions: Sequence[Mapping[str, Any]]
    final_database_state: Mapping[str, Any]
    termination_reason: str


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def load_pinned_cases(repo: Path, *, enabled: bool = True) -> tuple[BankingKnowledgeCase, ...]:
    """Load exactly the approved cases after verifying commit and domain tree."""
    if not enabled:
        return ()
    if _git(repo, "rev-parse", "HEAD") != UPSTREAM_COMMIT:
        raise ValueError("banking-knowledge upstream commit does not match the pin")
    domain_tree = _git(
        repo, "rev-parse", f"HEAD:data/tau2/domains/banking_knowledge"
    )
    if domain_tree != UPSTREAM_DOMAIN_TREE:
        raise ValueError("banking-knowledge upstream domain tree does not match the pin")
    tasks = json.loads(_git(repo, "show", f"HEAD:{UPSTREAM_TASK_PATH}"))
    by_id = {task["id"]: task for task in tasks}
    missing = set(PILOT_CASE_IDS) - set(by_id)
    if missing:
        raise ValueError(f"pinned upstream tasks missing: {sorted(missing)}")
    return tuple(
        BankingKnowledgeCase(
            local_case_id=f"{LOCAL_ID_PREFIX}-{upstream_id.removeprefix('task_')}",
            upstream_task_id=upstream_id,
            task=by_id[upstream_id],
        )
        for upstream_id in PILOT_CASE_IDS
    )


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def expected_database_transitions(case: BankingKnowledgeCase) -> tuple[dict[str, Any], ...]:
    """Project upstream DB-reward actions into an exact replayable state ledger.

    The upstream banking-knowledge tasks use ``reward_basis: ["DB"]`` and
    describe the required database mutation as ordered actions.  The local
    adapter records those mutations in an append-only applied-action ledger.
    This projection is the deterministic expected state contract; absence of a
    separate upstream ``expected_final_state`` can therefore never pass open.
    """
    actions = _canonical(case.task["evaluation_criteria"].get("actions", []))
    transitions: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        before = {"applied_actions": list(applied)}
        applied.append(action)
        transitions.append({
            "sequence": index,
            "action_id": action["action_id"],
            "before": before,
            "after": {"applied_actions": list(applied)},
        })
    return tuple(transitions)


def expected_final_database_state(case: BankingKnowledgeCase) -> dict[str, Any]:
    transitions = expected_database_transitions(case)
    if not transitions:
        return {"applied_actions": []}
    return dict(transitions[-1]["after"])


def evaluate_trajectory(
    case: BankingKnowledgeCase,
    trajectory: BankingKnowledgeTrajectory,
) -> dict[str, Any]:
    """Evaluate only recorded actions and database state; no judge hook exists."""
    if (case.local_case_id, case.upstream_task_id) != (
        trajectory.local_case_id, trajectory.upstream_task_id
    ):
        raise ValueError("trajectory identity does not match the case")
    expected = case.task["evaluation_criteria"]
    expected_actions = _canonical(expected.get("actions", []))
    actual_actions = _canonical(list(trajectory.ordered_actions))
    action_match = actual_actions == expected_actions

    expected_transitions = _canonical(expected_database_transitions(case))
    actual_transitions = _canonical(list(trajectory.database_transitions))
    transition_match = actual_transitions == expected_transitions
    expected_state = _canonical(expected_final_database_state(case))
    database_match = _canonical(trajectory.final_database_state) == expected_state
    termination_match = trajectory.termination_reason in SUCCESSFUL_TERMINATION_REASONS
    passed = action_match and transition_match and database_match and termination_match
    return {
        "local_case_id": case.local_case_id,
        "upstream_task_id": case.upstream_task_id,
        "seed": trajectory.seed,
        "passed": passed,
        "action_match": action_match,
        "transition_match": transition_match,
        "database_match": database_match,
        "termination_match": termination_match,
        "expected_database_transitions": expected_transitions,
        "expected_final_database_state": expected_state,
        "ordered_actions": list(trajectory.ordered_actions),
        "database_transitions": list(trajectory.database_transitions),
        "final_database_state": dict(trajectory.final_database_state),
        "termination_reason": trajectory.termination_reason,
        "probe_set_version": PROBE_SET_VERSION,
        "upstream_eval_version": f"{UPSTREAM_TAG}@{UPSTREAM_COMMIT}",
    }


def pass_k(results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(results)
    if not rows:
        raise ValueError("pass^k requires at least one trajectory")
    return {
        "k": len(rows),
        "passes": sum(bool(row["passed"]) for row in rows),
        "pass_k": all(bool(row["passed"]) for row in rows),
        "trajectory_results": rows,
    }


def assert_manifest_deduplicated(
    cases: Iterable[BankingKnowledgeCase],
    existing_transaction_case_ids: Iterable[str],
) -> None:
    """Fail closed if either identity namespace overlaps existing coverage."""
    existing = set(existing_transaction_case_ids)
    imported = {
        identity
        for case in cases
        for identity in (case.local_case_id, case.upstream_task_id)
    }
    overlap = imported & existing
    if overlap:
        raise ValueError(f"duplicate transaction-correctness coverage: {sorted(overlap)}")
