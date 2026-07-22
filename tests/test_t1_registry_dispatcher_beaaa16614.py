from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tools.t1_registry_dispatcher import HttpAgentAdapter, dispatch_t1


def _rows():
    return [
        {"case_id": "case-b", "disposition": "production_active", "countable_as_active_executable": True, "source_bucket": "T1 in-repo executable", "rubric_point_id": "P3.1", "prompt_or_action": "payload-b"},
        {"case_id": "case-a", "disposition": "production_active", "countable_as_active_executable": True, "source_bucket": "T1 in-repo executable", "rubric_point_id": "P1.1", "prompt_or_action": "payload-a"},
        {"case_id": "not-applicable", "disposition": "production_active", "countable_as_active_executable": True, "source_bucket": "T1 in-repo executable", "applicable": False, "prompt_or_action": "never"},
        {"case_id": "t2", "disposition": "production_active", "countable_as_active_executable": True, "source_bucket": "T2 framework-native", "prompt_or_action": "never"},
    ]


def test_controlled_http_agent_receives_every_selected_t1_payload_once(tmp_path):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.headers.get("Authorization"), body["prompt"]))
            payload = json.dumps({"response": f"seen:{body['prompt']}"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        manifest = dispatch_t1(
            _rows(),
            adapter=HttpAgentAdapter(f"http://127.0.0.1:{server.server_port}", bearer_token="fixture-token"),
            evidence_dir=tmp_path,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert received == [("Bearer fixture-token", "payload-a"), ("Bearer fixture-token", "payload-b")]
    assert manifest["expected_case_ids"] == ["case-a", "case-b"]
    assert manifest["expected_case_ids"] == manifest["attempted_case_ids"] == manifest["terminal_case_ids"] == manifest["evidenced_case_ids"]
    assert manifest["exact_reconciliation"] is True
    assert manifest["external_gate_open"] is True


def test_missing_adapter_is_terminal_incomplete_and_external_gate_stays_closed(tmp_path):
    manifest = dispatch_t1(_rows(), adapter=None, evidence_dir=tmp_path)

    assert manifest["exact_reconciliation"] is True
    assert manifest["external_gate_open"] is False
    assert {row["status"] for row in manifest["attempts"]} == {"incomplete"}
    assert {row["incomplete_reason"] for row in manifest["attempts"]} == {"target_adapter_unavailable"}
