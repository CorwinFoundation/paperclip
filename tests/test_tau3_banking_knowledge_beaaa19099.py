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
    trajectory = pilot.BankingKnowledgeTrajectory(
        local_case_id=case.local_case_id,
        upstream_task_id=case.upstream_task_id,
        seed=0,
        ordered_actions=expected_actions,
        database_transitions=({"operation": "apply", "before": {}, "after": {"ok": True}},),
        final_database_state={"ok": True},
        termination_reason="task_complete",
    )
    row = pilot.evaluate_trajectory(case, trajectory)
    assert row["passed"] is True
    assert row["ordered_actions"] == expected_actions
    assert row["database_transitions"]
    assert row["termination_reason"] == "task_complete"
    assert "judge" not in row
    assert pilot.pass_k([row] * 5)["pass_k"] is True


def test_manifest_deduplication_fails_closed(upstream: Path) -> None:
    cases = pilot.load_pinned_cases(upstream)
    pilot.assert_manifest_deduplicated(cases, {"tau-live-transfer-001"})
    with pytest.raises(ValueError, match="duplicate transaction-correctness"):
        pilot.assert_manifest_deduplicated(cases, {cases[0].upstream_task_id})
