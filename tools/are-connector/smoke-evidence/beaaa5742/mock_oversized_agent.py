#!/usr/bin/env python3
"""Mock agent that returns body sized via ?size= query (or 2MiB default)."""
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        qs = parse_qs(urlparse(self.path).query)
        size = int(qs.get("size", ["2097152"])[0])
        body = b"{\"response\":\"" + (b"x" * (size - 16)) + b"\"}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a, **k):
        return

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18090
    HTTPServer(("0.0.0.0", port), H).serve_forever()
