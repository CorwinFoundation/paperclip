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
from .history_import import HistoryImportStore, MAX_MESSAGE_BYTES
from .instinct_standing import STANDING_PREFIX, StandingReportStore, render_instruction


RECIPIENT = "check@inbound.backbond.ai"
MAX_CLOCK_SKEW_SECONDS = 900
MAX_REQUEST_BYTES = 10 * 1024 * 1024


class MailgunCancellationReminderSender:
    """Send a DEMO cancellation reminder to the registered agent address."""

    def __init__(self, api_url: str, api_key: str, from_address: str) -> None:
        self.api_url = api_url
        self.api_key = api_key
        self.from_address = from_address

    def __call__(self, relay: Mapping[str, str]) -> str:
        reference = relay["reference"]
        text = "\n".join((
            "BackBond DEMO cancellation reminder",
            f"Check reference: {reference}",
            f"Reason: {relay['reason']}",
            "A matching merchant confirmation was reported. Review and cancel the booking if appropriate.",
            "This is an agent-reported DEMO observation, not a verified decision.",
        ))
        body = urlencode({
            "from": self.from_address,
            "to": relay["agent_recipient"],
            "subject": f"BackBond cancellation reminder {reference}",
            "text": text,
            "v:backbond-check-id": reference,
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


def ingest_history_route(
    store: HistoryImportStore, fields: Mapping[str, str], signing_key: str,
    *, now: int | None = None,
) -> str:
    """Accept an enrolled user's one-time history email via signed Mailgun POST.

    The Mailgun signature authenticates transport, not Instinct's authorship.
    Instinct has no documented report-signing key for user-directed email.
    """
    verify_signature(fields, signing_key, now=now)
    recipient = fields.get("recipient", "").strip().lower()
    raw = fields.get("body-mime", "")
    if not raw or len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ValueError("history_mime_missing_or_too_large")
    from email import policy
    from email.parser import BytesParser
    from email.utils import parseaddr
    message = BytesParser(policy=policy.default).parsebytes(raw.encode("utf-8"))
    if parseaddr(str(message.get("To", "")))[1].lower() != recipient:
        raise PermissionError("history_envelope_recipient_mismatch")
    return store.ingest(raw.encode("utf-8"), transport_authenticated=True)


def make_handler(db_path: str, signing_key: str, reminder_sender=None, *,
                 history_db_path: str | None = None,
                 history_key: bytes | None = None,
                 history_external_enabled: bool = False,
                 history_synthetic_enabled: bool = False,
                 standing_db_path: str | None = None,
                 standing_synthetic_enabled: bool = False,
                 public_base: str = "https://scanner.backbond.ai/stage-b-demo"):
    """Build the localhost HTTP receiver used behind the TLS reverse proxy."""

    class Handler(BaseHTTPRequestHandler):
        def _history_store(self) -> HistoryImportStore:
            if not history_db_path or not history_key:
                raise RuntimeError("history_not_configured")
            return HistoryImportStore(
                history_db_path, provider_secret=signing_key.encode(),
                encryption_key=history_key,
                external_ingestion_enabled=history_external_enabled,
            )

        def _standing_store(self) -> StandingReportStore:
            if not standing_db_path or not standing_synthetic_enabled:
                raise RuntimeError("standing_not_configured")
            return StandingReportStore(standing_db_path)

        def _history_user(self, fields: Mapping[str, object]) -> str:
            email = str(fields.get("email", ""))
            authorization = self.headers.get("Authorization", "")
            token = authorization[7:] if authorization.startswith("Bearer ") else ""
            inbound = InboundStore(db_path)
            try:
                return inbound.authenticate(email, token)
            finally:
                inbound.close()

        def _reply(self, status: int, payload: Mapping[str, object]) -> None:
            body = json.dumps(payload, sort_keys=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _html(self, status: int, body: str) -> None:
            encoded = body.encode()
            self.send_response(status)
            for name, value in (("Content-Type", "text/html; charset=utf-8"),
                                ("Content-Length", str(len(encoded))),
                                ("Cache-Control", "no-store"),
                                ("X-Robots-Tag", "noindex, nofollow, noarchive"),
                                ("Referrer-Policy", "no-referrer"),
                                ("X-Content-Type-Options", "nosniff")):
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/healthz":
                self._reply(200, {"status": "ok", "mode": "DEMO"})
                return
            if parsed.path == "/history/state":
                fields = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
                history = None
                try:
                    user_id = self._history_user(fields)
                    history = self._history_store()
                    self._reply(200, {
                        "onboarding": history.onboarding_state(user_id),
                        "support": history.onboarding_state(user_id, purpose="support"),
                        "imports": history.imports_for(user_id),
                        "external_enabled": history_external_enabled,
                        "synthetic_enabled": history_synthetic_enabled,
                    })
                except (PermissionError, ValueError):
                    self._reply(401, {"error": "enrollment_authentication_invalid"})
                except RuntimeError:
                    self._reply(503, {"error": "history_not_configured"})
                finally:
                    if history is not None:
                        history.close()
                return
            if parsed.path.startswith("/instinct/instruction/"):
                token = parsed.path.rsplit("/", 1)[-1]
                standing = None
                try:
                    standing = self._standing_store()
                    grant = standing.grant(token)
                    self._html(200, render_instruction(token, grant["expires_at"],
                                                       grant["sender_email"], public_base,
                                                       grant["history_address"],
                                                       grant["history_expires_at"]))
                except PermissionError as exc:
                    self._reply(410, {"error": str(exc)})
                except RuntimeError:
                    self._reply(404, {"error": "standing_not_configured"})
                finally:
                    if standing is not None:
                        standing.close()
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
            path = urlparse(self.path).path
            standing_post = path.startswith("/instinct/report/")
            if path not in {"/mailgun/mime", "/history/mailgun/mime",
                            "/history/request", "/history/action",
                            "/instinct/grant", "/instinct/revoke",
                            "/instinct/reports"} and not standing_post:
                self._reply(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._reply(400, {"error": "content_length_invalid"})
                return
            json_paths = {"/history/request", "/history/action", "/instinct/grant",
                          "/instinct/revoke", "/instinct/reports"}
            limit = 64_000 if standing_post else 16_384 if path in json_paths else MAX_REQUEST_BYTES
            if length <= 0 or length > limit:
                self._reply(413, {"error": "request_size_invalid"})
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
            expected_type = ("application/json" if path in json_paths or standing_post
                             else "application/x-www-form-urlencoded")
            if content_type != expected_type:
                self._reply(415, {"error": "content_type_unsupported"})
                return
            try:
                raw = self.rfile.read(length).decode("utf-8")
                fields = (json.loads(raw) if expected_type == "application/json" else
                          {key: values[-1] for key, values in parse_qs(raw).items() if values})
                if not isinstance(fields, dict):
                    raise ValueError
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                self._reply(400, {"error": "request_body_invalid"})
                return
            if path in {"/instinct/grant", "/instinct/revoke", "/instinct/reports"}:
                standing = None
                try:
                    user_id = self._history_user(fields)
                    standing = self._standing_store()
                    if path == "/instinct/grant":
                        if fields.get("consent") is not True or fields.get("synthetic") is not True:
                            raise ValueError("standing_synthetic_consent_required")
                        inbound = InboundStore(db_path)
                        try:
                            sender = inbound.agent_recipient_for(user_id)
                        finally:
                            inbound.close()
                        history = self._history_store()
                        try:
                            onboarding = history.issue_address(
                                user_id, scope={"lookback_days": 90}, synthetic=True,
                                purpose="onboarding")
                        finally:
                            history.close()
                        issued = standing.issue(
                            user_id, sender, history_address=onboarding.address,
                            history_expires_at=onboarding.expires_at)
                        self._reply(200, {**issued, "instruction_url":
                                          f"{public_base.rstrip('/')}/instinct/instruction/{issued['token']}",
                                          "history_address": onboarding.address,
                                          "history_expires_at": onboarding.expires_at,
                                          "synthetic": True})
                    elif path == "/instinct/revoke":
                        grant_id = str(fields.get("grant_id", ""))
                        history_address = standing.pending_history_address(user_id, grant_id)
                        if history_address:
                            history = self._history_store()
                            try:
                                history.revoke_pending_address(user_id, history_address)
                            finally:
                                history.close()
                        standing.revoke(user_id, grant_id)
                        self._reply(200, {"state": "revoked"})
                    else:
                        inbound = InboundStore(db_path)
                        try:
                            sender = inbound.agent_recipient_for(user_id)
                        finally:
                            inbound.close()
                        self._reply(200, {"reports": standing.reports_for(user_id),
                                          "grants": standing.grants_for(user_id),
                                          "enrolled_sender": sender})
                except PermissionError as exc:
                    self._reply(403, {"error": str(exc)})
                except ValueError as exc:
                    self._reply(400, {"error": str(exc)})
                except RuntimeError:
                    self._reply(503, {"error": "standing_not_configured"})
                finally:
                    if standing is not None:
                        standing.close()
                return
            if standing_post:
                standing = None
                try:
                    standing = self._standing_store()
                    token = path.rsplit("/", 1)[-1]
                    sender = self.headers.get("X-Instinct-Sender", "")
                    self._reply(200, standing.ingest(token, sender, fields))
                except PermissionError as exc:
                    self._reply(410, {"error": str(exc)})
                except ValueError as exc:
                    self._reply(400, {"error": str(exc)})
                except RuntimeError:
                    self._reply(404, {"error": "standing_not_configured"})
                finally:
                    if standing is not None:
                        standing.close()
                return
            if path in {"/history/request", "/history/action"}:
                history = None
                try:
                    user_id = self._history_user(fields)
                    history = self._history_store()
                    if path == "/history/request":
                        if fields.get("consent") is not True:
                            raise ValueError("history_explicit_consent_required")
                        days = fields.get("lookback_days")
                        if days not in {7, 30, 90}:
                            raise ValueError("history_lookback_invalid")
                        purpose = fields.get("purpose", "onboarding")
                        synthetic = fields.get("synthetic") is True
                        if synthetic and not history_synthetic_enabled:
                            raise PermissionError("history_synthetic_disabled")
                        inbound = InboundStore(db_path)
                        try:
                            agent_sender = inbound.agent_recipient_for(user_id)
                        finally:
                            inbound.close()
                        address = history.issue_address(
                            user_id, purpose=purpose, synthetic=synthetic,
                            scope={"lookback_days": days, "categories": ["task_records"],
                                   "sender_email": agent_sender},
                        )
                        self._reply(200, {"address": address.address,
                                          "expires_at": address.expires_at,
                                          "purpose": purpose, "synthetic": synthetic,
                                          "sender_email": agent_sender})
                    else:
                        action = str(fields.get("action", ""))
                        preview_id = str(fields.get("preview_id", ""))
                        if action == "correct_field":
                            history.correct_field(
                                user_id, preview_id, int(fields.get("row_index", -1)),
                                str(fields.get("field", "")), str(fields.get("value", "")),
                            )
                        else:
                            history.act(user_id, preview_id, action)
                        self._reply(200, {"accepted": True})
                except PermissionError as exc:
                    self._reply(403, {"error": str(exc)})
                except (ValueError, KeyError) as exc:
                    self._reply(400, {"error": str(exc)})
                except RuntimeError:
                    self._reply(503, {"error": "history_not_configured"})
                finally:
                    if history is not None:
                        history.close()
                return
            if path == "/history/mailgun/mime":
                recipient = str(fields.get("recipient", "")).strip()
                local_part, separator, domain = recipient.rpartition("@")
                if (separator == "@" and domain.lower() == "inbound.backbond.ai"
                        and local_part.startswith(STANDING_PREFIX)):
                    standing = None
                    try:
                        verify_signature(fields, signing_key)
                        raw_message = str(fields.get("body-mime", "")).encode()
                        standing = self._standing_store()
                        self._reply(200, standing.ingest_email(
                            raw_message, envelope_recipient=recipient
                        ))
                    except PermissionError as exc:
                        self._reply(401, {"error": str(exc)})
                    except ValueError as exc:
                        self._reply(406, {"error": str(exc)})
                    except RuntimeError:
                        self._reply(503, {"error": "standing_not_configured"})
                    finally:
                        if standing is not None:
                            standing.close()
                    return
                history = None
                try:
                    history = self._history_store()
                    preview_id = ingest_history_route(history, fields, signing_key)
                    self._reply(200, {"accepted": True, "preview_id": preview_id})
                except PermissionError as exc:
                    self._reply(401, {"error": str(exc)})
                except ValueError as exc:
                    self._reply(406, {"error": str(exc)})
                except RuntimeError:
                    self._reply(503, {"error": "history_not_configured"})
                finally:
                    if history is not None:
                        history.close()
                return
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
                if receipt.kind == "forwarded_confirmation":
                    outcome = store.link_confirmation(receipt.receipt_id)
                    if (outcome in {"answer_no", "answer_missing", "answer_late"}
                            and reminder_sender and not store.reminder_was_delivered(receipt.receipt_id)):
                        delivery_id = reminder_sender(store.cancellation_reminder_payload(receipt.receipt_id))
                        store.record_reminder_delivery(receipt.receipt_id, delivery_id)
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
    sender = MailgunCancellationReminderSender(api_url, api_key, from_address) if all((api_url, api_key, from_address)) else None
    history_key_hex = os.environ.get("BB_HISTORY_ENCRYPTION_KEY_HEX", "")
    history_key = bytes.fromhex(history_key_hex) if history_key_hex else None
    ThreadingHTTPServer((args.host, args.port), make_handler(
        args.db, signing_key, sender,
        history_db_path=os.environ.get("BB_HISTORY_DB"), history_key=history_key,
        history_external_enabled=os.environ.get("BB_HISTORY_EXTERNAL_ENABLED") == "1",
        history_synthetic_enabled=os.environ.get("BB_HISTORY_SYNTHETIC_ENABLED") == "1",
        standing_db_path=os.environ.get("BB_INSTINCT_STANDING_DB"),
        standing_synthetic_enabled=os.environ.get("BB_INSTINCT_STANDING_SYNTHETIC_ENABLED") == "1",
        public_base=os.environ.get("BB_STAGE_B_PUBLIC_BASE", "https://scanner.backbond.ai/stage-b-demo"),
    )).serve_forever()


if __name__ == "__main__":
    main()
