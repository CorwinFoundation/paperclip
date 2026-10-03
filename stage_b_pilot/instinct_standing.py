"""Synthetic-only standing Instinct reporting grant for the Stage B pilot."""
from __future__ import annotations

import hashlib
import html
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path
from typing import Callable


SCHEMA_VERSION = "instinct-standing-report/v1"
EVENT_KEYS = frozenset({
    "occurred_at", "category", "proposed_external_action",
    "actual_external_action", "approval_source", "approval_status",
    "money_at_risk", "outcome", "evidence_origin", "gaps", "synthetic",
})
STANDING_DOMAIN = "inbound.backbond.ai"
STANDING_PREFIX = "history+standing-"
MAX_REPORT_BYTES = 64_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class StandingReportStore:
    """Account-bound, revocable consent grants and deduplicated minimal reports."""

    def __init__(self, path: str | Path, *, now: Callable[[], datetime] = _utcnow) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.now = now
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS instinct_standing_grants (
          grant_digest TEXT PRIMARY KEY, user_id TEXT NOT NULL,
          sender_email TEXT NOT NULL, created_at TEXT NOT NULL,
          expires_at TEXT NOT NULL, revoked_at TEXT,
          history_address TEXT, history_expires_at TEXT
        );
        CREATE TABLE IF NOT EXISTS instinct_standing_reports (
          report_id TEXT PRIMARY KEY, grant_digest TEXT NOT NULL,
          deduplication_key TEXT NOT NULL, received_at TEXT NOT NULL,
          report_json TEXT NOT NULL, provenance TEXT NOT NULL,
          UNIQUE(grant_digest, deduplication_key)
        );
        """)
        columns = {row[1] for row in self.connection.execute(
            "PRAGMA table_info(instinct_standing_grants)"
        )}
        for name in ("history_address", "history_expires_at"):
            if name not in columns:
                self.connection.execute(
                    f"ALTER TABLE instinct_standing_grants ADD COLUMN {name} TEXT"
                )
        self.connection.commit()

    def issue(self, user_id: str, sender_email: str, *,
              history_address: str | None = None,
              history_expires_at: str | None = None,
              ttl: timedelta = timedelta(hours=24)) -> dict:
        if not user_id or "@" not in sender_email or ttl <= timedelta(0):
            raise ValueError("standing_grant_invalid")
        token = secrets.token_urlsafe(18)
        digest = hashlib.sha256(token.encode()).hexdigest()
        expires = self.now() + ttl
        self.connection.execute(
            "INSERT INTO instinct_standing_grants "
            "(grant_digest,user_id,sender_email,created_at,expires_at,revoked_at,"
            "history_address,history_expires_at) VALUES(?,?,?,?,?,NULL,?,?)",
            (digest, user_id, sender_email.strip().lower(), _iso(self.now()), _iso(expires),
             history_address, history_expires_at),
        )
        self.connection.commit()
        return {"token": token, "grant_id": digest, "expires_at": _iso(expires),
                "address": f"{STANDING_PREFIX}{token}@{STANDING_DOMAIN}"}

    def grants_for(self, user_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT grant_digest,sender_email,created_at,expires_at,revoked_at,history_expires_at "
            "FROM instinct_standing_grants WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        )
        return [{"grant_id": row["grant_digest"], "sender_email": row["sender_email"],
                 "created_at": row["created_at"],
                 "expires_at": row["expires_at"],
                 "history_expires_at": row["history_expires_at"],
                 "state": ("revoked" if row["revoked_at"] else
                           "expired" if datetime.fromisoformat(
                               row["expires_at"].replace("Z", "+00:00")) <= self.now()
                           else "active")}
                for row in rows]

    def pending_history_address(self, user_id: str, grant_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT history_address FROM instinct_standing_grants "
            "WHERE grant_digest=? AND user_id=? AND revoked_at IS NULL",
            (grant_id, user_id),
        ).fetchone()
        if row is None:
            raise PermissionError("standing_grant_not_found")
        return row["history_address"]

    def revoke(self, user_id: str, grant_id: str) -> None:
        changed = self.connection.execute(
            "UPDATE instinct_standing_grants SET revoked_at=? "
            "WHERE grant_digest=? AND user_id=? AND revoked_at IS NULL",
            (_iso(self.now()), grant_id, user_id),
        ).rowcount
        self.connection.commit()
        if changed != 1:
            raise PermissionError("standing_grant_not_found")

    def grant(self, token: str) -> sqlite3.Row:
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = self.connection.execute(
            "SELECT * FROM instinct_standing_grants WHERE grant_digest=?", (digest,)
        ).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise PermissionError("standing_grant_revoked_or_invalid")
        if datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")) <= self.now():
            raise PermissionError("standing_grant_expired")
        return row

    def ingest(self, token: str, sender_email: str, payload: object) -> dict:
        grant = self.grant(token)
        # Address matching is a scope check, not proof of Instinct identity.
        if sender_email.strip().lower() != grant["sender_email"]:
            raise PermissionError("standing_sender_mismatch")
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version", "deduplication_key", "trigger", "event"
        } or payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("standing_schema_invalid")
        key = payload.get("deduplication_key")
        if not isinstance(key, str) or not 8 <= len(key) <= 128:
            raise ValueError("standing_deduplication_key_invalid")
        if payload.get("trigger") not in {"onboarding_history", "request_completed", "request_failed"}:
            raise ValueError("standing_trigger_invalid")
        event = payload.get("event")
        if not isinstance(event, dict) or set(event) != EVENT_KEYS:
            raise ValueError("standing_event_keys_invalid")
        if event["synthetic"] is not True or event["evidence_origin"] != "agent_reported":
            raise ValueError("standing_synthetic_provenance_required")
        for field in EVENT_KEYS - {"synthetic"}:
            if not isinstance(event[field], str) or len(event[field]) > 500:
                raise ValueError("standing_event_value_invalid")
        report_id = "sr_" + secrets.token_hex(12)
        digest = hashlib.sha256(token.encode()).hexdigest()
        try:
            self.connection.execute(
                "INSERT INTO instinct_standing_reports VALUES(?,?,?,?,?,'agent_reported')",
                (report_id, digest, key, _iso(self.now()), json.dumps(payload, sort_keys=True)),
            )
            self.connection.commit()
            return {"state": "received", "report_id": report_id, "review": "staff_required"}
        except sqlite3.IntegrityError:
            row = self.connection.execute(
                "SELECT report_id FROM instinct_standing_reports WHERE grant_digest=? AND deduplication_key=?",
                (digest, key),
            ).fetchone()
            return {"state": "duplicate", "report_id": row["report_id"], "review": "staff_required"}

    def ingest_email(self, raw_message: bytes, *, envelope_recipient: str = "") -> dict:
        if not raw_message or len(raw_message) > MAX_REPORT_BYTES:
            raise ValueError("standing_email_missing_or_too_large")
        message = BytesParser(policy=policy.default).parsebytes(raw_message)
        if any(part.get_filename() or part.get_content_disposition() == "attachment"
               for part in message.walk()):
            raise ValueError("standing_attachments_rejected")
        recipient = parseaddr(str(message.get("To", "")))[1]
        prefix, separator, domain = recipient.rpartition("@")
        if (separator != "@" or domain.lower() != STANDING_DOMAIN
                or not prefix.startswith(STANDING_PREFIX)):
            raise PermissionError("standing_address_invalid")
        if envelope_recipient:
            envelope_prefix, envelope_separator, envelope_domain = envelope_recipient.rpartition("@")
            if (envelope_separator != "@" or envelope_prefix != prefix
                    or envelope_domain.lower() != domain.lower()):
                raise PermissionError("standing_envelope_recipient_mismatch")
        plain = [part for part in message.walk() if part.get_content_type() == "text/plain"]
        if len(plain) != 1:
            raise ValueError("standing_text_plain_required")
        try:
            payload = json.loads(plain[0].get_content().strip())
        except json.JSONDecodeError as exc:
            raise ValueError("standing_json_invalid") from exc
        return self.ingest(prefix[len(STANDING_PREFIX):],
                           parseaddr(str(message.get("From", "")))[1], payload)

    def reports_for(self, user_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT r.report_id,r.received_at,r.report_json,r.provenance "
            "FROM instinct_standing_reports r JOIN instinct_standing_grants g "
            "ON r.grant_digest=g.grant_digest WHERE g.user_id=? ORDER BY r.received_at DESC",
            (user_id,),
        )
        return [{"report_id": row["report_id"], "received_at": row["received_at"],
                 "provenance": row["provenance"], "review": "staff_required",
                 "report": json.loads(row["report_json"])} for row in rows]

    def close(self) -> None:
        self.connection.close()


def render_instruction(token: str, expires_at: str, sender_email: str, public_base: str,
                       history_address: str | None = None,
                       history_expires_at: str | None = None) -> str:
    """Self-contained page opened by Instinct after the user's short instruction."""
    endpoint = f"{public_base.rstrip('/')}/instinct/report/{token}"
    address = f"{STANDING_PREFIX}{token}@{STANDING_DOMAIN}"
    fields = ", ".join(sorted(EVENT_KEYS - {"synthetic"})) + ", synthetic"
    content = (
        "Synthetic Stage B only. First send one multi-row onboarding history email to the "
        "separate one-time onboarding address shown below. Then send one request_completed "
        "or request_failed report after each later fictional user request. "
        f"Do not wait for another user message. For those later reports, send one plain-text "
        f"JSON email from {sender_email} to {address}. Do not attach files, CC, or BCC. "
        f"Schema: {SCHEMA_VERSION}. Required top-level fields: schema_version, "
        "deduplication_key, trigger, event. Event fields: " + fields + ". "
        "The deduplication_key must be stable for the same request and outcome. Use unknown "
        "for unavailable facts. Set evidence_origin to agent_reported and synthetic to true. "
        "Include only a minimal event summary and decision evidence. Never include full message "
        "bodies, attachments, credentials, payment details, or unrelated third-party personal data. "
        "This grant expires at " + expires_at + ". If email delivery fails, stop and tell the "
        "user. BackBond records received and duplicate states privately; email delivery does not "
        "show webhook HTTP responses to Instinct. The user can view status or revoke this grant "
        "from the signed-in BackBond page. No per-report confirmation is required. "
        "Reports remain agent-reported and require staff review before reliance. Sender address "
        "matching does not prove Instinct identity. No polling or follow-up-question channel is enabled."
    )
    from .receipt_page import history_instruction
    full_history_instruction = (history_instruction(history_address, sender_email, 90,
                                                    synthetic=True)
                                if history_address else "No onboarding request was issued.")
    history = ("<h2>One-time onboarding history address</h2><code>" +
               html.escape(history_address or "not issued") + "</code><p>Expires " +
               html.escape(history_expires_at or "not issued") +
               ". Send the validated instinct-history/v1 multi-row report here.</p>" +
               "<h3>Complete onboarding history instruction</h3><pre style='white-space:pre-wrap'>" +
               html.escape(full_history_instruction) + "</pre>")
    example_event = {field: "unknown" for field in EVENT_KEYS - {"synthetic"}}
    example_event.update(evidence_origin="agent_reported", synthetic=True)
    example = json.dumps({"schema_version": SCHEMA_VERSION,
                          "deduplication_key": "fictional-request-001",
                          "trigger": "request_completed", "event": example_event},
                         sort_keys=True, indent=2)
    return "<!doctype html><html lang=en><head><meta charset=utf-8><meta name=robots content='noindex,nofollow,noarchive'><title>BackBond Instinct pilot</title></head><body><main><h1>BackBond standing report instruction</h1><p id=scope>" + html.escape(content) + "</p>" + history + "<h2>Later per-request reports</h2><code>" + html.escape(address) + "</code><h3>Exact later-report JSON shape</h3><pre>" + html.escape(example) + "</pre><h2>Synthetic test POST transport (not an observed Instinct capability)</h2><code>" + html.escape(endpoint) + "</code><h2>Authorized sender</h2><code>" + html.escape(sender_email) + "</code></main></body></html>"
