from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time


DEMO_DIR = Path(__file__).resolve().parents[1] / "demo"
sys.path.insert(0, str(DEMO_DIR))

from app import DemoHandler, JobManager, _deployment_metadata  # noqa: E402


def test_deployment_metadata_contains_only_public_build_facts(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_GIT_SHA", "abc123")
    monkeypatch.setenv("DEMO_GITHUB_RUN_ID", "42")
    monkeypatch.setenv("DEMO_GITHUB_REPOSITORY", "CorwinFoundation/are-scanner")
    monkeypatch.setenv("DEMO_GITHUB_RUN_URL", "https://github.com/private/actions/runs/42")
    monkeypatch.setenv("DEMO_GIT_REF", "refs/pull/61/head")
    metadata = _deployment_metadata()
    assert metadata["git_sha"] == "abc123"
    assert metadata["github_run_id"] == "42"
    assert "repository" not in metadata
    assert "git_ref" not in metadata
    assert "github_run_url" not in metadata
    assert all(gate["status"] == "passed" for gate in metadata["gates"])
    assert not any("token" in key.lower() or "secret" in key.lower() for key in metadata)


def test_run_start_requires_demo_token_when_configured(monkeypatch) -> None:
    class HandlerStub:
        headers = {"X-Demo-Access-Token": "wrong"}

        def _request_token(self, parsed) -> str:
            return DemoHandler._request_token(self, parsed)

    monkeypatch.setenv("DEMO_ACCESS_TOKEN", "abcdefghijklmnopqrstuvwxyz123456")
    assert not DemoHandler._authorized_for_run(HandlerStub(), type("Parsed", (), {"query": ""})())
    HandlerStub.headers = {"Authorization": "Bearer abcdefghijklmnopqrstuvwxyz123456"}
    assert DemoHandler._authorized_for_run(HandlerStub(), type("Parsed", (), {"query": ""})())


def test_job_streams_agent_passive_tests_and_cicd() -> None:
    with tempfile.TemporaryDirectory(prefix="passive-demo-app-") as tmp:
        manager = JobManager(Path(tmp), max_jobs=2)
        job = manager.start()
        deadline = time.monotonic() + 180
        while job.status in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.1)
        snapshot = job.snapshot()
        assert snapshot["status"] == "passed", snapshot["error"]
        kinds = {event["kind"] for event in snapshot["events"]}
        assert {"scan", "bind", "install", "version", "certificate", "agent_log", "detection", "test_output", "cicd", "report", "complete"} <= kinds
        result = snapshot["result"]
        assert result is not None
        lifecycle = result["lifecycle"]
        assert lifecycle["initial_scan"]["backbond_score"] is None
        assert lifecycle["updated_scan"]["backbond_score"] is None
        assert lifecycle["updated_scan"]["rating_status"] == "not_available"
        assert lifecycle["bind"]["admittance_class"] == "CONSTRAINED_BIND"
        assert lifecycle["bind"]["real_policy_bound"] is False
        assert lifecycle["installation"]["status"] == "INSTALLED"
        assert lifecycle["ci_gate"]["status"] == "passed"
        assert lifecycle["certificate"]["signature_status"] == "valid"
        assert result["monitoring_report"]["production_logs"] == 9
        assert result["report"]["status"] == "PASS"
        assert result["report"]["perils_detected"] == ["P1", "P2", "P3", "P4", "P5", "P6"]
        assert result["tests"]["status"] == "passed"
        assert len(result["tracker_rows"]) == 7
        assert not os.path.exists(Path(tmp) / "unexpected")
