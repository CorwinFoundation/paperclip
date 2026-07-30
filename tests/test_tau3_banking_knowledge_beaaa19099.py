from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from evals.perils.money_movement import tau3_banking_knowledge as pilot


@pytest.fixture(scope="module")
def upstream(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("tau2") / "repo"
    subprocess.run(
        ["git", "clone", "--quiet", "--depth=1", "--branch", pilot.UPSTREAM_TAG,
         pilot.UPSTREAM_REPOSITORY, str(path)],
        check=True,
    )
    return path


def test_pinned_twenty_cases_keep_both_identities(upstream: Path) -> None:
    cases = pilot.load_pinned_cases(upstream)
    assert len(cases) == 20
    assert tuple(case.upstream_task_id for case in cases) == pilot.PILOT_CASE_IDS
    assert len({case.local_case_id for case in cases}) == 20
    assert all(case.local_case_id != case.upstream_task_id for case in cases)
    for case in cases:
        transitions, final_state = pilot.replay_database(
            case, case.task["evaluation_criteria"]["actions"]
        )
        assert len(transitions) == len(case.task["evaluation_criteria"]["actions"])
        assert pilot.database_state_hash(final_state)


def test_disabled_manifest_restores_empty_extension(upstream: Path) -> None:
    assert pilot.load_pinned_cases(upstream, enabled=False) == ()
    manifest = json.loads(
        (Path(pilot.__file__).with_name("tau3_banking_knowledge_manifest.json")).read_text()
    )
    assert manifest["enabled"] is False
    assert manifest["scoring_activation"] is False


def test_deterministic_action_and_database_evidence_has_no_judge(upstream: Path) -> None:
    case = pilot.load_pinned_cases(upstream)[0]
    expected_actions = case.task["evaluation_criteria"]["actions"]
    expected_transitions = pilot.expected_database_transitions(case)
    expected_state = pilot.expected_final_database_state(case)
    trajectory = pilot.BankingKnowledgeTrajectory(
        local_case_id=case.local_case_id,
        upstream_task_id=case.upstream_task_id,
        seed=0,
        ordered_actions=expected_actions,
        database_transitions=expected_transitions,
        final_database_state=expected_state,
        termination_reason="task_complete",
    )
    row = pilot.evaluate_trajectory(case, trajectory)
    assert row["passed"] is True
    assert row["ordered_actions"] == expected_actions
    assert row["database_transitions"]
    assert row["termination_reason"] == "task_complete"
    assert row["transition_match"] is True
    assert row["database_match"] is True
    assert row["termination_match"] is True
    assert "judge" not in row
    assert pilot.pass_k([row] * 5)["pass_k"] is True


def _valid_trajectory(case: pilot.BankingKnowledgeCase) -> pilot.BankingKnowledgeTrajectory:
    transitions, final_state = pilot.replay_database(
        case, case.task["evaluation_criteria"]["actions"]
    )
    return pilot.BankingKnowledgeTrajectory(
        local_case_id=case.local_case_id,
        upstream_task_id=case.upstream_task_id,
        seed=0,
        ordered_actions=case.task["evaluation_criteria"]["actions"],
        database_transitions=transitions,
        final_database_state=final_state,
        termination_reason="task_complete",
    )


def test_corrupted_database_transition_fails_closed(upstream: Path) -> None:
    case = pilot.load_pinned_cases(upstream)[0]
    valid = _valid_trajectory(case)
    corrupted = list(valid.database_transitions)
    corrupted[0] = {**corrupted[0], "action_id": "corrupted-action"}
    row = pilot.evaluate_trajectory(
        case,
        pilot.BankingKnowledgeTrajectory(
            **{**valid.__dict__, "database_transitions": tuple(corrupted)}
        ),
    )
    assert row["transition_match"] is False
    assert row["passed"] is False


def test_corrupted_final_database_state_fails_closed(upstream: Path) -> None:
    case = pilot.load_pinned_cases(upstream)[0]
    valid = _valid_trajectory(case)
    row = pilot.evaluate_trajectory(
        case,
        pilot.BankingKnowledgeTrajectory(
            **{**valid.__dict__, "final_database_state": case.initial_database_state}
        ),
    )
    assert row["database_match"] is False
    assert row["passed"] is False


def test_unchanged_actual_database_fails_when_mutation_required(upstream: Path) -> None:
    case = pilot.load_pinned_cases(upstream)[0]
    actions = case.task["evaluation_criteria"]["actions"]
    transitions, _ = pilot.replay_database(case, actions)
    row = pilot.evaluate_trajectory(
        case,
        pilot.BankingKnowledgeTrajectory(
            local_case_id=case.local_case_id,
            upstream_task_id=case.upstream_task_id,
            seed=0,
            ordered_actions=actions,
            database_transitions=transitions,
            final_database_state=case.initial_database_state,
            termination_reason="task_complete",
        ),
    )
    assert row["replay_match"] is False
    assert row["database_match"] is False
    assert row["passed"] is False


def test_db_equivalent_alternate_trajectory_passes(upstream: Path) -> None:
    case = pilot.load_pinned_cases(upstream)[0]
    expected_actions = case.task["evaluation_criteria"]["actions"]
    alternate_actions = [
        {
            "name": "transfer_to_human_agents",
            "arguments": {"reason": "other", "summary": "non-mutating prelude"},
            "requestor": "agent",
            "action_id": "alternate-noop",
        },
        *expected_actions,
    ]
    transitions, final_state = pilot.replay_database(case, alternate_actions)
    row = pilot.evaluate_trajectory(
        case,
        pilot.BankingKnowledgeTrajectory(
            local_case_id=case.local_case_id,
            upstream_task_id=case.upstream_task_id,
            seed=0,
            ordered_actions=alternate_actions,
            database_transitions=transitions,
            final_database_state=final_state,
            termination_reason="task_complete",
        ),
    )
    assert row["action_match"] is False
    assert row["database_match"] is True
    assert row["passed"] is True


def test_crashed_termination_fails_closed(upstream: Path) -> None:
    case = pilot.load_pinned_cases(upstream)[0]
    valid = _valid_trajectory(case)
    row = pilot.evaluate_trajectory(
        case,
        pilot.BankingKnowledgeTrajectory(
            **{**valid.__dict__, "termination_reason": "agent_crashed"}
        ),
    )
    assert row["termination_match"] is False
    assert row["passed"] is False


def test_manifest_deduplication_fails_closed(upstream: Path) -> None:
    cases = pilot.load_pinned_cases(upstream)
    pilot.assert_manifest_deduplicated(cases, {"tau-live-transfer-001"})
    with pytest.raises(ValueError, match="duplicate transaction-correctness"):
        pilot.assert_manifest_deduplicated(cases, {cases[0].upstream_task_id})
