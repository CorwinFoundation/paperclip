from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time


DEMO_DIR = Path(__file__).resolve().parents[1] / "demo"
sys.path.insert(0, str(DEMO_DIR))

from app import JobManager, _deployment_metadata  # noqa: E402


def test_deployment_metadata_contains_only_public_build_facts(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_GIT_SHA", "abc123")
    monkeypatch.setenv("DEMO_GITHUB_RUN_ID", "42")
    monkeypatch.setenv("DEMO_GITHUB_REPOSITORY", "CorwinFoundation/are-scanner")
    metadata = _deployment_metadata()
    assert metadata["git_sha"] == "abc123"
    assert metadata["github_run_url"].endswith("/actions/runs/42")
    assert all(gate["status"] == "passed" for gate in metadata["gates"])
    assert not any("token" in key.lower() or "secret" in key.lower() for key in metadata)


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
        assert {"agent_log", "detection", "test_output", "cicd", "complete"} <= kinds
        result = snapshot["result"]
        assert result is not None
        assert result["report"]["status"] == "PASS"
        assert result["report"]["perils_detected"] == ["P1", "P2", "P3", "P4", "P5", "P6"]
        assert result["tests"]["status"] == "passed"
        assert len(result["tracker_rows"]) == 7
        assert not os.path.exists(Path(tmp) / "unexpected")
