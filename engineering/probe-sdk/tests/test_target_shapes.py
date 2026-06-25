"""R2 target-shape shim tests.

These tests prove the live-target adapter layer normalizes Botpress-style
HTTP and local transcript exports into the existing TargetResponse shape.
No scoring, rubric, or judge logic lives here.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from are_probe.target import call_target


class _BotpressLikeHandler(BaseHTTPRequestHandler):
    seen_payloads: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.seen_payloads.append(payload)
        body = json.dumps({
            "messages": [
                {"payload": {"text": f"normalized: {payload['message']['text']}"}}
            ]
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):
        pass


def _serve(handler_cls):
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/api/messages"


def test_botpress_webchat_shape_posts_message_and_extracts_text():
    _BotpressLikeHandler.seen_payloads = []
    srv, url = _serve(_BotpressLikeHandler)
    try:
        response = call_target(
            url=url,
            shape="botpress_webchat",
            auth_mode="none",
            bearer_env_var=None,
            timeout_seconds=5,
            probe_id="probe-1",
            prompt="hello",
        )
    finally:
        srv.shutdown()

    assert response.status_code == 200
    assert response.error_class is None
    assert response.output_text == "normalized: hello"
    assert _BotpressLikeHandler.seen_payloads == [
        {
            "message": {"type": "text", "text": "hello"},
            "metadata": {"probe_id": "probe-1", "source": "are-probe-r2"},
        }
    ]


def test_custom_response_path_extracts_nested_text():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.dumps({"result": {"final": "custom path answer"}}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args, **kwargs):
            pass

    srv, url = _serve(Handler)
    try:
        response = call_target(
            url=url,
            shape="generic_prompt_output",
            auth_mode="none",
            bearer_env_var=None,
            timeout_seconds=5,
            probe_id="probe-1",
            prompt="hello",
            response_json_paths=("result.final",),
        )
    finally:
        srv.shutdown()

    assert response.output_text == "custom path answer"


def test_transcript_export_matches_probe_id(tmp_path: Path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"probe_id": "other", "output": "wrong"}) + "\n"
        + json.dumps({"probe_id": "probe-1", "output": "right answer"}) + "\n"
    )

    response = call_target(
        url=f"file://{transcript}",
        shape="transcript_export",
        auth_mode="none",
        bearer_env_var=None,
        timeout_seconds=5,
        probe_id="probe-1",
        prompt="ignored when probe_id matches",
    )

    assert response.status_code == 200
    assert response.error_class is None
    assert response.output_text == "right answer"

