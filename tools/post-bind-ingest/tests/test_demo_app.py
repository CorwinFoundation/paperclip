from __future__ import annotations

import os
import inspect
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.request


DEMO_DIR = Path(__file__).resolve().parents[1] / "demo"
sys.path.insert(0, str(DEMO_DIR))

from app import DETAILED_LIFECYCLE_ROUTE, INDEX, LIFECYCLE_INDEX, DemoHandler, JobManager, _deployment_metadata  # noqa: E402


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


def test_run_start_uses_handler_token_and_rejects_query_tokens(monkeypatch) -> None:
    class HandlerStub:
        access_token = "handler-token-with-at-least-32-characters"
        headers: dict[str, str] = {}

        def _session_cookie_value(self) -> str | None:
            return DemoHandler._session_cookie_value(self)

        def _is_authorized(self) -> bool:
            return DemoHandler._is_authorized(self)

    parsed = type("Parsed", (), {"query": f"token={HandlerStub.access_token}"})()
    monkeypatch.delenv("DEMO_ACCESS_TOKEN", raising=False)
    assert not DemoHandler._authorized_for_run(HandlerStub(), parsed)

    HandlerStub.headers = {"Authorization": f"Bearer {HandlerStub.access_token}"}
    assert DemoHandler._authorized_for_run(HandlerStub(), parsed)

    HandlerStub.headers = {"Authorization": "Bearer wrong"}
    monkeypatch.setenv("DEMO_ACCESS_TOKEN", "wrong")
    assert not DemoHandler._authorized_for_run(HandlerStub(), parsed)


def test_root_preserves_3d_demo_and_detailed_lifecycle_route_is_stable() -> None:
    source = inspect.getsource(DemoHandler.do_GET)
    assert 'if path == "/":' in source
    assert "self._send_index()" in source
    assert DETAILED_LIFECYCLE_ROUTE == "/lifecycle"
    assert "if path == DETAILED_LIFECYCLE_ROUTE:" in source
    assert "self._send_lifecycle_index()" in source
    assert INDEX.name == "BackBond-Underwriting-Desk-3D.html"
    assert LIFECYCLE_INDEX.name == "index_v2.html"


def test_static_routes_serve_3d_root_and_detailed_lifecycle_html() -> None:
    from http.server import ThreadingHTTPServer

    DemoHandler.manager = JobManager(Path(tempfile.mkdtemp(prefix="passive-demo-static-")), max_jobs=1)
    server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base + "/", timeout=5) as response:
            root_html = response.read().decode("utf-8")
        with urllib.request.urlopen(base + DETAILED_LIFECYCLE_ROUTE, timeout=5) as response:
            lifecycle_html = response.read().decode("utf-8")
    finally:
        server.shutdown()
        thread.join(timeout=5)
    assert "The Underwriting Desk" in root_html
    assert "Detailed lifecycle views" in root_html
    assert "BackBond full lifecycle proof" in lifecycle_html
    assert "Evidence JSON" in lifecycle_html


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
