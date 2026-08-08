"""Internal HTTP boundary for the BEAAA-20231 production ingest adapter."""
from __future__ import annotations

import argparse
import gzip
import importlib
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .appliance_ingest import ApplianceIngestStore, IngestRejected


MAX_BODY_BYTES = 2 * 1024 * 1024


def load_sealed_contract(module_root: Path):
    """Load only the immutable 0.1.1 trust-core exports."""
    import sys
    sys.path.insert(0, str(module_root))
    batch = importlib.import_module("appliance.batch")
    pipeline = importlib.import_module("appliance.pipeline")
    return batch.verify_batch, pipeline.CONTENT_FIELDS


def make_handler(store: ApplianceIngestStore):
    class Handler(BaseHTTPRequestHandler):
        server_version = "BackBondApplianceIngest/0.1"

        def log_message(self, _format, *_args):
            return  # never put request bodies, tokens, or tenant ids in access logs

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, sort_keys=True).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise IngestRejected("body_size_invalid")
            body = self.rfile.read(length)
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
                if len(body) > MAX_BODY_BYTES:
                    raise IngestRejected("body_size_invalid")
            return json.loads(body)

        def do_GET(self):
            if self.path == "/healthz":
                self._send(200, {"ok": True})
            elif self.path == "/metrics":
                self._send(200, store.status())
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/ingest":
                self._send(404, {"error": "not_found"}); return
            authorization = self.headers.get("Authorization", "")
            token = authorization[7:] if authorization.startswith("Bearer ") else ""
            try:
                result = store.ingest(
                    self._body(), token=token,
                    tenant_id=self.headers.get("X-BackBond-Tenant", ""),
                    source_id=self.headers.get("X-BackBond-Source", ""),
                )
                self._send(200, {"accepted": result.accepted,
                                 "duplicate": result.duplicate,
                                 "batch_hash": result.batch_hash})
            except (IngestRejected, json.JSONDecodeError, gzip.BadGzipFile) as exc:
                self._send(422, {"accepted": False, "error": str(exc)})
            except Exception:
                # Retryable outage response; internal details and payload stay off wire.
                self._send(503, {"accepted": False, "error": "persistence_unavailable"})
    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--database", required=True)
    parser.add_argument("--migration", required=True)
    parser.add_argument("--sealed-root", required=True)
    parser.add_argument("--enrollment-file", required=True)
    args = parser.parse_args()
    verify, fields = load_sealed_contract(Path(args.sealed_root))
    store = ApplianceIngestStore(args.database, args.migration,
                                 verify_batch=verify, prohibited_fields=fields)
    enrollment = json.loads(Path(args.enrollment_file).read_text(encoding="utf-8"))
    token_env = enrollment.pop("token_env")
    store.enroll(token=os.environ[token_env], **enrollment)
    ThreadingHTTPServer((args.host, args.port), make_handler(store)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
