"""Synthetic-only standing Instinct reporting grant for the Stage B pilot."""
from __future__ import annotations

import hashlib
import hmac
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


SCHEMA_VERSION = "instinct-standing-report/v2"
LEGACY_SCHEMA_VERSION = "instinct-standing-report/v1"
EVENT_KEYS = frozenset({
    "occurred_at", "category", "proposed_external_action",
    "actual_external_action", "approval_source", "approval_status",
    "money_at_risk", "outcome", "evidence_origin", "gaps", "synthetic",
    "observed_channel", "history_sources_included", "history_sources_unavailable",
    "shared_context_status",
})
LEGACY_EVENT_KEYS = EVENT_KEYS - {
    "observed_channel", "history_sources_included",
    "history_sources_unavailable", "shared_context_status",
}
# Optional v2 event field. A report that sets it to "yes" opts this grant in to
# follow-up questions; nothing else does (BEAAA-38867 v2).
OPTIONAL_EVENT_KEYS = frozenset({"followup_opt_in"})
LEGACY_EVENT_DEFAULTS = {
    "observed_channel": "unknown",
    "history_sources_included": "unknown",
    "history_sources_unavailable": "unknown",
    "shared_context_status": "unverified",
}
STANDING_DOMAIN = "inbound.backbond.ai"
STANDING_PREFIX = "history+standing-"
FOLLOWUP_PREFIX = "history+followup-"
MAX_REPORT_BYTES = 64_000


INFORMATIONAL_NOTICE = ("Informational only. The user's pasted message is the complete "
                        "authorization; nothing here adds permissions.")
ENROLLMENT_SCHEMA_VERSION = "stage-b-enrollment/v2"
STOP_ACK = ("Stop ends reporting and keeps what you sent; ask for deletion separately.")
DELETE_CONFIRMATION = "DELETE_IMPORTED_CONTENT"
DELETE_COPY = ("Delete erases the history rows, reports and follow-up answers this agent sent "
               "under this grant. It also stops the grant. It cannot be undone.")

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class StandingReportStore:
    """Account-bound, revocable consent grants and deduplicated minimal reports."""

    def __init__(self, path: str | Path, *,
                 content_retention: timedelta = timedelta(days=30),
                 now: Callable[[], datetime] = _utcnow) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.now = now
        self.content_retention = content_retention
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS instinct_standing_grants (
          grant_digest TEXT PRIMARY KEY, user_id TEXT NOT NULL,
          sender_email TEXT NOT NULL, created_at TEXT NOT NULL,
          expires_at TEXT NOT NULL, revoked_at TEXT,
          history_address TEXT, history_expires_at TEXT,
          followup_enabled INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS instinct_standing_reports (
          report_id TEXT PRIMARY KEY, grant_digest TEXT NOT NULL,
          deduplication_key TEXT NOT NULL, received_at TEXT NOT NULL,
          report_json TEXT NOT NULL, provenance TEXT NOT NULL,
          retention_expires_at TEXT NOT NULL DEFAULT '',
          UNIQUE(grant_digest, deduplication_key)
        );
        CREATE TABLE IF NOT EXISTS instinct_followup_requests (
          request_id TEXT PRIMARY KEY, grant_digest TEXT NOT NULL,
          question TEXT NOT NULL, created_at TEXT NOT NULL,
          expires_at TEXT NOT NULL, replied_at TEXT, reply_json TEXT,
          retention_expires_at TEXT NOT NULL DEFAULT '',
          UNIQUE(grant_digest, question, created_at)
        );
        CREATE TABLE IF NOT EXISTS instinct_risk_check_schedule (
          grant_digest TEXT NOT NULL, manifest_version TEXT NOT NULL,
          case_id TEXT NOT NULL, trigger_key TEXT NOT NULL,
          request_id TEXT NOT NULL, scheduled_at TEXT NOT NULL,
          PRIMARY KEY(grant_digest, manifest_version, case_id, trigger_key)
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
        if "followup_enabled" not in columns:
            self.connection.execute(
                "ALTER TABLE instinct_standing_grants ADD COLUMN followup_enabled INTEGER NOT NULL DEFAULT 0"
            )
        report_columns = {row[1] for row in self.connection.execute(
            "PRAGMA table_info(instinct_standing_reports)"
        )}
        if "retention_expires_at" not in report_columns:
            self.connection.execute(
                "ALTER TABLE instinct_standing_reports ADD COLUMN retention_expires_at TEXT NOT NULL DEFAULT ''"
            )
        followup_columns = {row[1] for row in self.connection.execute(
            "PRAGMA table_info(instinct_followup_requests)"
        )}
        if "retention_expires_at" not in followup_columns:
            self.connection.execute(
                "ALTER TABLE instinct_followup_requests ADD COLUMN retention_expires_at TEXT NOT NULL DEFAULT ''"
            )
        legacy_cutoff = _iso(self.now() + self.content_retention)
        self.connection.execute(
            "UPDATE instinct_standing_reports SET retention_expires_at=? WHERE retention_expires_at=''",
            (legacy_cutoff,),
        )
        self.connection.execute(
            "UPDATE instinct_followup_requests SET retention_expires_at=? WHERE retention_expires_at=''",
            (legacy_cutoff,),
        )
        self.connection.commit()

    def issue(self, user_id: str, sender_email: str, *,
              history_address: str | None = None,
              history_expires_at: str | None = None,
              followup_enabled: bool = False,
              ttl: timedelta = timedelta(hours=24)) -> dict:
        if not user_id or "@" not in sender_email or ttl <= timedelta(0):
            raise ValueError("standing_grant_invalid")
        token = secrets.token_urlsafe(18)
        digest = hashlib.sha256(token.encode()).hexdigest()
        expires = self.now() + ttl
        self.connection.execute(
            "INSERT INTO instinct_standing_grants "
            "(grant_digest,user_id,sender_email,created_at,expires_at,revoked_at,"
            "history_address,history_expires_at,followup_enabled) VALUES(?,?,?,?,?,NULL,?,?,?)",
            (digest, user_id, sender_email.strip().lower(), _iso(self.now()), _iso(expires),
             history_address, history_expires_at, int(followup_enabled)),
        )
        self.connection.commit()
        return {"token": token, "grant_id": digest, "expires_at": _iso(expires),
                "address": f"{STANDING_PREFIX}{token}@{STANDING_DOMAIN}"}

    def grants_for(self, user_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT grant_digest,sender_email,created_at,expires_at,revoked_at,history_expires_at,followup_enabled "
            "FROM instinct_standing_grants WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        )
        return [{"grant_id": row["grant_digest"], "sender_email": row["sender_email"],
                 "created_at": row["created_at"],
                 "expires_at": row["expires_at"],
                 "history_expires_at": row["history_expires_at"],
                 "followup_enabled": bool(row["followup_enabled"]),
                 "followup_opt_in": self._followup_opted_in(row["grant_digest"]),
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

    def grant_for_binding(self, user_id: str, agent_id: str) -> str:
        row = self.connection.execute(
            "SELECT grant_digest FROM instinct_standing_grants WHERE user_id=? "
            "AND sender_email=? AND history_address IS NOT NULL AND revoked_at IS NULL "
            "AND followup_enabled=1 ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (user_id, agent_id.strip().lower()),
        ).fetchone()
        if row is None:
            raise PermissionError("standing_grant_not_found")
        return row["grant_digest"]

    def revoke_for_binding(self, user_id: str, agent_id: str) -> str:
        """Revoke the active pilot grant bound to an enrolled chat agent."""
        grant_id = self.grant_for_binding(user_id, agent_id)
        self.revoke(user_id, grant_id)
        return grant_id

    def history_address_for_stop(self, user_id: str, grant_id: str,
                                 expected_sender: str) -> str | None:
        row = self.connection.execute(
            "SELECT sender_email,history_address FROM instinct_standing_grants "
            "WHERE grant_digest=? AND user_id=?", (grant_id, user_id)
        ).fetchone()
        if row is None or not hmac.compare_digest(
                row["sender_email"], expected_sender.strip().lower()):
            raise PermissionError("standing_grant_binding_mismatch")
        return row["history_address"]

    def revoke(self, user_id: str, grant_id: str, *,
               expected_sender: str | None = None,
               idempotent: bool = False) -> str:
        row = self.connection.execute(
            "SELECT sender_email,revoked_at FROM instinct_standing_grants "
            "WHERE grant_digest=? AND user_id=?",
            (grant_id, user_id),
        ).fetchone()
        if row is None:
            raise PermissionError("standing_grant_not_found")
        if expected_sender is not None and not hmac.compare_digest(
                row["sender_email"], expected_sender.strip().lower()):
            raise PermissionError("standing_grant_binding_mismatch")
        if row["revoked_at"] is not None:
            if idempotent:
                return "already_revoked"
            raise PermissionError("standing_grant_not_found")
        changed = self.connection.execute(
            "UPDATE instinct_standing_grants SET revoked_at=? "
            "WHERE grant_digest=? AND user_id=? AND revoked_at IS NULL",
            (_iso(self.now()), grant_id, user_id),
        ).rowcount
        if changed != 1:
            raise PermissionError("standing_grant_not_found")
        # STOP ends the grant and keeps what was received; delete_imported() erases it.
        self.connection.commit()
        return "revoked"

    def history_address_for_grant(self, user_id: str, grant_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT history_address FROM instinct_standing_grants WHERE grant_digest=? AND user_id=?",
            (grant_id, user_id),
        ).fetchone()
        if row is None:
            raise PermissionError("standing_grant_not_found")
        return row["history_address"]

    def delete_imported(self, user_id: str, grant_id: str, *, confirm: str) -> dict[str, int | str]:
        """Erase this grant's reports and follow-up answers on an explicit user request."""
        if confirm != DELETE_CONFIRMATION:
            raise ValueError("delete_confirmation_required")
        self.revoke(user_id, grant_id, idempotent=True)
        reports = self.connection.execute(
            "DELETE FROM instinct_standing_reports WHERE grant_digest=?", (grant_id,)
        ).rowcount
        replies = self.connection.execute(
            "UPDATE instinct_followup_requests SET replied_at=NULL,reply_json=NULL "
            "WHERE grant_digest=? AND reply_json IS NOT NULL", (grant_id,)
        ).rowcount
        self.connection.commit()
        return {"state": "deleted", "reports_deleted": reports, "replies_deleted": replies}

    def relay_stop(self, user_id: str, grant_id: str, sender_email: str) -> dict[str, str]:
        """Commit an account- and agent-bound chat stop without exposing secrets."""
        state = self.revoke(user_id, grant_id, expected_sender=sender_email, idempotent=True)
        return {"state": state, "backend_enforcement": "confirmed", "message": STOP_ACK}

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
        } or payload.get("schema_version") not in {SCHEMA_VERSION, LEGACY_SCHEMA_VERSION}:
            raise ValueError("standing_schema_invalid")
        key = payload.get("deduplication_key")
        if not isinstance(key, str) or not 8 <= len(key) <= 128:
            raise ValueError("standing_deduplication_key_invalid")
        # This is an account-scoped task identifier, never a hash of message text.
        # The same task reported through another channel must deduplicate; two tasks
        # with identical text must carry different identifiers and remain distinct.
        if payload.get("trigger") not in {"onboarding_history", "request_completed", "request_failed"}:
            raise ValueError("standing_trigger_invalid")
        event = payload.get("event")
        expected_event_keys = (LEGACY_EVENT_KEYS
                               if payload["schema_version"] == LEGACY_SCHEMA_VERSION
                               else EVENT_KEYS)
        if not isinstance(event, dict) or not (
                set(event) == expected_event_keys or (
                    payload["schema_version"] == SCHEMA_VERSION
                    and set(event) == expected_event_keys | OPTIONAL_EVENT_KEYS)):
            raise ValueError("standing_event_keys_invalid")
        if payload["schema_version"] == LEGACY_SCHEMA_VERSION:
            event = {**event, **LEGACY_EVENT_DEFAULTS}
            payload = {**payload, "schema_version": SCHEMA_VERSION, "event": event}
        if event["synthetic"] is not True or event["evidence_origin"] != "agent_reported":
            raise ValueError("standing_synthetic_provenance_required")
        if event["observed_channel"] not in {"whatsapp", "imessage", "unknown"}:
            raise ValueError("standing_channel_invalid")
        if event["shared_context_status"] not in {"unverified", "unknown"}:
            raise ValueError("standing_shared_context_status_invalid")
        for field in sorted((EVENT_KEYS | OPTIONAL_EVENT_KEYS) - {"synthetic"}):
            if field not in event:
                continue
            v = event[field]
            if not isinstance(v, str):
                raise ValueError(f"standing_event_value_invalid:{field}:not_string")
            if len(v) > 500:
                raise ValueError(f"standing_event_value_invalid:{field}:too_long")
        report_id = "sr_" + secrets.token_hex(12)
        digest = hashlib.sha256(token.encode()).hexdigest()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            previous_row = self.connection.execute(
                "SELECT report_json FROM instinct_standing_reports WHERE grant_digest=? "
                "ORDER BY rowid DESC LIMIT 1", (digest,),
            ).fetchone()
            previous_event = (json.loads(previous_row["report_json"])["event"]
                              if previous_row is not None else None)
            self.connection.execute(
                "INSERT INTO instinct_standing_reports "
                "(report_id,grant_digest,deduplication_key,received_at,report_json,provenance,retention_expires_at) "
                "VALUES(?,?,?,?,?,'agent_reported',?)",
                (report_id, digest, key, _iso(self.now()), json.dumps(payload, sort_keys=True),
                 _iso(self.now() + self.content_retention)),
            )
            triggers = []
            if previous_event is not None and any(
                event[field] != previous_event[field]
                for field in ("approval_source", "approval_status")
            ):
                triggers.append("authority_changed")
            if (previous_event is not None
                    and event["money_at_risk"] != previous_event["money_at_risk"]):
                triggers.append("amount_changed")
            scheduled = []
            if self._followups_allowed(grant):
                for trigger in triggers:
                    scheduled.extend(self._schedule_risk_checks(
                        grant["user_id"], digest, trigger, trigger_key=key))
            self.connection.commit()
            return {"state": "received", "report_id": report_id, "review": "staff_required",
                    "risk_checks_scheduled": len(scheduled)}
        except sqlite3.IntegrityError:
            self.connection.rollback()
            row = self.connection.execute(
                "SELECT report_id,report_json FROM instinct_standing_reports "
                "WHERE grant_digest=? AND deduplication_key=?",
                (digest, key),
            ).fetchone()
            if row is None:
                raise
            previous_payload = json.loads(row["report_json"])
            # Delivery-channel metadata may differ when Instinct retries the same
            # account-scoped task through another chat surface. It is still the
            # same report; substantive changes under the same task ID fail closed.
            previous_event = {**previous_payload["event"], "observed_channel": "deduplicated"}
            current_event = {**payload["event"], "observed_channel": "deduplicated"}
            if ({**previous_payload, "event": previous_event}
                    != {**payload, "event": current_event}):
                raise ValueError("standing_deduplication_conflict")
            return {"state": "duplicate", "report_id": row["report_id"], "review": "staff_required"}
        except Exception:
            self.connection.rollback()
            raise

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

    def followups_for(self, user_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT f.request_id,f.question,f.created_at,f.expires_at,f.replied_at,f.reply_json,g.revoked_at "
            "FROM instinct_followup_requests f JOIN instinct_standing_grants g "
            "ON f.grant_digest=g.grant_digest WHERE g.user_id=? ORDER BY f.created_at DESC",
            (user_id,),
        )
        now = self.now()
        return [{"request_id": row["request_id"], "question": row["question"],
                 "created_at": row["created_at"], "expires_at": row["expires_at"],
                 "state": ("replied" if row["replied_at"] else "revoked" if row["revoked_at"] else "expired" if datetime.fromisoformat(
                     row["expires_at"].replace("Z", "+00:00")) <= now else "pending"),
                 "answer": (json.loads(row["reply_json"])["answer"] if row["reply_json"] else None),
                 "provenance": "agent_reported" if row["reply_json"] else None,
                 "review": "staff_required" if row["reply_json"] else None}
                for row in rows]

    def issue_followup(self, user_id: str, grant_id: str, question: str, *,
                       ttl: timedelta = timedelta(hours=4)) -> dict:
        """Create a short-lived, account-bound read-only follow-up question."""
        question = question.strip()
        if not question or len(question) > 500 or ttl <= timedelta(0):
            raise ValueError("followup_request_invalid")
        grant = self.connection.execute(
            "SELECT * FROM instinct_standing_grants WHERE grant_digest=? AND user_id=?",
            (grant_id, user_id),
        ).fetchone()
        if grant is None:
            raise PermissionError("followup_cross_account")
        if grant["revoked_at"] is not None:
            raise PermissionError("standing_grant_revoked_or_invalid")
        if not self._followups_allowed(grant):
            raise PermissionError("followup_not_authorized")
        grant_expiry = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
        if grant_expiry <= self.now():
            raise PermissionError("standing_grant_expired")
        expires = min(grant_expiry, self.now() + ttl)
        request_id = "fq_" + secrets.token_hex(12)
        self.connection.execute(
            "INSERT INTO instinct_followup_requests "
            "(request_id,grant_digest,question,created_at,expires_at,replied_at,reply_json,retention_expires_at) "
            "VALUES(?,?,?,?,?,NULL,NULL,?)",
            (request_id, grant_id, question, _iso(self.now()), _iso(expires),
             _iso(self.now() + self.content_retention)),
        )
        self.connection.commit()
        return {"request_id": request_id, "question": question,
                "reply_address": f"{FOLLOWUP_PREFIX}{request_id}@{STANDING_DOMAIN}",
                "expires_at": _iso(expires), "state": "pending"}

    def schedule_risk_checks(self, user_id: str, grant_id: str, trigger: str, *,
                             trigger_key: str) -> list[dict]:
        """Schedule the bounded pack once per intake or once per selective event."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            scheduled = self._schedule_risk_checks(
                user_id, grant_id, trigger, trigger_key=trigger_key)
            self.connection.commit()
            return scheduled
        except Exception:
            self.connection.rollback()
            raise

    def _schedule_risk_checks(self, user_id: str, grant_id: str, trigger: str, *,
                              trigger_key: str) -> list[dict]:
        """Schedule checks inside the caller's open transaction."""
        from .instinct_risk_checks import CASES, MANIFEST_VERSION

        if trigger not in {"valid_intake", "authority_changed", "amount_changed"}:
            raise ValueError("risk_check_trigger_invalid")
        if not trigger_key or len(trigger_key) > 128:
            raise ValueError("risk_check_trigger_key_invalid")
        scheduled = []
        for case in CASES:
            if not (case.onboarding if trigger == "valid_intake" else trigger in case.rerun_triggers):
                continue
            exists = self.connection.execute(
                    "SELECT 1 FROM instinct_risk_check_schedule WHERE grant_digest=? "
                    "AND manifest_version=? AND case_id=? AND trigger_key=?",
                    (grant_id, MANIFEST_VERSION, case.case_id, trigger_key),
            ).fetchone()
            if exists is not None:
                continue
            question = f"[{case.case_id}] {case.prompt}"
            if len(question) > 500:
                raise ValueError("followup_request_invalid")
            grant = self.connection.execute(
                    "SELECT * FROM instinct_standing_grants WHERE grant_digest=? AND user_id=?",
                    (grant_id, user_id),
            ).fetchone()
            if grant is None or grant["revoked_at"] is not None or not self._followups_allowed(grant):
                raise PermissionError("standing_grant_revoked_or_invalid")
            grant_expiry = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
            if grant_expiry <= self.now():
                raise PermissionError("standing_grant_expired")
            expires = min(grant_expiry, self.now() + timedelta(hours=4))
            request_id = "fq_" + secrets.token_hex(12)
            followup = {"request_id": request_id, "question": question,
                        "reply_address": f"{FOLLOWUP_PREFIX}{request_id}@{STANDING_DOMAIN}",
                        "expires_at": _iso(expires), "state": "pending"}
            self.connection.execute(
                    "INSERT INTO instinct_followup_requests "
                    "(request_id,grant_digest,question,created_at,expires_at,replied_at,reply_json,retention_expires_at) "
                    "VALUES(?,?,?,?,?,NULL,NULL,?)",
                    (request_id, grant_id, question, _iso(self.now()), _iso(expires),
                     _iso(self.now() + self.content_retention)),
            )
            self.connection.execute(
                    "INSERT INTO instinct_risk_check_schedule VALUES(?,?,?,?,?,?)",
                    (grant_id, MANIFEST_VERSION, case.case_id, trigger_key,
                     followup["request_id"], _iso(self.now())),
            )
            scheduled.append({**followup, "case_id": case.case_id,
                              "manifest_version": MANIFEST_VERSION})
        return scheduled

    def _followup_opted_in(self, grant_digest: str) -> bool:
        """True once any report under this grant recorded followup_opt_in "yes"."""
        for row in self.connection.execute(
                "SELECT report_json FROM instinct_standing_reports WHERE grant_digest=?",
                (grant_digest,)):
            event = json.loads(row["report_json"]).get("event", {})
            if str(event.get("followup_opt_in", "")).strip().lower() == "yes":
                return True
        return False

    def _followups_allowed(self, grant: sqlite3.Row) -> bool:
        return bool(grant["followup_enabled"]) or self._followup_opted_in(grant["grant_digest"])

    def followup_page(self, token: str) -> dict:
        """One user-triggered read; empty unless a report recorded followup_opt_in."""
        grant = self.grant(token)
        reply_schema = {"request_id": "fq_...", "answer": "string",
                        "evidence_origin": "agent_reported", "synthetic": True}
        if not self._followup_opted_in(grant["grant_digest"]):
            return {"followup_opt_in": False, "reply_schema": reply_schema, "requests": []}
        rows = self.connection.execute(
            "SELECT request_id,question,created_at,expires_at,replied_at FROM instinct_followup_requests "
            "WHERE grant_digest=? ORDER BY created_at,request_id", (grant["grant_digest"],),
        )
        now = self.now()
        return {"followup_opt_in": True, "reply_schema": reply_schema,
                "requests": [{**dict(row),
                    "reply_address": f"{FOLLOWUP_PREFIX}{row['request_id']}@{STANDING_DOMAIN}",
                    "state": (
                    "replied" if row["replied_at"] else
                    "expired" if datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")) <= now
                    else "pending")} for row in rows]}

    def reply_followup(self, token: str, request_id: str, payload: dict) -> dict:
        grant = self.grant(token)
        if not self._followups_allowed(grant):
            raise PermissionError("followup_not_authorized")
        row = self.connection.execute(
            "SELECT * FROM instinct_followup_requests WHERE request_id=? AND grant_digest=?",
            (request_id, grant["grant_digest"]),
        ).fetchone()
        if row is None:
            raise PermissionError("followup_cross_account")
        if datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")) <= self.now():
            raise PermissionError("followup_expired")
        if not isinstance(payload, dict) or set(payload) != {"request_id", "answer", "evidence_origin", "synthetic"}:
            raise ValueError("followup_reply_schema_invalid")
        if payload["request_id"] != request_id:
            raise ValueError("followup_request_id_mismatch")
        if payload["evidence_origin"] != "agent_reported" or payload["synthetic"] is not True:
            raise ValueError("followup_synthetic_provenance_required")
        if not isinstance(payload["answer"], str) or not payload["answer"] or len(payload["answer"]) > 500:
            raise ValueError("followup_answer_invalid")
        canonical = json.dumps(payload, sort_keys=True)
        if row["replied_at"] is not None:
            if row["reply_json"] != canonical:
                raise ValueError("followup_reply_conflict")
            return {"state": "duplicate", "request_id": request_id}
        changed = self.connection.execute(
            "UPDATE instinct_followup_requests SET replied_at=?,reply_json=? "
            "WHERE request_id=? AND grant_digest=? AND replied_at IS NULL",
            (_iso(self.now()), canonical, request_id, grant["grant_digest"]),
        ).rowcount
        self.connection.commit()
        if changed != 1:
            stored = self.connection.execute(
                "SELECT reply_json FROM instinct_followup_requests WHERE request_id=? AND grant_digest=?",
                (request_id, grant["grant_digest"]),
            ).fetchone()
            if stored is not None and stored["reply_json"] == canonical:
                return {"state": "duplicate", "request_id": request_id}
            raise ValueError("followup_reply_conflict")
        return {"state": "received", "request_id": request_id,
                "review": "staff_required", "provenance": "agent_reported"}

    def ingest_followup_email(self, raw_message: bytes, *, envelope_recipient: str = "") -> dict:
        if not raw_message or len(raw_message) > MAX_REPORT_BYTES:
            raise ValueError("followup_email_missing_or_too_large")
        message = BytesParser(policy=policy.default).parsebytes(raw_message)
        if any(part.get_filename() or part.get_content_disposition() == "attachment"
               for part in message.walk()):
            raise ValueError("followup_attachments_rejected")
        recipient = parseaddr(str(message.get("To", "")))[1].lower()
        local, separator, domain = recipient.rpartition("@")
        if separator != "@" or domain != STANDING_DOMAIN or not local.startswith(FOLLOWUP_PREFIX):
            raise PermissionError("followup_address_invalid")
        if envelope_recipient.strip().lower() != recipient:
            raise PermissionError("followup_envelope_recipient_mismatch")
        request_id = local[len(FOLLOWUP_PREFIX):]
        row = self.connection.execute(
            "SELECT g.sender_email FROM instinct_followup_requests f JOIN instinct_standing_grants g "
            "ON f.grant_digest=g.grant_digest WHERE f.request_id=?", (request_id,),
        ).fetchone()
        if row is None:
            raise PermissionError("followup_revoked_or_deleted")
        if parseaddr(str(message.get("From", "")))[1].lower() != row["sender_email"]:
            raise PermissionError("followup_sender_mismatch")
        plain = [part for part in message.walk() if part.get_content_type() == "text/plain"]
        if len(plain) != 1:
            raise ValueError("followup_text_plain_required")
        try:
            payload = json.loads(plain[0].get_content().strip())
        except json.JSONDecodeError as exc:
            raise ValueError("followup_json_invalid") from exc
        grant = self.connection.execute(
            "SELECT grant_digest FROM instinct_followup_requests WHERE request_id=?", (request_id,),
        ).fetchone()
        tokenless = self.connection.execute(
            "SELECT * FROM instinct_standing_grants WHERE grant_digest=?", (grant["grant_digest"],)
        ).fetchone()
        # Reuse the same validation/write path without exposing a direct HTTP reply transport.
        if tokenless["revoked_at"] is not None:
            raise PermissionError("standing_grant_revoked_or_invalid")
        if not self._followups_allowed(tokenless):
            raise PermissionError("followup_not_authorized")
        if datetime.fromisoformat(tokenless["expires_at"].replace("Z", "+00:00")) <= self.now():
            raise PermissionError("standing_grant_expired")
        row = self.connection.execute(
            "SELECT * FROM instinct_followup_requests WHERE request_id=?", (request_id,),
        ).fetchone()
        if datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")) <= self.now():
            raise PermissionError("followup_expired")
        if not isinstance(payload, dict) or set(payload) != {"request_id", "answer", "evidence_origin", "synthetic"}:
            raise ValueError("followup_reply_schema_invalid")
        if payload["request_id"] != request_id:
            raise ValueError("followup_request_id_mismatch")
        if payload["evidence_origin"] != "agent_reported" or payload["synthetic"] is not True:
            raise ValueError("followup_synthetic_provenance_required")
        if not isinstance(payload["answer"], str) or not payload["answer"] or len(payload["answer"]) > 500:
            raise ValueError("followup_answer_invalid")
        canonical = json.dumps(payload, sort_keys=True)
        changed = self.connection.execute(
            "UPDATE instinct_followup_requests SET replied_at=?,reply_json=? WHERE request_id=? AND replied_at IS NULL",
            (_iso(self.now()), canonical, request_id),
        ).rowcount
        self.connection.commit()
        if changed == 1:
            return {"state": "received", "request_id": request_id,
                    "review": "staff_required", "provenance": "agent_reported"}
        stored = self.connection.execute(
            "SELECT reply_json FROM instinct_followup_requests WHERE request_id=?", (request_id,),
        ).fetchone()["reply_json"]
        if stored != canonical:
            raise ValueError("followup_reply_conflict")
        return {"state": "duplicate", "request_id": request_id}

    def purge_expired(self) -> dict[str, int]:
        """Delete retained standing content and follow-up questions/answers on schedule."""
        now = _iso(self.now())
        reports = self.connection.execute(
            "DELETE FROM instinct_standing_reports WHERE retention_expires_at<=?", (now,)
        ).rowcount
        followups = self.connection.execute(
            "DELETE FROM instinct_followup_requests WHERE retention_expires_at<=?", (now,)
        ).rowcount
        self.connection.commit()
        return {"reports_deleted": reports, "followups_deleted": followups}

    def close(self) -> None:
        self.connection.close()


def example_report() -> dict:
    """One accepted per-task report; every value except ``synthetic`` is a string."""
    event = {field: "unknown" for field in EVENT_KEYS - {"synthetic"}}
    event.update(
        evidence_origin="agent_reported", synthetic=True,
        observed_channel="unknown", shared_context_status="unverified",
        category="payment", proposed_external_action="Transfer $150 to merchant",
        actual_external_action="unknown", approval_source="user_instruction",
        approval_status="approved", money_at_risk="$150",
        outcome="unknown", gaps="unknown", occurred_at="2024-09-15",
        history_sources_included="unknown", history_sources_unavailable="unknown",
    )
    return {"schema_version": SCHEMA_VERSION, "deduplication_key": "fictional-request-001",
            "trigger": "request_completed", "event": event}


def enrollment_data(*, report_address: str, history_address: str, expires_at: str,
                    stop_endpoint: str = "") -> dict:
    """Data-only enrollment reply: addresses, Stop transport, and field names.

    Both address keys are always present, even when they carry the same value.
    """
    from .history_import import REPORT_VERSION, ROW_KEYS

    history_row = {field: "unknown" for field in ROW_KEYS - {"synthetic"}}
    history_row.update(task_id="fictional-task-001", evidence_origin="agent_reported",
                       synthetic=True)
    return {
        "report_address": report_address,
        "history_address": history_address,
        "expires_at": expires_at,
        "stop_endpoint": stop_endpoint,
        "schema_version": ENROLLMENT_SCHEMA_VERSION,
        "history_fields": {
            "report": ["schema_version", "row_count", "rows", "end_marker"],
            "row": sorted(ROW_KEYS),
            "values": {"schema_version": REPORT_VERSION,
                       "end_marker": "END INSTINCT HISTORY",
                       "evidence_origin": "agent_reported", "synthetic": True},
        },
        "report_fields": {
            "report": ["schema_version", "deduplication_key", "trigger", "event"],
            "event": sorted(EVENT_KEYS),
            "event_optional": sorted(OPTIONAL_EVENT_KEYS),
            "values": {"schema_version": SCHEMA_VERSION,
                       "trigger": ["request_completed", "request_failed"],
                       "evidence_origin": "agent_reported", "synthetic": True,
                       "observed_channel": ["whatsapp", "imessage", "unknown"],
                       "shared_context_status": ["unverified", "unknown"]},
        },
        "example_history": {"schema_version": REPORT_VERSION, "row_count": "1",
                            "rows": [history_row], "end_marker": "END INSTINCT HISTORY"},
        "example_report": example_report(),
        "value_rules": {"type": "text", "max_chars": 500, "if_unknown": "unknown",
                        "not_text": ["synthetic", "rows"], "history_max_rows": 3,
                        "transport": "plain-text email body, one JSON object"},
    }


def render_instruction(token: str, expires_at: str, sender_email: str, public_base: str,
                       history_address: str | None = None,
                       history_expires_at: str | None = None,
                       followup_enabled: bool = False) -> str:
    """Informational reference page; the user's pasted message is the authorization."""
    endpoint = f"{public_base.rstrip('/')}/instinct/report/{token}"
    address = f"{STANDING_PREFIX}{token}@{STANDING_DOMAIN}"
    fields = ", ".join(sorted(EVENT_KEYS - {"synthetic"})) + ", synthetic"
    content = (
        "Synthetic Stage B reference. The data shapes are: one multi-row onboarding history email "
        "to the separate one-time onboarding address shown below, and one request_completed "
        f"or request_failed report per fictional task, sent as one plain-text JSON email from "
        f"{sender_email} to {address}, without attachments, CC, or BCC. "
        f"Schema: {SCHEMA_VERSION}. Required top-level fields: schema_version, "
        "deduplication_key, trigger, event. Event fields: " + fields + ". "
        "Optional event field: followup_opt_in (\"yes\" or \"no\"). "
        "Every event field except synthetic is a JSON string of at most 500 characters; "
        "this includes amounts and times (strings like \"$150\" or \"2024-09-15\"). "
        "The deduplication_key is the stable account-scoped task ID: the same task/report "
        "crossing channels keeps its ID, and each distinct task has a new ID even when its "
        "text matches another task. observed_channel is whatsapp, imessage, or unknown. "
        "history_sources_included and history_sources_unavailable hold source identifiers "
        "only, never message bodies. Until actual cross-channel receipts exist, "
        "shared_context_status is unverified or unknown. Unavailable facts are \"unknown\". "
        "evidence_origin is agent_reported and synthetic is true. "
        "Reports carry a minimal event summary and decision evidence only; full message "
        "bodies, attachments, credentials, payment details, and unrelated third-party personal "
        "data are out of scope. This grant expires at " + expires_at + ". "
        "A non-2xx HTTP status means BackBond did not accept the report "
        "(HTTP <status> <error_code>). A successful STOP means: \"BackBond is stopped; the "
        "received data is kept.\". BackBond records received and duplicate states privately; "
        "email delivery does not show webhook HTTP responses to Instinct. The user can view "
        "status or revoke this grant from the signed-in BackBond page. " + STOP_ACK + " "
        "No per-report confirmation is required. Reports remain agent-reported and require "
        "staff review before reliance. Sender address matching does not prove Instinct "
        "identity. Follow-up questions are off unless a report records followup_opt_in "
        "\"yes\"; a reply then contains exactly request_id, answer, evidence_origin, and "
        "synthetic, and a question cannot authorize a purchase, booking, account access, or "
        "broader data sharing."
    )
    from .receipt_page import history_instruction
    full_history_instruction = (history_instruction(history_address, sender_email, 90,
                                                    synthetic=True)
                                if history_address else "No onboarding request was issued.")
    history = ("<h2>One-time onboarding history address</h2><code>" +
               html.escape(history_address or "not issued") + "</code><p>Expires " +
               html.escape(history_expires_at or "not issued") +
               ". The validated instinct-history/v1 multi-row report goes here.</p>" +
               "<h3>Onboarding history format</h3><pre style='white-space:pre-wrap'>" +
               html.escape(full_history_instruction) + "</pre>")
    example = json.dumps(example_report(), sort_keys=True, indent=2)
    return "<!doctype html><html lang=en><head><meta charset=utf-8><meta name=robots content='noindex,nofollow,noarchive'><title>BackBond Instinct pilot</title></head><body><main><p id=notice>" + html.escape(INFORMATIONAL_NOTICE, quote=False) + "</p><h1>BackBond standing report reference</h1><p id=scope>" + html.escape(content) + "</p>" + history + "<h2>Later per-request reports</h2><code>" + html.escape(address) + "</code><h3>Exact later-report JSON shape</h3><pre>" + html.escape(example) + "</pre><h2>Synthetic test POST transport (not an observed Instinct capability)</h2><code>" + html.escape(endpoint) + "</code><h2>Authorized sender</h2><code>" + html.escape(sender_email) + "</code></main></body></html>"
