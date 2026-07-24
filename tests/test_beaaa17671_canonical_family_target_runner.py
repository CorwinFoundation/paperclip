from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tools.canonical_family_target_runner import (
    ConfiguredTargetRunner,
    REGISTRY_VERSION,
    TargetConfig,
    TargetRunError,
    load_registry,
)


class _Target(BaseHTTPRequestHandler):
    invocations: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.invocations.append(body)
        case_count = body["required_cases"]
        payload = json.dumps(
            {
                "family_id": body["family_id"],
                "registry_version": body["registry_version"],
                "rubric_point_id": body["rubric_point_id"],
                "runner_path": body["runner_path"],
                "cases_executed": case_count,
                "case_results": [
                    {
                        "case_id": body["case"]["case_id"],
                        "runner_path": body["runner_path"],
                        "verdict": "pass",
                    }
                ],
                "evidence_refs": [
                    {
                        "kind": "development_fixture_case_result",
                        "case_id": body["case"]["case_id"],
                    }
                ],
                "customer_data": False,
                "score_activation": False,
                "verdict": "pass",
            },
            sort_keys=True,
        ).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        pass


def test_all_61_are_discovered_and_invoked_through_one_configured_target(
    tmp_path: Path, monkeypatch
) -> None:
    rows = load_registry()
    assert len(rows) == 61
    assert len({row["family_id"] for row in rows}) == 61
    _Target.invocations = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Target)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("BACKBOND_TEST_TARGET_TOKEN", "test-token")
    runner = ConfiguredTargetRunner(
        TargetConfig(
            url=f"http://127.0.0.1:{server.server_port}/complete",
            identity="pytest-non-customer-target",
            config_class="local_http_test_fixture",
            credential_env="BACKBOND_TEST_TARGET_TOKEN",
            call_cap=61,
            spend_cap_usd=0.0,
        ),
        tmp_path,
    )
    try:
        results = [runner.run_family(row) for row in rows]
    finally:
        server.shutdown()
    assert [body["family_id"] for body in _Target.invocations] == [
        row["family_id"] for row in rows
    ]
    assert [body["runner_path"] for body in _Target.invocations] == [
        row["runner_path"] for row in rows
    ]
    assert [body["required_cases"] for body in _Target.invocations] == [
        row["required_cases"] for row in rows
    ]
    assert all(
        body["case"]["kind"] == "family_runner_verdict" for body in _Target.invocations
    )
    assert all(result["verdict"] == "pass" for result in results)
    assert all(result["target_verdict"] == "pass" for result in results)
    assert all(result["evidence"]["sha256"] for result in results)
    assert runner.calls == 61
    assert runner.spend_usd == 0.0
    assert REGISTRY_VERSION == "canonical-61-family-registry@2026-07-13"


def test_runner_fails_closed_on_generic_non_empty_http_200(
    tmp_path: Path, monkeypatch
) -> None:
    class GenericTarget(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            payload = json.dumps({"accepted": "anything"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), GenericTarget)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("BACKBOND_TEST_TARGET_TOKEN", "test-token")
    runner = ConfiguredTargetRunner(
        TargetConfig(
            url=f"http://127.0.0.1:{server.server_port}/complete",
            identity="pytest-non-customer-target",
            config_class="local_http_test_fixture",
            credential_env="BACKBOND_TEST_TARGET_TOKEN",
            call_cap=1,
            spend_cap_usd=0.0,
        ),
        tmp_path,
    )
    try:
        result = runner.run_family(load_registry()[40])
    finally:
        server.shutdown()

    assert result["verdict"] == "fail"
    assert result["target_verdict"] is None
    assert result["evidence"]["sha256"]


def test_runner_fails_closed_on_family_specific_failed_verdict(
    tmp_path: Path, monkeypatch
) -> None:
    class FailingTarget(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            payload = json.dumps(
                {
                    "family_id": body["family_id"],
                    "registry_version": body["registry_version"],
                    "rubric_point_id": body["rubric_point_id"],
                    "runner_path": body["runner_path"],
                    "cases_executed": body["required_cases"],
                    "case_results": [
                        {
                            "case_id": body["case"]["case_id"],
                            "runner_path": body["runner_path"],
                            "verdict": "fail",
                        }
                    ],
                    "evidence_refs": [
                        {
                            "kind": "development_fixture_case_result",
                            "case_id": body["case"]["case_id"],
                        }
                    ],
                    "customer_data": False,
                    "score_activation": False,
                    "verdict": "fail",
                },
                sort_keys=True,
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), FailingTarget)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("BACKBOND_TEST_TARGET_TOKEN", "test-token")
    runner = ConfiguredTargetRunner(
        TargetConfig(
            url=f"http://127.0.0.1:{server.server_port}/complete",
            identity="pytest-non-customer-target",
            config_class="local_http_test_fixture",
            credential_env="BACKBOND_TEST_TARGET_TOKEN",
            call_cap=1,
            spend_cap_usd=0.0,
        ),
        tmp_path,
    )
    try:
        result = runner.run_family(load_registry()[40])
    finally:
        server.shutdown()

    assert result["verdict"] == "fail"
    assert result["target_verdict"] == "fail"


def test_runner_rejects_production_credential_markers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CUSTOMER_PROD_TOKEN", "unsafe")
    runner = ConfiguredTargetRunner(
        TargetConfig("http://127.0.0.1", "x", "x", "CUSTOMER_PROD_TOKEN"), tmp_path
    )
    try:
        runner.run_family(load_registry()[0])
    except TargetRunError as exc:
        assert "non-production" in str(exc)
    else:
        raise AssertionError("production/customer credential marker must fail closed")
