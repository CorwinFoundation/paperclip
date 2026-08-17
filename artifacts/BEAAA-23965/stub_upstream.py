#!/usr/bin/env python3
"""QA-independent stub upstream for BEAAA-23965. Appends one line per POST to
POSTLOG so the count of upstream POSTs is observed out-of-process, not asserted
by the same code that drives the bridge."""
import hashlib, json, os, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

POSTLOG = os.environ["QA_POSTLOG"]
LK = threading.Lock()


class Stub(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        with LK:
            with open(POSTLOG, "a") as f:
                f.write("%s %d %s %s\n" % (self.path, len(body),
                                           hashlib.sha256(body).hexdigest()[:12],
                                           body[:60].decode(errors="replace")))
                f.flush()
        out = json.dumps({"ok": True, "bytes": n}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2")
        self.end_headers(); self.wfile.write(b"up")

    def log_message(self, *a):
        pass


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Stub).serve_forever()
