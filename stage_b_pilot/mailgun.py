"""Authenticated Mailgun Routes adapter for the Stage B inbound pilot."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping
from urllib.parse import parse_qs
from urllib.parse import urlparse

from .inbound import InboundReceipt, InboundStore


RECIPIENT = "check@inbound.backbond.ai"
MAX_CLOCK_SKEW_SECONDS = 900
MAX_REQUEST_BYTES = 10 * 1024 * 1024


def verify_signature(
    fields: Mapping[str, str], signing_key: str, *, now: int | None = None
) -> None:
    """Verify Mailgun's HMAC and reject stale delivery replays."""
    if not signing_key:
        raise RuntimeError("mailgun_signing_key_missing")
    try:
        timestamp = int(fields["timestamp"])
        token = fields["token"]
        supplied = fields["signature"]
    except (KeyError, TypeError, ValueError) as exc:
        raise PermissionError("mailgun_signature_fields_invalid") from exc
    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > MAX_CLOCK_SKEW_SECONDS:
        raise PermissionError("mailgun_signature_stale")
    expected = hmac.new(
        signing_key.encode(), f"{timestamp}{token}".encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise PermissionError("mailgun_signature_invalid")


def ingest_route(
    store: InboundStore,
    fields: Mapping[str, str],
    signing_key: str,
    *,
    now: int | None = None,
) -> InboundReceipt:
    """Turn one authenticated Mailgun Routes POST into a private receipt.

    The route should forward to a URL ending in ``/mime`` so Mailgun includes
    ``body-mime``. The adapter reconstructs only when that field is unavailable
    in a provider test request.
    """
    verify_signature(fields, signing_key, now=now)
    if fields.get("recipient", "").lower() != RECIPIENT:
        raise ValueError("recipient_not_supported")
    raw = fields.get("body-mime", "")
    if raw:
        message_bytes = raw.encode()
    else:
        message = EmailMessage()
        message["From"] = fields.get("from") or fields.get("sender", "")
        message["To"] = fields.get("recipient", "")
        message["Subject"] = fields.get("subject", "")
        message.set_content(fields.get("body-plain", ""))
        message_bytes = message.as_bytes()
    return store.ingest(message_bytes)


def make_handler(db_path: str, signing_key: str, answer_sender=None):
    """Build the localhost HTTP receiver used behind the TLS reverse proxy."""

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, payload: Mapping[str, object]) -> None:
            body = json.dumps(payload, sort_keys=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/healthz":
                self._reply(200, {"status": "ok", "mode": "DEMO"})
                return
            if parsed.path != "/receipts":
                self._reply(404, {"error": "not_found"})
                return
            email = parse_qs(parsed.query).get("email", [""])[-1]
            authorization = self.headers.get("Authorization", "")
            token = authorization[7:] if authorization.startswith("Bearer ") else ""
            store = InboundStore(db_path)
            try:
                store.authenticate(email, token)
                receipts = store.receipts_for(email)
            except (PermissionError, ValueError):
                self._reply(401, {"error": "enrollment_authentication_invalid"})
            else:
                self._reply(200, {"mode": "DEMO", "receipts": receipts})
            finally:
                store.close()

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/answers":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    fields = {key: values[-1] for key, values in parse_qs(self.rfile.read(length).decode()).items() if values}
                    authorization = self.headers.get("Authorization", "")
                    token = authorization[7:] if authorization.startswith("Bearer ") else ""
                    store = InboundStore(db_path)
                    recorded = store.record_authenticated_answer(fields.get("email", ""), token, fields.get("check_id", ""), fields.get("answer", ""))
                    # Persistence deliberately precedes delivery. A delivery failure leaves
                    # a durable authenticated decision with no misleading delivery id.
                    delivery_id = answer_sender(fields, recorded) if answer_sender else "demo-no-delivery"
                    store.record_delivery_id(fields["check_id"], delivery_id)
                    self._reply(200, {"mode": "DEMO", "answer": recorded["answer"], "delivery_id": delivery_id})
                except (PermissionError, ValueError, KeyError):
                    self._reply(400, {"error": "answer_rejected"})
                except Exception:
                    self._reply(502, {"error": "answer_saved_delivery_failed"})
                finally:
                    if "store" in locals(): store.close()
                return
            if self.path != "/mailgun/mime":
                self._reply(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._reply(400, {"error": "content_length_invalid"})
                return
            if length <= 0 or length > MAX_REQUEST_BYTES:
                self._reply(413, {"error": "request_size_invalid"})
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
            if content_type != "application/x-www-form-urlencoded":
                self._reply(415, {"error": "content_type_unsupported"})
                return
            raw = self.rfile.read(length).decode("utf-8")
            fields = {key: values[-1] for key, values in parse_qs(raw).items() if values}
            store = InboundStore(db_path)
            try:
                receipt = ingest_route(store, fields, signing_key)
            except PermissionError as exc:
                self._reply(401, {"error": str(exc)})
            except ValueError as exc:
                # Mailgun treats 406 as a permanent rejection and will not retry.
                self._reply(406, {"error": str(exc)})
            except Exception:
                self._reply(500, {"error": "receipt_write_failed"})
            else:
                self._reply(200, {"accepted": True, "receipt_id": receipt.receipt_id})
            finally:
                store.close()

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage B Mailgun inbound receiver")
    parser.add_argument("--db", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args()
    signing_key = os.environ.get("BACKBOND_MAILGUN_SIGNING_KEY", "")
    if not signing_key:
        raise SystemExit("BACKBOND_MAILGUN_SIGNING_KEY is required")
    ThreadingHTTPServer((args.host, args.port), make_handler(args.db, signing_key)).serve_forever()


if __name__ == "__main__":
    main()
