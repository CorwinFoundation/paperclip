from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time


DEMO_DIR = Path(__file__).resolve().parents[1]
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))

from app import EvaluationBudgetConsumed, JobManager  # noqa: E402
from harness.runner import MockRunner  # noqa: E402
from tools.active_test_registry import load_published_count  # noqa: E402
from real_scan import (  # noqa: E402
    EXPECTED_FAMILY_COUNTS,
    PLANNED_JUDGE_CALLS,
    PLANNED_TARGET_CALLS,
    RealScanConfig,
    execute_real_scan,
    registry_inventory,
)


JUDGE_OUTPUT = """\
ANSWER_MATCH: YES
PLAUSIBILITY: YES
FALSE_SPECIFICITY: NO
EVIDENCE_SPAN: example
CONSISTENCY: MATCH
ACTION: ANSWERED
CORRECTNESS: CORRECT
FAILURE_MODE: none
EVIDENCE_SPAN_A: example
EVIDENCE_SPAN_B: example
"""


def _subject(_endpoint: str, secret: str):
    assert secret == "test-secret"
    return MockRunner(
        "authenticated-test-agent",
        lambda _prompt: "Example answer.\nCONFIDENCE: 0.90",
    )


def _judge():
    def complete(prompt: str) -> str:
        if '"verdict":"pass|partial|fail"' in prompt:
            return json.dumps(
                {"verdict": "pass", "reason": "The response satisfies the contract."}
            )
        return JUDGE_OUTPUT

    return MockRunner("real-judge-contract-test", complete)


def test_registry_inventory_is_exactly_the_real_203_case_plan() -> None:
    inventory = registry_inventory()
    assert inventory["runnable_case_count"] == 203
    assert inventory["published_executed_case_count"] == load_published_count()
    assert inventory["evidenced_executable_case_count"] == load_published_count()
    assert inventory["planned_target_calls"] == PLANNED_TARGET_CALLS == 246
    assert inventory["planned_judge_calls"] == PLANNED_JUDGE_CALLS == 203
    assert {
        family["family_id"]: family["cases"] for family in inventory["families"]
    } == EXPECTED_FAMILY_COUNTS


def test_real_scan_dispatches_all_203_and_reconciles_signed_evidence(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "test-only-signing-key")
    events: list[dict] = []

    def emit(kind: str, message: str, **details) -> None:
        events.append({"kind": kind, "message": message, **details})

    result = execute_real_scan(
        tmp_path / "run",
        "run-real-203",
        emit,
        config=RealScanConfig(
            agent_endpoint="https://agent.example.test",
            agent_secret="test-secret",
        ),
        subject_factory=_subject,
        judge_factory=_judge,
    )

    assert result["runnable_case_count"] == 203
    assert len(result["cases"]) == 203
    assert result["counts"]["expected"] == 203
    assert result["counts"]["evidence_rows"] == 203
    assert result["reconciliation"]["attempted"] == 203
    assert result["reconciliation"]["terminal"] == 203
    assert result["budget"]["target_calls"] == 246
    assert result["budget"]["judge_calls"] == 203
    assert result["budget"]["estimated_judge_cost_usd"] <= 100.0
    assert result["score"] is None
    assert len([event for event in events if event["kind"] == "case_result"]) == 203
    assert result["manifest_hash"].startswith("sha256:")
    manifest_path = next((tmp_path / "run" / "evidence").glob("*/manifest.json"))
    serialized = manifest_path.read_text(encoding="utf-8")
    assert "test-secret" not in serialized
    assert "authenticated-test-agent" in serialized


def test_behavioral_failures_complete_the_scan_instead_of_crashing(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "test-only-signing-key")
    result = execute_real_scan(
        tmp_path / "run",
        "run-with-fails",
        lambda *_args, **_kwargs: None,
        config=RealScanConfig(
            agent_endpoint="https://agent.example.test",
            agent_secret="test-secret",
        ),
        subject_factory=_subject,
        judge_factory=_judge,
    )
    assert result["analysis_status"] == "complete"
    assert result["incomplete"] is False
    assert result["verdict_counts"].get("fail", 0) > 0
    assert all(case["execution_status"] == "complete" for case in result["cases"])


def test_budget_exhaustion_is_visible_as_incomplete_evidence(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "test-only-signing-key")
    result = execute_real_scan(
        tmp_path / "run",
        "run-budget",
        lambda *_args, **_kwargs: None,
        config=RealScanConfig(
            agent_endpoint="https://agent.example.test",
            agent_secret="test-secret",
            max_target_calls=1,
            max_judge_calls=203,
        ),
        subject_factory=_subject,
        judge_factory=_judge,
    )
    assert result["incomplete"] is True
    assert result["analysis_status"] == "analysis_incomplete"
    assert result["reconciliation"]["terminal"] == 203
    assert result["budget"]["target_calls"] == 1
    assert any(
        case["incomplete_reason"] == "budget_exceeded:max_target_calls"
        for case in result["cases"]
    )


def _fake_runner(output_dir: Path, run_id: str, emit):
    output_dir.mkdir(parents=True)
    emit(
        "case_result",
        "CASE-1: fail",
        case_id="CASE-1",
        verdict="fail",
        completed=1,
        total=1,
    )
    return {
        "run_id": run_id,
        "incomplete": False,
        "verdict_counts": {"fail": 1},
    }


def test_job_manager_reports_complete_analysis_with_fail_rows() -> None:
    with tempfile.TemporaryDirectory(prefix="real-agent-demo-") as tmp:
        manager = JobManager(Path(tmp), scan_runner=_fake_runner)
        job = manager.start()
        deadline = time.monotonic() + 3
        while job.status in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.01)
        snapshot = job.snapshot()
        assert snapshot["status"] == "complete"
        assert snapshot["result"]["scan"]["verdict_counts"] == {"fail": 1}
        assert snapshot["error"] is None


def test_approved_evaluation_budget_is_one_persisted_run() -> None:
    with tempfile.TemporaryDirectory(prefix="real-agent-demo-budget-") as tmp:
        runs = Path(tmp)
        manager = JobManager(runs, scan_runner=_fake_runner)
        job = manager.start()
        deadline = time.monotonic() + 3
        while job.status in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.01)
        assert manager.evaluation_available() is False
        try:
            manager.start()
        except EvaluationBudgetConsumed:
            pass
        else:
            raise AssertionError("second run should not receive another $100 ceiling")
        restarted = JobManager(runs, scan_runner=_fake_runner)
        assert restarted.evaluation_available() is False
        try:
            restarted.start()
        except EvaluationBudgetConsumed:
            pass
        else:
            raise AssertionError("container restart must not reset evaluation budget")


def test_public_result_shape_contains_no_credential_fields(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "test-only-signing-key")
    result = execute_real_scan(
        tmp_path / "run",
        "run-redaction",
        lambda *_args, **_kwargs: None,
        config=RealScanConfig(
            agent_endpoint="https://agent.example.test/private/path?token=hidden",
            agent_secret="test-secret",
        ),
        subject_factory=_subject,
        judge_factory=_judge,
    )
    payload = json.dumps(result, sort_keys=True)
    assert "test-secret" not in payload
    assert "token=hidden" not in payload
    assert result["target_host"] == "agent.example.test"
    assert not any("secret" in key or "credential" in key for key in result)
