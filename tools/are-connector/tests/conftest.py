from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from are_connector.probe_runtime import ProbeRuntime


class ProbeHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        body = json.dumps({"response": f"echo:{payload.get('input', '')}"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return None


@pytest.fixture
def agent_endpoint(monkeypatch):
    monkeypatch.setenv("ALLOW_PLAINTEXT_AGENT_ENDPOINT", "1")
    server = HTTPServer(("127.0.0.1", 0), ProbeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/probe"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture
def probe_runtime(agent_endpoint: str) -> ProbeRuntime:
    return ProbeRuntime(
        agent_endpoint=agent_endpoint,
        portal_base_url=agent_endpoint,
        scanner_version="0.4.2",
        corpus_version="connector_probe_runtime_smoke@0.1.0",
        model_versions={"primary": "mock-http-agent@0.1.0", "judges": []},
        seed=42,
        probe_set_version="connector_probe_runtime_smoke@0.1.0",
    )
