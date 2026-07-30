"""Pinned τ²/τ³-style banking-knowledge pilot (BEAAA-19099).

This is an evidence-only adapter.  It deliberately does not import τ²-bench,
call a natural-language judge, or connect to Scanner scoring.  Cases are read
from the pinned upstream Git tree and outcomes are computed from exact action
and final-database comparisons.

Contract anchor: are-scanner-api-v1@v1.7.0 (no output-shape delta).
"""
from __future__ import annotations

import json
import copy
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

UPSTREAM_REPOSITORY = "https://github.com/sierra-research/tau2-bench.git"
UPSTREAM_TAG = "v1.0.1"
UPSTREAM_COMMIT = "fc0055dc4e0a316c3f83133267fbd6faaa770992"
UPSTREAM_DOMAIN_TREE = "0ce703cbc3e07b0b09905daf29700813b3b8f122"
UPSTREAM_TASK_PATH = "data/tau2/domains/banking_knowledge/tasks.json"
UPSTREAM_DB_PATH = "data/tau2/domains/banking_knowledge/db.json"
PROBE_SET_VERSION = "tau3-banking-knowledge-pilot@0.1.2"
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
    initial_database_state: Mapping[str, Any]


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
    initial_database_state = json.loads(_git(repo, "show", f"HEAD:{UPSTREAM_DB_PATH}"))
    by_id = {task["id"]: task for task in tasks}
    missing = set(PILOT_CASE_IDS) - set(by_id)
    if missing:
        raise ValueError(f"pinned upstream tasks missing: {sorted(missing)}")
    return tuple(
        BankingKnowledgeCase(
            local_case_id=f"{LOCAL_ID_PREFIX}-{upstream_id.removeprefix('task_')}",
            upstream_task_id=upstream_id,
            task=by_id[upstream_id],
            initial_database_state=initial_database_state,
        )
        for upstream_id in PILOT_CASE_IDS
    )


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def database_state_hash(state: Mapping[str, Any]) -> str:
    payload = json.dumps(_canonical(state), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _deterministic_id(seed: str, length: int = 16) -> str:
    return hashlib.sha256(seed.encode()).digest()[: length // 2].hex()


def _table(state: dict[str, Any], name: str) -> dict[str, Any]:
    return state.setdefault(name, {"data": {}, "notes": ""}).setdefault("data", {})


def _apply_action(state: dict[str, Any], action: Mapping[str, Any]) -> None:
    """Replay the selected upstream banking mutations without importing τ²."""
    name = str(action["name"])
    args = dict(action.get("arguments", {}))
    if name == "transfer_to_human_agents":
        return  # upstream action is generic and does not mutate TransactionalDB
    if name == "apply_for_credit_card":
        income = float(args["annual_income"])
        seed = (
            f"credit_card:{args['card_type']}:{args['customer_name']}:"
            f"{income}:{args.get('rho_bank_subscription', False)}"
        )
        record_id = _deterministic_id(seed)
        _table(state, "credit_card_applications")[record_id] = {
            "application_id": record_id,
            "card_type": args["card_type"],
            "customer_name": args["customer_name"],
            "annual_income": income,
            "rho_bank_subscription": args.get("rho_bank_subscription", False),
            "status": "PENDING",
            "date": "11/14/2025",
        }
        return
    if name == "change_user_email":
        _table(state, "users")[args["user_id"]]["email"] = args["new_email"]
        return
    if name == "log_verification":
        record_id = (
            f"{args['user_id']}_"
            f"{args['time_verified'].replace(' ', '_').replace(':', '').replace('-', '')}"
        )
        _table(state, "verification_history")[record_id] = args
        return
    if name == "submit_referral":
        record_id = _deterministic_id(
            f"referral:{args['user_id']}:{args['account_type']}"
        )
        _table(state, "referrals")[record_id] = {
            "referral_id": record_id,
            "referrer_id": args["user_id"],
            "referred_account_type": args["account_type"],
            "referral_status": "NO_PROGRESS",
            "date": "11/14/2025",
        }
        return
    if name == "submit_transaction":
        amount = float(args["amount"])
        seed = (
            f"transaction:{args['user_id']}:{args['credit_card_type']}:"
            f"{args['merchant_name']}:{amount:.2f}:{args['category']}"
        )
        record_id = f"txn_{_deterministic_id(seed, 12)}"
        _table(state, "credit_card_transaction_history")[record_id] = {
            "transaction_id": record_id,
            "user_id": args["user_id"],
            "credit_card_type": args["credit_card_type"],
            "merchant_name": args["merchant_name"],
            "transaction_amount": f"${amount:.2f}",
            "transaction_date": "11/14/2025",
            "category": args["category"],
            "status": "COMPLETED",
        }
        return
    if name == "give_discoverable_user_tool":
        tool_name = args["discoverable_tool_name"]
        record_id = _deterministic_id(f"user_discoverable_tool:{tool_name}")
        _table(state, "user_discoverable_tools")[record_id] = {
            "tool_name": tool_name,
            "status": "GIVEN",
        }
        return
    if name == "call_discoverable_user_tool":
        tool_name = args["discoverable_tool_name"]
        parsed = json.loads(args.get("arguments", "{}"), parse_int=float)
        seed = f"user_discoverable_tool_call:{tool_name}:{json.dumps(parsed, sort_keys=True)}"
        record_id = _deterministic_id(seed)
        _table(state, "user_discoverable_tool_calls")[record_id] = {
            "tool_name": tool_name,
            "arguments": parsed,
            "status": "CALLED",
        }
        return
    raise ValueError(f"unsupported pinned banking action: {name}")


def replay_database(
    case: BankingKnowledgeCase,
    actions: Sequence[Mapping[str, Any]],
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    """Replay actions against a copy of the pinned upstream transactional DB.

    Transitions bind each recorded action to independent before/after hashes of
    the actual banking state. Equivalent action trajectories may differ while
    still converging on the same final database hash.
    """
    state = copy.deepcopy(dict(case.initial_database_state))
    transitions: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        before_hash = database_state_hash(state)
        _apply_action(state, action)
        transitions.append({
            "sequence": index,
            "action_id": action["action_id"],
            "before_state_sha256": before_hash,
            "after_state_sha256": database_state_hash(state),
        })
    return tuple(transitions), state


def expected_database_transitions(case: BankingKnowledgeCase) -> tuple[dict[str, Any], ...]:
    transitions, _ = replay_database(
        case, case.task["evaluation_criteria"].get("actions", [])
    )
    return transitions


def expected_final_database_state(case: BankingKnowledgeCase) -> dict[str, Any]:
    _, state = replay_database(case, case.task["evaluation_criteria"].get("actions", []))
    return state


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
    replayed_transitions, replayed_actual_state = replay_database(
        case, trajectory.ordered_actions
    )
    transition_match = actual_transitions == _canonical(replayed_transitions)
    expected_state = _canonical(expected_final_database_state(case))
    actual_state = _canonical(trajectory.final_database_state)
    replay_match = actual_state == _canonical(replayed_actual_state)
    database_match = database_state_hash(actual_state) == database_state_hash(expected_state)
    termination_match = trajectory.termination_reason in SUCCESSFUL_TERMINATION_REASONS
    passed = transition_match and replay_match and database_match and termination_match
    return {
        "local_case_id": case.local_case_id,
        "upstream_task_id": case.upstream_task_id,
        "seed": trajectory.seed,
        "passed": passed,
        "action_match": action_match,
        "transition_match": transition_match,
        "replay_match": replay_match,
        "database_match": database_match,
        "expected_database_sha256": database_state_hash(expected_state),
        "actual_database_sha256": database_state_hash(actual_state),
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
