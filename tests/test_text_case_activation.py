"""Execution proof for the materialized text-compatible registry rows."""
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
        for row in build_registry(include_content_candidates=False)
        if row["test_id"] in TEXT_COMPATIBLE_TEST_IDS
        and row["status"] == "active"
        and row["target_bound"] is True
    ]


def test_registry_binds_text_rows_without_bypassing_evidence_gate():
    rows = build_registry(include_content_candidates=False)
    text_rows = _text_rows()

    assert len(text_rows) == 113
    assert count_active_executable(rows) == 340
    assert Counter(row["production_runner_executable"] for row in text_rows) == {
        True: 90,
        False: 23,
    }
    assert Counter(row["execution_evidenced"] for row in text_rows) == {
        True: 90,
        False: 23,
    }
    assert Counter(row["execution_evidence_status"] for row in text_rows) == {
        "promoted_runtime_identity_bound": 82,
        "duplicate_runtime_identity_alias": 23,
        "target_bound_and_evidenced": 8,
    }
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


def test_all_text_rows_call_target_and_judge_and_write_provenance(tmp_path: Path):
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
    job = SimpleNamespace(job_id="job-text-current", agent_id="agent-demo", seed=42)

    results = [executor(row, job) for row in _text_rows()]

    assert len(results) == 113
    assert all(result.status == "complete" for result in results)
    assert all(result.verdict == "pass" for result in results)
    assert sum(result.target_calls for result in results) == 136
    assert sum(result.judge_calls for result in results) == 113

    evidence_files = list((tmp_path / "job-text-current").glob("*/*.json"))
    assert len(evidence_files) == 113
    for evidence_file in evidence_files:
        evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
        assert evidence["payloads"]
        assert evidence["responses"]
        if "framework_worker_id" in evidence:
            assert evidence["framework_worker_id"] in {
                "promptfoo",
                "garak",
                "inspect",
                "pyrit",
            }
        assert evidence.get("execution_mode") in {
            None,
            "materialized_framework_compatible_case",
            "alias_not_executed",
        }
        source_mode = evidence.get("source_mode")
        if source_mode is not None:
            assert source_mode in {
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

    assert result.status == "failed"
    assert result.verdict == "unresolved"
    assert result.target_calls == 1
    assert result.judge_calls == 1
