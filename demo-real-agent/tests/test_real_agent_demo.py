from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time


DEMO_DIR = Path(__file__).resolve().parents[1]
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))

from app import JobManager  # noqa: E402
from harness.runner import MockRunner  # noqa: E402
from real_scan import RealScanConfig, execute_real_scan, registry_inventory  # noqa: E402


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
    return MockRunner("authenticated-test-agent", lambda _prompt: "Example answer.\nCONFIDENCE: 0.90")


def _judge():
    return MockRunner("real-judge-contract-test", lambda _prompt: JUDGE_OUTPUT)


def test_registry_inventory_is_exactly_the_real_90_case_plan() -> None:
    inventory = registry_inventory()
    assert inventory["active_case_count"] == 90
    assert inventory["published_case_count"] == 90
    assert inventory["planned_target_calls"] == 110
    assert inventory["planned_judge_calls"] == 90
    assert {family["family_id"]: family["cases"] for family in inventory["families"]} == {
        "p1_1_factuality_golden": 30,
        "p1_3_consistency_golden": 20,
        "p1_4_refusal_golden": 20,
        "p1_5_calibration_golden": 20,
    }


def test_real_scan_dispatches_all_90_and_reconciles_signed_evidence(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "test-only-signing-key")
    events: list[dict] = []

    def emit(kind: str, message: str, **details) -> None:
        events.append({"kind": kind, "message": message, **details})

    result = execute_real_scan(
        tmp_path / "run",
        "run-real-90",
        emit,
        config=RealScanConfig(
            agent_endpoint="https://agent.example.test",
            agent_secret="test-secret",
        ),
        subject_factory=_subject,
        judge_factory=_judge,
    )

    assert result["active_case_count"] == 90
    assert len(result["cases"]) == 90
    assert result["counts"]["expected"] == 90
    assert result["counts"]["evidence_rows"] == 90
    assert result["reconciliation"]["attempted"] == 90
    assert result["reconciliation"]["terminal"] == 90
    assert result["budget"]["target_calls"] == 110
    assert result["budget"]["judge_calls"] == 90
    assert result["budget"]["estimated_judge_cost_usd"] <= 100.0
    assert result["score"] is None
    assert len([event for event in events if event["kind"] == "case_result"]) == 90
    assert result["manifest_hash"].startswith("sha256:")
    manifest_path = next((tmp_path / "run" / "evidence").glob("*/manifest.json"))
    serialized = manifest_path.read_text(encoding="utf-8")
    assert "test-secret" not in serialized
    assert "authenticated-test-agent" in serialized


def test_behavioral_failures_complete_the_scan_instead_of_crashing(monkeypatch, tmp_path) -> None:
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


def test_budget_exhaustion_is_visible_as_incomplete_evidence(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "test-only-signing-key")
    result = execute_real_scan(
        tmp_path / "run",
        "run-budget",
        lambda *_args, **_kwargs: None,
        config=RealScanConfig(
            agent_endpoint="https://agent.example.test",
            agent_secret="test-secret",
            max_target_calls=1,
            max_judge_calls=90,
        ),
        subject_factory=_subject,
        judge_factory=_judge,
    )
    assert result["incomplete"] is True
    assert result["analysis_status"] == "analysis_incomplete"
    assert result["reconciliation"]["terminal"] == 90
    assert result["budget"]["target_calls"] == 1
    assert any(
        case["incomplete_reason"] == "budget_exceeded:max_target_calls"
        for case in result["cases"]
    )


def test_job_manager_reports_complete_analysis_with_fail_rows() -> None:
    def fake_runner(output_dir: Path, run_id: str, emit):
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

    with tempfile.TemporaryDirectory(prefix="real-agent-demo-") as tmp:
        manager = JobManager(Path(tmp), scan_runner=fake_runner)
        job = manager.start()
        deadline = time.monotonic() + 3
        while job.status in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.01)
        snapshot = job.snapshot()
        assert snapshot["status"] == "complete"
        assert snapshot["result"]["scan"]["verdict_counts"] == {"fail": 1}
        assert snapshot["error"] is None


def test_public_result_shape_contains_no_credential_fields(monkeypatch, tmp_path) -> None:
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
