"""Internal HTTP boundary for the BEAAA-20231 production ingest adapter."""
from __future__ import annotations

import argparse
import gzip
import importlib
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .appliance_ingest import ApplianceIngestStore, IngestRejected, outcome_log_line


MAX_BODY_BYTES = 2 * 1024 * 1024


def emit_outcome(stream=None, **fields) -> None:
    """Write one structured outcome line to stderr, which journald carries under
    the existing unit. The default access log stays silenced (``log_message``);
    this replaces it with something deliberate rather than adding to it."""
    print(outcome_log_line(**fields), file=stream or sys.stderr, flush=True)


def load_sealed_contract(module_root: Path):
    """Load only the immutable 0.1.0 trust-core exports.

    Returns ``(verify_batch, CONTENT_FIELDS)``; the signature is unchanged so the
    BEAAA-20237 harnesses keep loading the contract the way the unit does.
    """
    import sys
    sys.path.insert(0, str(module_root))
    batch = importlib.import_module("appliance.batch")
    pipeline = importlib.import_module("appliance.pipeline")
    return batch.verify_batch, pipeline.CONTENT_FIELDS


def make_handler(store: ApplianceIngestStore, *, log_stream=None):
    """``log_stream`` defaults to stderr, which journald carries under the unit.
    It is a parameter so the outcome log is exercisable at the HTTP boundary
    rather than only at the writer."""
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
            payload = json.loads(body)
            if not isinstance(payload, dict):
                # A JSON scalar or array is a permanently malformed envelope, not
                # an outage. Without this it reached the store and died on
                # ``batch.get`` inside the catch-all, answering a deterministic
                # defect with a retryable 503 and inflating unavailable_total.
                raise IngestRejected("malformed_body")
            return payload

        def _bearer(self) -> str:
            authorization = self.headers.get("Authorization", "")
            return authorization[7:] if authorization.startswith("Bearer ") else ""

        def do_GET(self):
            if self.path == "/healthz":
                self._send(200, {"ok": True})
            elif self.path == "/metrics":
                # Counts are per-tenant and require the enrolled token; an
                # unauthenticated caller never sees another tenant's volumes (F5).
                try:
                    self._send(200, store.scoped_status(
                        token=self._bearer(),
                        tenant_id=self.headers.get("X-BackBond-Tenant", ""),
                    ))
                except IngestRejected as exc:
                    self._send(403, {"error": str(exc)})
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/ingest":
                self._send(404, {"error": "not_found"}); return
            token = self._bearer()
            tenant_id = self.headers.get("X-BackBond-Tenant", "")
            outcome, reason, appliance_id, batch_seq = "unavailable", None, None, None
            try:
                batch = self._body()
                appliance_id, batch_seq = batch.get("appliance_id"), batch.get("batch_seq")
                result = store.ingest(
                    batch, token=token, tenant_id=tenant_id,
                    source_id=self.headers.get("X-BackBond-Source", ""),
                )
                outcome = "duplicate" if result.duplicate else "accepted"
                self._send(200, {"accepted": result.accepted,
                                 "duplicate": result.duplicate,
                                 "batch_hash": result.batch_hash})
            except (IngestRejected, json.JSONDecodeError, gzip.BadGzipFile) as exc:
                outcome = "rejected"
                # A decoder's message is not ours and is not a class; only an
                # IngestRejected carries a reason from the pinned taxonomy.
                reason = str(exc) if isinstance(exc, IngestRejected) else "malformed_body"
                self._send(422, {"accepted": False, "error": str(exc)})
            except Exception:
                # Retryable outage response; internal details and payload stay off wire.
                outcome, reason = "unavailable", "persistence_unavailable"
                self._send(503, {"accepted": False, "error": "persistence_unavailable"})
            finally:
                # In ``finally`` so an outcome is always recorded, including when
                # writing the response itself fails on a dropped connection.
                store.record_outcome(tenant_id=tenant_id, outcome=outcome, reason=reason)
                emit_outcome(stream=log_stream, outcome=outcome, reason=reason,
                             appliance_id=appliance_id, batch_seq=batch_seq)
    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--database", required=True)
    parser.add_argument("--migration", required=True, action="append")
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
