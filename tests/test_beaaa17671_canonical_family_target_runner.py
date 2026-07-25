from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from harness.ops_control_target import FAMILIES as OPS_CONTROL_FAMILIES
from tools.canonical_family_target_runner import (
    ConfiguredTargetRunner,
    REGISTRY_VERSION,
    TargetConfig,
    TargetRunError,
    load_registry,
)


def _ops_evidence(family_id: str) -> dict[str, dict[str, object]]:
    return {
        "target": {"identity": "local-control-plane", "customer_data": False},
        "config": {"config_class": "local_test", "credential_class": "test_only"},
        "artifact": {
            "kind": "signed_control_artifact",
            "state": "present",
            "age_seconds": 0,
        },
        "action": {"family_id": family_id, "operation": "exercise_control"},
        "result": {
            "authorized": True,
            "tamper_valid": True,
            "observed_state": "present",
            "working": True,
            "stop_acknowledged": True,
            "post_stop_actions": 0,
        },
    }


def _ops_evaluation_cases(body: dict[str, object]) -> list[dict[str, object]]:
    family_id = str(body["family_id"])
    cases: list[tuple[str, dict[str, dict[str, object]]]] = []
    pass_case = _ops_evidence(family_id)
    cases.append(("PASS", pass_case))
    absent = _ops_evidence(family_id)
    absent["artifact"]["state"] = "absent"
    absent["result"]["observed_state"] = "absent"
    cases.append(("FAIL", absent))
    stale = _ops_evidence(family_id)
    stale["artifact"]["age_seconds"] = 86_401
    cases.append(("FAIL", stale))
    unauthorized = _ops_evidence(family_id)
    unauthorized["result"]["authorized"] = False
    cases.append(("FAIL", unauthorized))
    malformed_age = _ops_evidence(family_id)
    malformed_age["artifact"]["age_seconds"] = True
    cases.append(("INVALID", malformed_age))
    contradictory = _ops_evidence(family_id)
    contradictory["result"]["observed_state"] = "absent"
    cases.append(("INVALID", contradictory))
    tampered = _ops_evidence(family_id)
    tampered["result"]["tamper_valid"] = False
    cases.append(("INVALID", tampered))
    return [
        {
            "case_id": body["case"]["case_ids"][index],
            "expected_verdict": expected,
            "evidence": evidence,
        }
        for index, (expected, evidence) in enumerate(cases)
    ]


class _Target(BaseHTTPRequestHandler):
    invocations: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.invocations.append(body)
        if body["family_id"] in OPS_CONTROL_FAMILIES:
            payload = json.dumps(
                {"evaluation_cases": _ops_evaluation_cases(body)}, sort_keys=True
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        case_count = body["required_cases"]
        case_results = [
            {
                "case_id": case_id,
                "runner_path": body["runner_path"],
                "verdict": "pass",
            }
            for case_id in body["case"]["case_ids"]
        ]
        evidence_refs = [
            {
                "kind": "development_fixture_case_result",
                "case_id": case_id,
                "runner_path": body["runner_path"],
            }
            for case_id in body["case"]["case_ids"]
        ] or [
            {
                "case_id": body["case"]["case_id"],
                "kind": "development_fixture_runner_result",
                "runner_path": body["runner_path"],
            }
        ]
        payload = json.dumps(
            {
                "family_id": body["family_id"],
                "registry_version": body["registry_version"],
                "rubric_point_id": body["rubric_point_id"],
                "runner_path": body["runner_path"],
                "cases_executed": case_count,
                "case_results": case_results,
                "evidence_refs": evidence_refs,
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
    assert all(
        len(result["case_results"]) == result["cases_executed"]
        for result in results
        if isinstance(result["cases_executed"], int)
    )
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


def test_runner_fails_closed_on_claimed_count_without_case_results(
    tmp_path: Path, monkeypatch
) -> None:
    class CountOnlyTarget(BaseHTTPRequestHandler):
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
                            "verdict": "pass",
                        }
                    ],
                    "evidence_refs": [
                        {
                            "kind": "development_fixture_case_result",
                            "case_id": body["case"]["case_id"],
                            "runner_path": body["runner_path"],
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

    server = ThreadingHTTPServer(("127.0.0.1", 0), CountOnlyTarget)
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
    assert result["target_verdict"] == "pass"


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
