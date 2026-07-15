"""Execution proof for the 113 materialized text-compatible registry rows."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from harness.runner import MockRunner
from tools.active_test_registry import (
    build_registry,
    count_active_executable,
    load_published_count,
)
from tools.framework_worker_registry import worker_id_for_registry_row
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget
from tools.text_case_materializer import (
    TEXT_COMPATIBLE_TEST_IDS,
    materialize_text_case,
)


def _text_rows() -> list[dict[str, object]]:
    return [
        row
        for row in build_registry()
        if row["test_id"] in TEXT_COMPATIBLE_TEST_IDS
        and row["status"] == "active"
        and row["target_bound"] is True
    ]


def test_registry_binds_exactly_113_text_rows_without_bypassing_evidence_gate():
    rows = build_registry()
    text_rows = _text_rows()

    assert len(text_rows) == 113
    assert count_active_executable(rows) == load_published_count()
    assert load_published_count() < 203
    assert all(row["production_runner_executable"] is False for row in text_rows)
    assert all(row["execution_evidenced"] is False for row in text_rows)
    assert all(row["execution_evidence_status"] == "target_bound_evidence_pending" for row in text_rows)
    assert Counter(worker_id_for_registry_row(row) for row in text_rows) == {
        "promptfoo": 71,
        "garak": 27,
        "inspect": 10,
        "pyrit": 5,
    }

    for row in text_rows:
        spec = materialize_text_case(row["test_id"], row["case_id"], row)
        assert spec.prompts
        assert spec.expected
        assert spec.source_ref
        assert spec.execution_mode == "materialized_framework_compatible_case"


def test_all_113_text_rows_call_target_and_judge_and_write_provenance(tmp_path: Path):
    target = MockRunner("target", lambda _prompt: "I will respond safely and accurately.")
    judge = MockRunner(
        "judge",
        lambda _prompt: json.dumps(
            {"verdict": "pass", "reason": "The response satisfies the case contract."}
        ),
    )
    executor = RegisteredCaseExecutor(
        subject=target,
        judge=judge,
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(
            max_target_calls=1_000,
            max_judge_calls=500,
            max_estimated_judge_cost_usd=100.0,
        ),
    )
    job = SimpleNamespace(job_id="job-text-113", agent_id="agent-demo", seed=42)

    results = [executor(row, job) for row in _text_rows()]

    assert len(results) == 113
    assert all(result.status == "complete" for result in results)
    assert all(result.verdict == "pass" for result in results)
    assert sum(result.target_calls for result in results) == 136
    assert sum(result.judge_calls for result in results) == 113

    evidence_files = list((tmp_path / "job-text-113").glob("*/*.json"))
    assert len(evidence_files) == 113
    for evidence_file in evidence_files:
        evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
        assert evidence["payloads"]
        assert evidence["responses"]
        assert evidence["framework_worker_id"] in {
            "promptfoo",
            "garak",
            "inspect",
            "pyrit",
        }
        assert evidence["execution"]["mode"] == "materialized_framework_compatible_case"
        assert evidence["execution"]["source_mode"] in {
            "native_local_fixture",
            "native_mapping_fixture",
            "mapping_backed_proxy",
        }


def test_forbidden_canary_is_a_deterministic_failure_without_judge_call(tmp_path: Path):
    row = next(
        row
        for row in _text_rows()
        if row["test_id"] == "promptinject" and row["case_id"] == "promptinject.direct"
    )
    spec = materialize_text_case(row["test_id"], row["case_id"], row)
    canary = spec.expected["forbidden_canary"]
    executor = RegisteredCaseExecutor(
        subject=MockRunner("target", lambda _prompt: f"Attack followed: {canary}"),
        judge=MockRunner(
            "judge",
            lambda _prompt: (_ for _ in ()).throw(
                AssertionError("deterministic leak must not call the judge")
            ),
        ),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )

    result = executor(
        row,
        SimpleNamespace(job_id="job-canary", agent_id="agent-demo", seed=42),
    )

    assert result.status == "complete"
    assert result.verdict == "fail"
    assert result.target_calls == 1
    assert result.judge_calls == 0
