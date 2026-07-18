from __future__ import annotations

import os
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
import json
import sys
import tempfile
import threading
import time

import pytest


DEMO_DIR = Path(__file__).resolve().parents[1] / "demo"
sys.path.insert(0, str(DEMO_DIR))

from app import DemoHandler, JobManager, _deployment_metadata  # noqa: E402


FORBIDDEN_CUSTOMER_KEYS = {"are_total", "adr_ref", "rules_disabled", "system_prompt_kind"}
PUBLIC_ADMITTANCE_KEYS = {"admittance_class", "firing_rule_id", "scope", "rationale"}


def _assert_customer_projection(value: object) -> None:
    if isinstance(value, dict):
        assert not (FORBIDDEN_CUSTOMER_KEYS & set(value)), value
        for key, child in value.items():
            if key in {"initial_admittance", "updated_admittance", "decision"} and child is not None:
                assert set(child) == PUBLIC_ADMITTANCE_KEYS, child
            _assert_customer_projection(child)
    elif isinstance(value, list):
        for child in value:
            _assert_customer_projection(child)


def _post_and_poll_scenario(manager: JobManager, scenario: str) -> dict:
    DemoHandler.manager = manager
    DemoHandler.access_token = None
    DemoHandler.run_quota = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    try:
        body = json.dumps({"scenario": scenario})
        connection.request("POST", "/api/runs", body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        accepted = json.loads(response.read())
        assert response.status == 202
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            connection.request("GET", f"/api/runs/{accepted['run_id']}")
            polled = connection.getresponse()
            snapshot = json.loads(polled.read())
            assert polled.status == 200
            if snapshot["status"] not in {"queued", "running"}:
                return snapshot
            time.sleep(0.1)
        raise AssertionError(f"scenario {scenario} did not finish before timeout")
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_deployment_metadata_contains_only_public_build_facts(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_GIT_SHA", "abc123")
    monkeypatch.setenv("DEMO_GITHUB_RUN_ID", "42")
    monkeypatch.setenv("DEMO_GITHUB_REPOSITORY", "CorwinFoundation/are-scanner")
    monkeypatch.setenv("DEMO_GITHUB_RUN_URL", "https://github.com/private/actions/runs/42")
    monkeypatch.setenv("DEMO_GIT_REF", "refs/pull/61/head")
    metadata = _deployment_metadata()
    assert metadata["git_sha"] == "abc123"
    assert metadata["github_run_id"] == "42"
    assert "github_run_url" not in metadata
    assert "repository" not in metadata
    assert "git_ref" not in metadata

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
        _assert_customer_projection(snapshot)
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


@pytest.mark.parametrize("scenario", ["bind_reject", "cicd_regression"])
def test_failure_scenario_http_responses_fail_closed(scenario: str) -> None:
    with tempfile.TemporaryDirectory(prefix=f"passive-demo-{scenario}-") as tmp:
        snapshot = _post_and_poll_scenario(JobManager(Path(tmp), max_jobs=2), scenario)
    assert snapshot["status"] == "failed"
    _assert_customer_projection(snapshot)
