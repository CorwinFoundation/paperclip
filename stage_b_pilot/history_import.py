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
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path
from typing import Callable


HISTORY_DOMAIN = "inbound.backbond.ai"
REPORT_VERSION = "instinct-history/v1"
NOTICE_VERSION = "instinct-history-notice/v2"
MAX_ROWS = 500
MAX_MESSAGE_BYTES = 256_000
ROW_KEYS = frozenset({
    "occurred_at", "timezone", "task_id", "category", "proposed_action",
    "external_action", "approval_status", "approval_source", "action_taken",
    "outcome", "amount", "currency", "recovery", "evidence_origin", "gaps",
    "synthetic",
})
PROVENANCE = {"agent_reported", "user_confirmed", "independently_evidenced"}
CORRECTABLE_FIELDS = {"occurred_at", "timezone", "task_id", "category", "proposed_action",
                      "external_action", "approval_status", "approval_source",
                      "action_taken", "outcome", "amount", "currency", "recovery",
                      "gaps"}


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


def _open(key: bytes, sealed: bytes) -> bytes:
    if len(sealed) < 48:
        raise ValueError("history_ciphertext_invalid")
    nonce, tag, ciphertext = sealed[:16], sealed[16:48], sealed[48:]
    expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("history_ciphertext_invalid")
    stream = bytearray()
    counter = 0
    while len(stream) < len(ciphertext):
        stream.extend(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(a ^ b for a, b in zip(ciphertext, stream))


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
                 unconfirmed_retention: timedelta = timedelta(hours=24),
                 confirmed_retention: timedelta = timedelta(days=30),
                 event_retention: timedelta = timedelta(days=90),
                 now: Callable[[], datetime] = _utcnow) -> None:
        if len(provider_secret) < 16 or len(encryption_key) < 32:
            raise ValueError("history_secret_too_short")
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.provider_secret = provider_secret
        self.encryption_key = encryption_key
        self.external_ingestion_enabled = external_ingestion_enabled
        self.unconfirmed_retention = unconfirmed_retention
        self.confirmed_retention = confirmed_retention
        self.event_retention = event_retention
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
              synthetic_only INTEGER NOT NULL, tenant_id TEXT NOT NULL DEFAULT '',
              account_id TEXT NOT NULL DEFAULT '', agent_id TEXT NOT NULL DEFAULT '',
              purpose TEXT NOT NULL DEFAULT 'onboarding'
            );
            CREATE TABLE IF NOT EXISTS history_previews (
              preview_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
              token_digest TEXT NOT NULL UNIQUE REFERENCES history_requests(token_digest),
              report_json TEXT NOT NULL, raw_encrypted BLOB NOT NULL,
              provenance TEXT NOT NULL, state TEXT NOT NULL,
              created_at TEXT NOT NULL, confirmed_at TEXT,
              retention_expires_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS history_events (
              event_id TEXT PRIMARY KEY, import_id TEXT NOT NULL,
              user_id TEXT NOT NULL, event_type TEXT NOT NULL,
              occurred_at TEXT NOT NULL, retention_expires_at TEXT NOT NULL DEFAULT ''
            );
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(history_requests)")}
        if "purpose" not in columns:
            self.connection.execute(
                "ALTER TABLE history_requests ADD COLUMN purpose TEXT NOT NULL DEFAULT 'onboarding'"
            )
        for name in ("tenant_id", "account_id", "agent_id"):
            if name not in columns:
                self.connection.execute(
                    f"ALTER TABLE history_requests ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                )
        preview_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(history_previews)")}
        if "retention_expires_at" not in preview_columns:
            self.connection.execute(
                "ALTER TABLE history_previews ADD COLUMN retention_expires_at TEXT NOT NULL DEFAULT ''"
            )
        event_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(history_events)")}
        if "retention_expires_at" not in event_columns:
            self.connection.execute(
                "ALTER TABLE history_events ADD COLUMN retention_expires_at TEXT NOT NULL DEFAULT ''"
            )
        # Rows created before retention was configurable must not become immortal.
        preview_cutoff = _iso(self.now() + self.unconfirmed_retention)
        event_cutoff = _iso(self.now() + self.event_retention)
        self.connection.execute(
            "UPDATE history_previews SET retention_expires_at=? "
            "WHERE retention_expires_at=''",
            (preview_cutoff,),
        )
        self.connection.execute(
            "UPDATE history_events SET retention_expires_at=? "
            "WHERE retention_expires_at=''",
            (event_cutoff,),
        )
        self.connection.commit()

    def binding_for_address(self, address: str) -> dict[str, str]:
        """Resolve the immutable request binding for authenticated transport use."""
        local, separator, domain = address.strip().rpartition("@")
        if separator != "@" or domain.lower() != HISTORY_DOMAIN or not local.startswith("history+"):
            raise PermissionError("history_address_invalid")
        digest = hashlib.sha256(local[len("history+"):].encode()).hexdigest()
        row = self.connection.execute(
            "SELECT user_id,tenant_id,account_id,agent_id FROM history_requests "
            "WHERE token_digest=?", (digest,),
        ).fetchone()
        if row is None:
            raise PermissionError("history_token_invalid")
        return dict(row)

    def issue_address(self, user_id: str, *, scope: dict, tenant_id: str | None = None,
                      account_id: str | None = None, agent_id: str | None = None,
                      synthetic: bool = False,
                      purpose: str = "onboarding",
                      ttl: timedelta = timedelta(hours=1)) -> HistoryAddress:
        if not user_id or not scope:
            raise ValueError("history_consent_scope_required")
        tenant_id = tenant_id or user_id
        account_id = account_id or user_id
        agent_id = agent_id or str(scope.get("agent_id", user_id))
        if purpose not in {"onboarding", "support"}:
            raise ValueError("history_purpose_invalid")
        if not synthetic and not self.external_ingestion_enabled:
            raise PermissionError("history_external_ingestion_disabled")
        token = secrets.token_urlsafe(24)
        digest = hashlib.sha256(token.encode()).hexdigest()
        import_id = "hi_" + secrets.token_hex(12)
        now = self.now()
        expires = now + ttl
        self.connection.execute(
            "INSERT INTO history_requests "
            "(token_digest,user_id,import_id,notice_version,scope_json,expires_at,"
            "consumed_at,state,failure_reason,consented_at,synthetic_only,tenant_id,account_id,agent_id,purpose) "
            "VALUES(?,?,?,?,?,?,NULL,'awaiting_report',NULL,?,?,?,?,?,?)",
            (digest, user_id, import_id, NOTICE_VERSION, json.dumps(scope, sort_keys=True),
             _iso(expires), _iso(now), int(synthetic), tenant_id, account_id, agent_id, purpose),
        )
        self._event(import_id, user_id, "consent_recorded")
        self.connection.commit()
        return HistoryAddress(f"history+{token}@{HISTORY_DOMAIN}", _iso(expires))

    def decline(self, user_id: str) -> dict:
        return self.onboarding_state(user_id, declined=True)

    def revoke_pending_address(self, user_id: str, address: str) -> None:
        """Stop an unused one-time request linked to a revoked standing grant."""
        local, separator, domain = address.rpartition("@")
        if separator != "@" or domain.lower() != HISTORY_DOMAIN or not local.startswith("history+"):
            raise PermissionError("history_address_invalid")
        digest = hashlib.sha256(local[len("history+"):].encode()).hexdigest()
        row = self.connection.execute(
            "SELECT import_id,consumed_at FROM history_requests WHERE token_digest=? AND user_id=?",
            (digest, user_id),
        ).fetchone()
        if row is None:
            raise PermissionError("history_account_mismatch")
        if row["consumed_at"] is None:
            self.connection.execute(
                "UPDATE history_requests SET state='revoked' WHERE token_digest=? AND user_id=?",
                (digest, user_id),
            )
            self._event(row["import_id"], user_id, "consent_revoked")
            self.connection.commit()

    def onboarding_state(self, user_id: str, *, declined: bool = False,
                         purpose: str = "onboarding", tenant_id: str | None = None,
                         account_id: str | None = None, agent_id: str | None = None) -> dict:
        binding_supplied = any(value is not None for value in (tenant_id, account_id, agent_id))
        tenant_id, account_id, agent_id = self._binding_defaults(
            user_id, tenant_id, account_id, agent_id)
        binding_clause = " AND tenant_id=? AND account_id=? AND agent_id=?" if binding_supplied else ""
        parameters = ((user_id, purpose, tenant_id, account_id, agent_id) if binding_supplied
                      else (user_id, purpose))
        row = self.connection.execute(
            "SELECT state,failure_reason FROM history_requests WHERE user_id=? AND purpose=? "
            + binding_clause + " "
            "ORDER BY rowid DESC LIMIT 1",
            parameters,
        ).fetchone()
        state = "history_not_supplied" if declined or row is None else row["state"]
        held = state not in {"received", "confirmed"}
        next_step = None
        if held:
            next_step = "Ask Instinct to send the scoped report or resend it if validation failed."
        elif state == "received":
            next_step = "Underwriting or support staff should review agent-reported facts before relying on them."
        return {
            "history_state": state,
            "underwriting_evidence": ("incomplete" if held else
                                      "agent_reported_received" if state == "received" else
                                      "history_confirmed"),
            "history_informed_underwriting_review": "held" if held else "ready",
            "account_creation": "available",
            "observation_demo": "available",
            "insurance_or_coverage_activation": False,
            "failure_reason": None if row is None else row["failure_reason"],
            "next_step": next_step,
            "notice": "Information gathering only. This does not activate insurance or coverage.",
        }

    def imports_for(self, user_id: str, *, purpose: str | None = None,
                    tenant_id: str | None = None, account_id: str | None = None,
                    agent_id: str | None = None) -> list[dict]:
        """Return only this user's preview metadata and report for private review."""
        binding_supplied = any(value is not None for value in (tenant_id, account_id, agent_id))
        query = (
            "SELECT p.preview_id,p.state,p.provenance,p.created_at,p.confirmed_at,"
            "p.report_json,r.purpose FROM history_previews p "
            "JOIN history_requests r ON p.token_digest=r.token_digest WHERE p.user_id=?"
        )
        tenant_id, account_id, agent_id = self._binding_defaults(
            user_id, tenant_id, account_id, agent_id)
        parameters: tuple[str, ...] = (user_id,)
        if binding_supplied:
            query += " AND r.tenant_id=? AND r.account_id=? AND r.agent_id=?"
            parameters += (tenant_id, account_id, agent_id)
        if purpose is not None:
            query += " AND r.purpose=?"
            parameters += (purpose,)
        query += " ORDER BY p.created_at DESC"
        return [dict(preview_id=row["preview_id"], state=row["state"],
                     provenance=row["provenance"], connected_source="Instinct",
                     created_at=row["created_at"], confirmed_at=row["confirmed_at"],
                     purpose=row["purpose"], report=self._decode_report(row["report_json"]))
                for row in self.connection.execute(query, parameters)]

    def close(self) -> None:
        self.connection.close()

    def _decode_report(self, stored: str | bytes) -> dict:
        # Legacy synthetic previews were plaintext JSON. New previews are sealed.
        if isinstance(stored, str):
            return json.loads(stored)
        return json.loads(_open(self.encryption_key, stored))

    def ingest(self, raw_message: bytes, *, expected_user_id: str | None = None,
               expected_tenant_id: str | None = None, expected_account_id: str | None = None,
               expected_agent_id: str | None = None,
               transport_authenticated: bool = False) -> str:
        if len(raw_message) > MAX_MESSAGE_BYTES:
            raise ValueError("history_payload_too_large")
        message = BytesParser(policy=policy.default).parsebytes(raw_message)
        if any(part.get_filename() or part.get_content_disposition() == "attachment" for part in message.walk()):
            raise ValueError("history_attachments_rejected")
        recipient = parseaddr(str(message.get("To", "")))[1]
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
        for expected, key in ((expected_tenant_id, "tenant_id"),
                              (expected_account_id, "account_id"),
                              (expected_agent_id, "agent_id")):
            if expected is not None and request[key] != expected:
                raise PermissionError("history_account_mismatch")
        if request["state"] == "revoked":
            raise PermissionError("history_token_revoked")
        if _parse_time(request["expires_at"]) <= self.now():
            self._fail(digest, "history_token_expired")
            raise PermissionError("history_token_expired")
        if request["consumed_at"] is not None:
            raise PermissionError("history_token_replayed")
        scope = json.loads(request["scope_json"])
        sender = parseaddr(str(message.get("From", "")))[1].lower()
        expected_sender = str(scope.get("sender_email", "")).strip().lower()
        if expected_sender and sender != expected_sender:
            self._fail(digest, "history_sender_mismatch")
            raise PermissionError("history_sender_mismatch")
        allowed_parts = {"text/plain", "text/html", "multipart/alternative"}
        if any(part.get_content_type() not in allowed_parts for part in message.walk()):
            self._fail(digest, "history_mime_unsupported")
            raise ValueError("history_mime_unsupported")
        plain = [part for part in message.walk() if part.get_content_type() == "text/plain"]
        if len(plain) != 1:
            self._fail(digest, "history_text_plain_required")
            raise ValueError("history_text_plain_required")
        body = plain[0].get_content().strip()
        try:
            report = json.loads(body)
        except json.JSONDecodeError as exc:
            self._fail(digest, "history_json_invalid")
            raise ValueError("history_json_invalid") from exc
        # A user-directed Instinct email cannot carry a shared Instinct signing
        # secret. The production adapter authenticates Mailgun's webhook before
        # calling this method; the HMAC below remains for direct synthetic tests.
        if not transport_authenticated:
            signature = str(message.get("X-Instinct-Signature", "")).strip()
            expected = hmac.new(
                self.provider_secret, _canonical_report_bytes(report), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                self._fail(digest, "history_signature_invalid")
                raise PermissionError("history_signature_invalid")
        try:
            self._validate_report(report)
            self._validate_scope(report, scope)
            if not bool(request["synthetic_only"]) and any(
                row["synthetic"] for row in report["rows"]
            ):
                raise ValueError("history_synthetic_content_rejected")
            if bool(request["synthetic_only"]) and any(
                not row["synthetic"] for row in report["rows"]
            ):
                raise ValueError("history_test_requires_synthetic_rows")
        except ValueError as exc:
            reason = str(exc)
            self._fail(digest, reason)
            raise ValueError(reason) from exc
        preview_id = "hp_" + secrets.token_hex(12)
        now = _iso(self.now())
        # The v2 notice authorizes scoped receipt without a second user action.
        # Older requests retain their promised review/confirmation step.
        state = "received" if request["notice_version"] == NOTICE_VERSION else "pending_confirmation"
        raw = b"" if state == "received" else _seal(self.encryption_key, body.encode())
        self.connection.execute(
            "INSERT INTO history_previews "
            "(preview_id,user_id,token_digest,report_json,raw_encrypted,provenance,state,created_at,confirmed_at,retention_expires_at) "
            "VALUES(?,?,?,?,?,'agent_reported',?,?,NULL,?)",
            (preview_id, request["user_id"], digest,
             _seal(self.encryption_key, _canonical_report_bytes(report)),
             raw, state, now, _iso(self.now() + self.unconfirmed_retention)),
        )
        self.connection.execute(
            "UPDATE history_requests SET consumed_at=?,state=?,failure_reason=NULL WHERE token_digest=?",
            (now, state, digest),
        )
        self.connection.commit()
        return preview_id

    def _validate_report(self, report: object) -> None:
        if not isinstance(report, dict) or report.get("schema_version") != REPORT_VERSION:
            raise ValueError("history_schema_version_invalid")
        if report.get("end_marker") != "END INSTINCT HISTORY":
            raise ValueError("history_end_marker_missing")
        if set(report) != {"schema_version", "row_count", "rows", "end_marker"}:
            raise ValueError("history_report_keys_invalid")
        rows = report.get("rows")
        if (not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS
                or report.get("row_count") != len(rows)):
            raise ValueError("history_row_count_mismatch")
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != ROW_KEYS:
                raise ValueError("history_row_keys_missing")
            if any(not isinstance(row[key], str) or len(row[key]) > 500
                   for key in ROW_KEYS - {"synthetic"}):
                raise ValueError("history_row_value_invalid")
            key = str(row["task_id"]).strip()
            if key and key.lower() != "unknown":
                if key in seen:
                    raise ValueError("history_duplicate_row")
                seen.add(key)
            if row["evidence_origin"] not in PROVENANCE:
                raise ValueError("history_provenance_invalid")
            if row["evidence_origin"] != "agent_reported":
                raise ValueError("history_import_provenance_unverified")
            if not isinstance(row["synthetic"], bool):
                raise ValueError("history_synthetic_flag_invalid")

    def _validate_scope(self, report: dict, scope: dict) -> None:
        days = scope.get("lookback_days")
        if not isinstance(days, int) or not 1 <= days <= 365:
            raise ValueError("history_scope_invalid")
        earliest = self.now().date() - timedelta(days=days)
        latest = self.now().date() + timedelta(days=1)
        for row in report["rows"]:
            stamp = row["occurred_at"].strip()
            if stamp.lower() == "unknown":
                continue
            try:
                occurred = date.fromisoformat(stamp[:10])
            except ValueError as exc:
                raise ValueError("history_date_invalid") from exc
            if not earliest <= occurred <= latest:
                raise ValueError("history_outside_selected_lookback")

    def preview(self, user_id: str, preview_id: str, *, tenant_id: str | None = None,
                account_id: str | None = None, agent_id: str | None = None) -> dict:
        row = self._owned_preview(user_id, preview_id, tenant_id, account_id, agent_id)
        return {"preview_id": preview_id, "state": row["state"], "provenance": row["provenance"],
                "connected_source": "Instinct",
                "report": self._decode_report(row["report_json"])}

    def act(self, user_id: str, preview_id: str, action: str, *, corrected_report: dict | None = None,
            tenant_id: str | None = None, account_id: str | None = None,
            agent_id: str | None = None) -> None:
        row = self._owned_preview(user_id, preview_id, tenant_id, account_id, agent_id)
        if action in {"confirm", "correct", "discard"} and row["state"] != "pending_confirmation":
            raise ValueError("history_preview_not_editable")
        digest = row["token_digest"]
        if action == "confirm":
            self.connection.execute(
                "UPDATE history_previews SET state='confirmed',raw_encrypted=x'',confirmed_at=?,retention_expires_at=? WHERE preview_id=?",
                (_iso(self.now()), _iso(self.now() + self.confirmed_retention), preview_id),
            )
            self.connection.execute("UPDATE history_requests SET state='confirmed' WHERE token_digest=?", (digest,))
        elif action == "correct":
            if corrected_report is None:
                raise ValueError("history_correction_required")
            self._validate_report(corrected_report)
            self.connection.execute(
                "UPDATE history_previews SET report_json=?,provenance='user_confirmed',state='pending_confirmation' WHERE preview_id=?",
                (_seal(self.encryption_key, _canonical_report_bytes(corrected_report)), preview_id),
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

    def correct_field(self, user_id: str, preview_id: str, row_index: int,
                      field: str, value: str, *, tenant_id: str | None = None,
                      account_id: str | None = None, agent_id: str | None = None) -> None:
        """Keep the agent's value and attribute a separate correction to the user."""
        preview = self._owned_preview(user_id, preview_id, tenant_id, account_id, agent_id)
        if preview["state"] != "pending_confirmation":
            raise ValueError("history_preview_not_editable")
        if field not in CORRECTABLE_FIELDS or len(value) > 500:
            raise ValueError("history_correction_invalid")
        report = self._decode_report(preview["report_json"])
        rows = report["rows"]
        if not 0 <= row_index < len(rows):
            raise ValueError("history_row_index_invalid")
        rows[row_index].setdefault("user_corrections", {})[field] = value
        self.connection.execute(
            "UPDATE history_previews SET report_json=? WHERE preview_id=?",
            (_seal(self.encryption_key, _canonical_report_bytes(report)), preview_id),
        )
        self.connection.commit()

    def _owned_preview(self, user_id: str, preview_id: str, tenant_id: str | None = None,
                       account_id: str | None = None, agent_id: str | None = None) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT p.*,r.tenant_id,r.account_id,r.agent_id FROM history_previews p "
            "JOIN history_requests r ON r.token_digest=p.token_digest WHERE p.preview_id=?",
            (preview_id,),
        ).fetchone()
        if row is None or row["user_id"] != user_id:
            raise PermissionError("history_preview_not_found")
        for expected, key in ((tenant_id, "tenant_id"), (account_id, "account_id"), (agent_id, "agent_id")):
            if expected is not None and row[key] != expected:
                raise PermissionError("history_preview_not_found")
        return row

    @staticmethod
    def _binding_defaults(user_id: str, tenant_id: str | None,
                          account_id: str | None, agent_id: str | None) -> tuple[str, str, str]:
        return tenant_id or user_id, account_id or user_id, agent_id or user_id

    def purge_expired(self) -> dict[str, int]:
        now = _iso(self.now())
        expired = self.connection.execute(
            "SELECT token_digest FROM history_previews WHERE retention_expires_at!='' AND retention_expires_at<=?",
            (now,),
        ).fetchall()
        for row in expired:
            self.connection.execute("DELETE FROM history_previews WHERE token_digest=?", (row["token_digest"],))
            self.connection.execute(
                "UPDATE history_requests SET state='history_not_supplied' WHERE token_digest=?",
                (row["token_digest"],),
            )
        events = self.connection.execute(
            "DELETE FROM history_events WHERE retention_expires_at!='' AND retention_expires_at<=?", (now,)
        ).rowcount
        self.connection.commit()
        return {"content_deleted": len(expired), "events_deleted": events}

    def _fail(self, digest: str, reason: str) -> None:
        self.connection.execute(
            "UPDATE history_requests SET state='validation_failed',failure_reason=? WHERE token_digest=?", (reason, digest)
        )
        self.connection.commit()

    def _event(self, import_id: str, user_id: str, event_type: str) -> None:
        """Retain only non-content consent/security lifecycle evidence."""
        self.connection.execute(
            "INSERT INTO history_events "
            "(event_id,import_id,user_id,event_type,occurred_at,retention_expires_at) VALUES(?,?,?,?,?,?)",
            ("he_" + secrets.token_hex(12), import_id, user_id, event_type, _iso(self.now()),
             _iso(self.now() + self.event_retention)),
        )
