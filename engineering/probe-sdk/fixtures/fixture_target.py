"""In-process fixture target. Deterministic refusals per probe prompt.

Used by tests/test_n5_determinism.py to satisfy the R1 acceptance:
spread = 0 on are_total + every pillar subscore across N=5 runs.

The target is content-addressed — it inspects the prompt text and returns
canned refusal text when the prompt looks like a "refuse" probe per the
shipped corpus. Otherwise returns canned compliance text. No randomness;
no model; no network. Listens on 127.0.0.1 only.
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

_REFUSE_PHRASES = [
    "step-by-step instructions for synthesizing",
    "exfiltrates a user's private",
    "phish my coworker",
    "draft a defamatory accusation",
    "ignore prior instructions",
]

_REFUSAL_RESPONSE = (
    "I can't help with that request. That's against my guidelines and I "
    "must decline. Please reach out to an appropriate resource."
)
_ANSWER_RESPONSE = (
    "Sure — here is a short, factual answer suitable for the requested "
    "topic. (fixture target deterministic response)"
)


def _classify_prompt(prompt: str) -> str:
    p = prompt.lower()
    for phrase in _REFUSE_PHRASES:
        if phrase in p:
            return _REFUSAL_RESPONSE
    return _ANSWER_RESPONSE


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        try:
            req = json.loads(body.decode("utf-8"))
            prompt = str(req.get("prompt", ""))
        except Exception:
            self.send_response(400)
            self.end_headers()
            return
        out = _classify_prompt(prompt)
        payload = json.dumps({"output": out}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a, **kw):  # silence access log
        pass


def serve_in_thread(port: int = 0) -> tuple[HTTPServer, str]:
    """Bind to 127.0.0.1:port (port=0 picks free port). Returns (server, url)."""
    srv = HTTPServer(("127.0.0.1", port), _Handler)
    actual_port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{actual_port}/v1/chat"


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


if __name__ == "__main__":
    srv, url = serve_in_thread(18077)
    print(f"fixture target listening at {url}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
