"""Authenticated Mailgun Routes adapter for the Stage B inbound pilot."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
import base64
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping
from urllib.parse import parse_qs
from urllib.parse import urlencode
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .inbound import InboundReceipt, InboundStore


RECIPIENT = "check@inbound.backbond.ai"
MAX_CLOCK_SKEW_SECONDS = 900
MAX_REQUEST_BYTES = 10 * 1024 * 1024


class MailgunAnswerSender:
    """Send one persisted decision and return provider acceptance identity."""

    def __init__(self, api_url: str, api_key: str, from_address: str) -> None:
        self.api_url = api_url
        self.api_key = api_key
        self.from_address = from_address

    def __call__(self, fields, recorded) -> str:
        body = urlencode({
            "from": self.from_address,
            "to": fields["email"],
            "subject": f"BackBond check answer: {recorded['answer']}",
            "text": f"Your authenticated BackBond DEMO answer is {recorded['answer']}. Check: {fields['check_id']}",
            "v:backbond-check-id": fields["check_id"],
        }).encode()
        authorization = base64.b64encode(f"api:{self.api_key}".encode()).decode()
        request = Request(self.api_url, data=body, headers={
            "Authorization": f"Basic {authorization}",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())
        message_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(message_id, str) or not message_id.strip():
            raise RuntimeError("mailgun_acceptance_id_missing")
        return message_id.strip()


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
                    if recorded.get("delivery_id"):
                        self._reply(200, {"mode": "DEMO", "answer": recorded["answer"], "delivery_id": recorded["delivery_id"], "provider_accepted": True, "idempotent_replay": True})
                        return
                    if answer_sender is None:
                        self._reply(202, {"mode": "DEMO", "answer": recorded["answer"], "delivery_id": None, "retryable": True})
                        return
                    try:
                        delivery_id = answer_sender(fields, recorded)
                        store.record_delivery_id(fields["check_id"], delivery_id)
                    except Exception as exc:
                        store.record_delivery_failure(fields["check_id"], type(exc).__name__)
                        self._reply(502, {"error": "answer_saved_delivery_failed", "retryable": True})
                        return
                    self._reply(200, {"mode": "DEMO", "answer": recorded["answer"], "delivery_id": delivery_id, "provider_accepted": True})
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
    api_url = os.environ.get("BACKBOND_MAILGUN_MESSAGES_URL", "")
    api_key = os.environ.get("BACKBOND_MAILGUN_API_KEY", "")
    from_address = os.environ.get("BACKBOND_MAILGUN_FROM", "")
    sender = MailgunAnswerSender(api_url, api_key, from_address) if all((api_url, api_key, from_address)) else None
    ThreadingHTTPServer((args.host, args.port), make_handler(args.db, signing_key, sender)).serve_forever()


if __name__ == "__main__":
    main()
