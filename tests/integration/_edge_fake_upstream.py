"""Fake scanner app-tier upstream for the BEAAA-11866 edge integration test.

Runs inside a throwaway ``python:3.12-alpine`` container behind the Caddy edge.
It (1) records every request path it receives so the test can prove denied
routes never reach upstream, and (2) deliberately leaks topology detail
(``Server``/``Via``/``X-Powered-By``/``Location`` headers, internal host/port,
a stack trace on 500) so the test can prove the edge strips all of it.

Control endpoints (never proxied by the edge; queried directly by the test):
  GET  /__received__  -> JSON list of [method, path] seen since last reset
  POST /__reset__     -> clear the recording

Env: UPSTREAM_PORT (default 9000).
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LEAK_HOST = "10.0.0.5"
LEAK_PORT = "8077"
LEAK_INTERNAL_PATH = "/internal/scan/leaky-path"
LEAK_STACK = "Traceback (most recent call last): RuntimeError at app.py:1337"
LEAK_SERVER = "uvicorn/0.29-internal"
TRIGGER_500_ID = "trigger-500"

_received = []
_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _leaky(self):
        self.send_header("Server", LEAK_SERVER)
        self.send_header("Via", "1.1 internal-app-tier")
        self.send_header("X-Powered-By", "FastAPI")
        self.send_header("X-Runtime", "0.004321")
        self.send_header(
            "Location", "http://%s:%s%s" % (LEAK_HOST, LEAK_PORT, LEAK_INTERNAL_PATH)
        )

    def _send(self, code, body, leak=True):
        raw = body.encode()
        self.send_response(code)
        if leak:
            self._leaky()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _record(self):
        with _lock:
            _received.append([self.command, self.path])

    def do_GET(self):
        if self.path == "/__received__":
            with _lock:
                snap = list(_received)
            self._send(200, json.dumps(snap), leak=False)
            return
        self._record()
        if self.path == "/scan/%s" % TRIGGER_500_ID:
            self._send(500, LEAK_STACK)
        elif self.path.startswith("/scan/"):
            self._send(200, json.dumps({"scan_id": self.path.rsplit("/", 1)[-1]}))
        else:
            self._send(200, json.dumps({"reached_upstream": self.path}))

    def do_POST(self):
        if self.path == "/__reset__":
            with _lock:
                _received.clear()
            self._send(200, json.dumps({"ok": True}), leak=False)
            return
        self._record()
        if self.path == "/scanner/v1/scans":
            self._send(202, json.dumps({"scan_id": "scn_ok", "status": "queued"}))
        else:
            self._send(200, json.dumps({"reached_upstream": self.path}))

    def do_DELETE(self):
        self._record()
        self._send(200, json.dumps({"reached_upstream": self.path}))


if __name__ == "__main__":
    port = int(os.environ.get("UPSTREAM_PORT", "9000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
