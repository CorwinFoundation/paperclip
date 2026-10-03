"""Consented, account-bound Instinct history import for consumer onboarding.

This is deliberately separate from the DEMO receipt mailbox.  It accepts a
provider-signed, single-use message, validates the complete report before it
creates a private preview, and never turns absent history into a risk result.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Callable


HISTORY_DOMAIN = "history.inbound.backbond.ai"
REPORT_VERSION = "instinct-history/v1"
NOTICE_VERSION = "instinct-history-notice/v1"
EXPECTED_ROWS = 51
MAX_MESSAGE_BYTES = 256_000
ROW_KEYS = frozenset({
    "occurred_at", "timezone", "task_id", "category", "proposed_action",
    "external_action", "approval_status", "approval_source", "action_taken",
    "outcome", "amount", "currency", "recovery", "evidence_origin", "gaps",
    "synthetic",
})
PROVENANCE = {"agent_reported", "user_confirmed", "independently_evidenced"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _seal(key: bytes, plaintext: bytes) -> bytes:
    """Authenticated at-rest envelope; callers must keep ``key`` out of DB."""
    nonce = secrets.token_bytes(16)
    stream = bytearray()
    counter = 0
    while len(stream) < len(plaintext):
        stream.extend(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream))
    tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    return nonce + tag + ciphertext


def _canonical_report_bytes(report: object) -> bytes:
    """Return the provider-signature representation, independent of MIME wrapping."""
    return json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


@dataclass(frozen=True)
class HistoryAddress:
    address: str
    expires_at: str


class HistoryImportStore:
    """Private history previews and the onboarding evidence state machine."""

    def __init__(self, path: str | Path, *, provider_secret: bytes, encryption_key: bytes,
                 external_ingestion_enabled: bool = False,
                 now: Callable[[], datetime] = _utcnow) -> None:
        if len(provider_secret) < 16 or len(encryption_key) < 32:
            raise ValueError("history_secret_too_short")
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.provider_secret = provider_secret
        self.encryption_key = encryption_key
        self.external_ingestion_enabled = external_ingestion_enabled
        self.now = now
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS history_requests (
              token_digest TEXT PRIMARY KEY, user_id TEXT NOT NULL,
              import_id TEXT NOT NULL UNIQUE, notice_version TEXT NOT NULL,
              scope_json TEXT NOT NULL, expires_at TEXT NOT NULL,
              consumed_at TEXT, state TEXT NOT NULL,
              failure_reason TEXT, consented_at TEXT NOT NULL,
              synthetic_only INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history_previews (
              preview_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
              token_digest TEXT NOT NULL UNIQUE REFERENCES history_requests(token_digest),
              report_json TEXT NOT NULL, raw_encrypted BLOB NOT NULL,
              provenance TEXT NOT NULL, state TEXT NOT NULL,
              created_at TEXT NOT NULL, confirmed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS history_events (
              event_id TEXT PRIMARY KEY, import_id TEXT NOT NULL,
              user_id TEXT NOT NULL, event_type TEXT NOT NULL,
              occurred_at TEXT NOT NULL
            );
            """
        )

    def issue_address(self, user_id: str, *, scope: dict, synthetic: bool = False,
                      ttl: timedelta = timedelta(hours=1)) -> HistoryAddress:
        if not user_id or not scope:
            raise ValueError("history_consent_scope_required")
        if not synthetic and not self.external_ingestion_enabled:
            raise PermissionError("history_external_ingestion_disabled")
        token = secrets.token_urlsafe(24)
        digest = hashlib.sha256(token.encode()).hexdigest()
        import_id = "hi_" + secrets.token_hex(12)
        now = self.now()
        expires = now + ttl
        self.connection.execute(
            "INSERT INTO history_requests VALUES(?,?,?,?,?,?,NULL,'awaiting_report',NULL,?,?)",
            (digest, user_id, import_id, NOTICE_VERSION, json.dumps(scope, sort_keys=True),
             _iso(expires), _iso(now), int(synthetic)),
        )
        self._event(import_id, user_id, "consent_recorded")
        self.connection.commit()
        return HistoryAddress(f"history+{token}@{HISTORY_DOMAIN}", _iso(expires))

    def decline(self, user_id: str) -> dict:
        return self.onboarding_state(user_id, declined=True)

    def onboarding_state(self, user_id: str, *, declined: bool = False) -> dict:
        row = self.connection.execute(
            "SELECT state,failure_reason FROM history_requests WHERE user_id=? ORDER BY consented_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        state = "history_not_supplied" if declined or row is None else row["state"]
        held = state != "confirmed"
        next_step = None
        if held:
            next_step = "Supply a scoped Instinct history report. Correct it and resend if validation failed."
        return {
            "history_state": state,
            "underwriting_evidence": "incomplete" if held else "history_confirmed",
            "history_informed_underwriting_review": "held" if held else "ready",
            "account_creation": "available",
            "observation_demo": "available",
            "insurance_or_coverage_activation": False,
            "failure_reason": None if row is None else row["failure_reason"],
            "next_step": next_step,
            "notice": "Information gathering only. This does not activate insurance or coverage.",
        }

    def ingest(self, raw_message: bytes, *, expected_user_id: str | None = None) -> str:
        if len(raw_message) > MAX_MESSAGE_BYTES:
            raise ValueError("history_payload_too_large")
        message = BytesParser(policy=policy.default).parsebytes(raw_message)
        if any(part.get_filename() or part.get_content_disposition() == "attachment" for part in message.walk()):
            raise ValueError("history_attachments_rejected")
        recipient = str(message.get("To", ""))
        prefix, separator, domain = recipient.rpartition("@")
        if separator != "@" or domain.lower() != HISTORY_DOMAIN or not prefix.startswith("history+"):
            raise PermissionError("history_address_invalid")
        token = prefix[len("history+"):]
        digest = hashlib.sha256(token.encode()).hexdigest()
        request = self.connection.execute("SELECT * FROM history_requests WHERE token_digest=?", (digest,)).fetchone()
        if request is None:
            raise PermissionError("history_token_invalid")
        if expected_user_id is not None and request["user_id"] != expected_user_id:
            raise PermissionError("history_account_mismatch")
        if _parse_time(request["expires_at"]) <= self.now():
            self._fail(digest, "history_token_expired")
            raise PermissionError("history_token_expired")
        if request["consumed_at"] is not None:
            raise PermissionError("history_token_replayed")
        plain = [part for part in message.walk() if part.get_content_type() == "text/plain"]
        if not plain:
            self._fail(digest, "history_text_plain_required")
            raise ValueError("history_text_plain_required")
        body = plain[0].get_content().strip()
        try:
            report = json.loads(body)
        except json.JSONDecodeError as exc:
            self._fail(digest, "history_json_invalid")
            raise ValueError("history_json_invalid") from exc
        signature = str(message.get("X-Instinct-Signature", "")).strip()
        expected = hmac.new(
            self.provider_secret, _canonical_report_bytes(report), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            self._fail(digest, "history_signature_invalid")
            raise PermissionError("history_signature_invalid")
        try:
            self._validate_report(report)
        except ValueError as exc:
            reason = str(exc)
            self._fail(digest, reason)
            raise ValueError(reason) from exc
        preview_id = "hp_" + secrets.token_hex(12)
        now = _iso(self.now())
        self.connection.execute(
            "INSERT INTO history_previews VALUES(?,?,?,?,?,'agent_reported','pending_confirmation',?,NULL)",
            (preview_id, request["user_id"], digest, json.dumps(report, sort_keys=True),
             _seal(self.encryption_key, body.encode()), now),
        )
        self.connection.execute(
            "UPDATE history_requests SET consumed_at=?,state='pending_confirmation',failure_reason=NULL WHERE token_digest=?",
            (now, digest),
        )
        self.connection.commit()
        return preview_id

    def _validate_report(self, report: object) -> None:
        if not isinstance(report, dict) or report.get("schema_version") != REPORT_VERSION:
            raise ValueError("history_schema_version_invalid")
        rows = report.get("rows")
        if not isinstance(rows, list) or len(rows) != EXPECTED_ROWS or report.get("row_count") != len(rows):
            raise ValueError("history_row_count_mismatch")
        if report.get("end_marker") != "END INSTINCT HISTORY":
            raise ValueError("history_end_marker_missing")
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or not ROW_KEYS.issubset(row):
                raise ValueError("history_row_keys_missing")
            key = str(row["task_id"])
            if key in seen:
                raise ValueError("history_duplicate_row")
            seen.add(key)
            if row["evidence_origin"] not in PROVENANCE:
                raise ValueError("history_provenance_invalid")

    def preview(self, user_id: str, preview_id: str) -> dict:
        row = self._owned_preview(user_id, preview_id)
        return {"preview_id": preview_id, "state": row["state"], "provenance": row["provenance"],
                "connected_source": "Instinct",
                "report": json.loads(row["report_json"])}

    def act(self, user_id: str, preview_id: str, action: str, *, corrected_report: dict | None = None) -> None:
        row = self._owned_preview(user_id, preview_id)
        digest = row["token_digest"]
        if action == "confirm":
            self.connection.execute(
                "UPDATE history_previews SET state='confirmed',provenance='user_confirmed',confirmed_at=? WHERE preview_id=?",
                (_iso(self.now()), preview_id),
            )
            self.connection.execute("UPDATE history_requests SET state='confirmed' WHERE token_digest=?", (digest,))
        elif action == "correct":
            if corrected_report is None:
                raise ValueError("history_correction_required")
            self._validate_report(corrected_report)
            self.connection.execute(
                "UPDATE history_previews SET report_json=?,provenance='user_confirmed',state='pending_confirmation' WHERE preview_id=?",
                (json.dumps(corrected_report, sort_keys=True), preview_id),
            )
        elif action in {"discard", "delete"}:
            self.connection.execute("DELETE FROM history_previews WHERE preview_id=?", (preview_id,))
            self.connection.execute(
                "UPDATE history_requests SET state='history_not_supplied',failure_reason=NULL WHERE token_digest=?", (digest,)
            )
            request = self.connection.execute(
                "SELECT import_id,user_id FROM history_requests WHERE token_digest=?", (digest,)
            ).fetchone()
            self._event(request["import_id"], request["user_id"],
                        "consent_withdrawn" if action == "discard" else "content_deleted")
        else:
            raise ValueError("history_preview_action_invalid")
        self.connection.commit()

    def _owned_preview(self, user_id: str, preview_id: str) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM history_previews WHERE preview_id=?", (preview_id,)).fetchone()
        if row is None or row["user_id"] != user_id:
            raise PermissionError("history_preview_not_found")
        return row

    def _fail(self, digest: str, reason: str) -> None:
        self.connection.execute(
            "UPDATE history_requests SET state='validation_failed',failure_reason=? WHERE token_digest=?", (reason, digest)
        )
        self.connection.commit()

    def _event(self, import_id: str, user_id: str, event_type: str) -> None:
        """Retain only non-content consent/security lifecycle evidence."""
        self.connection.execute(
            "INSERT INTO history_events VALUES(?,?,?,?,?)",
            ("he_" + secrets.token_hex(12), import_id, user_id, event_type, _iso(self.now())),
        )
