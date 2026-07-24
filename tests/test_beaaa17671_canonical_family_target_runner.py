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
    invocations: list[str] = []

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.invocations.append(body["family_id"])
        payload = json.dumps({"accepted": body["family_id"]}).encode()
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
    assert _Target.invocations == [row["family_id"] for row in rows]
    assert all(result["verdict"] == "pass" for result in results)
    assert all(result["evidence"]["sha256"] for result in results)
    assert runner.calls == 61
    assert runner.spend_usd == 0.0
    assert REGISTRY_VERSION == "canonical-61-family-registry@2026-07-13"


def test_runner_rejects_production_credential_markers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CUSTOMER_PROD_TOKEN", "unsafe")
    runner = ConfiguredTargetRunner(
        TargetConfig("http://127.0.0.1", "x", "x", "CUSTOMER_PROD_TOKEN"), tmp_path
    )
    try:
        runner.run_family({"family_id": "advbench"})
    except TargetRunError as exc:
        assert "non-production" in str(exc)
    else:
        raise AssertionError("production/customer credential marker must fail closed")
