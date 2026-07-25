#!/usr/bin/env python3
"""Synthetic-only HTTP ingress guard for BEAAA-17702."""

from __future__ import annotations

import http.client
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PROHIBITED_KEYS = frozenset({"email", "phone", "customer_id", "customer_name", "pii"})
UPSTREAM_HOST = os.environ.get("UPSTREAM_HOST", "langfuse-web")
UPSTREAM_PORT = int(os.environ.get("UPSTREAM_PORT", "3000"))
MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", "1048576"))


def prohibited_paths(value: Any, prefix: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}"
            if key.lower() in PROHIBITED_KEYS:
                hits.append(path)
            hits.extend(prohibited_paths(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits.extend(prohibited_paths(child, f"{prefix}[{index}]"))
    return hits


class GuardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _reject(self, status: int, message: str) -> None:
        body = json.dumps({"error": message}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > MAX_BODY_BYTES:
            self._reject(413, "request body exceeds synthetic ingress limit")
            return
        body = self.rfile.read(content_length) if content_length else b""

        if self.command in {"POST", "PUT", "PATCH"} and self.path.startswith(
            "/api/public/ingestion"
        ):
            try:
                document = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._reject(400, "ingestion body must be valid JSON")
                return
            hits = prohibited_paths(document)
            if hits:
                self._reject(422, "prohibited customer or PII field")
                return

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "connection", "content-length"}
        }
        headers["Host"] = f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"
        if body:
            headers["Content-Length"] = str(len(body))

        connection = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=30)
        try:
            connection.request(self.command, self.path, body=body or None, headers=headers)
            response = connection.getresponse()
            response_body = response.read()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in {
                    "connection",
                    "keep-alive",
                    "proxy-authenticate",
                    "proxy-authorization",
                    "te",
                    "trailer",
                    "transfer-encoding",
                    "upgrade",
                    "content-length",
                }:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(response_body)
        finally:
            connection.close()

    do_GET = _proxy
    do_POST = _proxy
    do_PUT = _proxy
    do_PATCH = _proxy
    do_DELETE = _proxy
    do_HEAD = _proxy
    do_OPTIONS = _proxy

    def log_message(self, format: str, *args: object) -> None:
        # Never log request bodies or credentials.
        print(f"{self.address_string()} {format % args}", flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), GuardHandler).serve_forever()
