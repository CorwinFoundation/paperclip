"""Inbound-only email receipt ingestion for the Stage B DEMO pilot.

The receiver consumes an RFC 5322 message delivered by an inbound email
provider.  It never connects to, authenticates to, or reads a user's mailbox.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path
from typing import Callable

from .correlation import correlation_key
from .mail_filter import (
    assess_dkim,
    check_matches_confirmation,
    confirmation_facts,
    gmail_forwarding_code,
    original_message,
)


CHECK_SUBJECT = "BackBond check"
import re
_FORWARD_PREFIX = re.compile(r"^(?:(?:fwd?|fw):\s*)+", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _address(value: str) -> str:
    address = parseaddr(value)[1].strip().lower()
    if not address or "@" not in address:
        raise ValueError("sender_address_invalid")
    return address


def _body(message) -> str:
    part = message.get_body(preferencelist=("plain",))
    if part is None:
        return ""
    return part.get_content().strip()


@dataclass(frozen=True)
class InboundReceipt:
    receipt_id: str
    user_id: str
    sender_address: str
    subject: str
    kind: str
    received_at: str
    message_sha256: str
    body_text: str
    correlation_key: str | None = None
    original_sha256: str | None = None
    dkim_signing_domain: str | None = None
    dkim_result: str = "missing"
    link_state: str = "not_applicable"
    linked_check_receipt_id: str | None = None
    ownership: str | None = None
    forwarding_code: str | None = None
    mode: str = "DEMO"


class InboundStore:
    """SQLite store for confirmed sender identities and immutable receipts."""

    def __init__(
        self, path: str | Path, *, dkim_verifier: Callable[[bytes], bool] | None = None
    ) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.dkim_verifier = dkim_verifier or (lambda _: False)
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS inbound_users (
              user_id TEXT PRIMARY KEY,
              email TEXT NOT NULL UNIQUE,
              confirmation_digest TEXT NOT NULL,
              confirmed_at TEXT,
              agent_recipient TEXT
            );
            CREATE TABLE IF NOT EXISTS inbound_receipts (
              receipt_id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES inbound_users(user_id),
              sender_address TEXT NOT NULL,
              subject TEXT NOT NULL,
              kind TEXT NOT NULL CHECK(kind IN ('check', 'forwarded_confirmation')),
              received_at TEXT NOT NULL,
              message_sha256 TEXT NOT NULL UNIQUE,
              body_text TEXT NOT NULL,
              correlation_key TEXT,
              mode TEXT NOT NULL CHECK(mode = 'DEMO')
            );
            CREATE TABLE IF NOT EXISTS receipt_answers (
              check_id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES inbound_users(user_id),
              answer TEXT NOT NULL CHECK(answer IN ('OK','STOP')),
              answered_at TEXT NOT NULL,
              channel TEXT NOT NULL CHECK(channel = 'receipt_page'),
              authorship TEXT NOT NULL CHECK(authorship = 'authenticated'),
              delivery_id TEXT,
              delivery_attempts INTEGER NOT NULL DEFAULT 0,
              last_delivery_error TEXT
            );
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(inbound_receipts)")}
        user_columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(inbound_users)")
        }
        if "agent_recipient" not in user_columns:
            self.connection.execute(
                "ALTER TABLE inbound_users ADD COLUMN agent_recipient TEXT"
            )
        if "correlation_key" not in columns:
            self.connection.execute("ALTER TABLE inbound_receipts ADD COLUMN correlation_key TEXT")
        migrations = {
            "raw_original": "BLOB",
            "original_sha256": "TEXT",
            "dkim_signing_domain": "TEXT",
            "dkim_result": "TEXT NOT NULL DEFAULT 'missing'",
            "link_state": "TEXT NOT NULL DEFAULT 'not_applicable'",
            "linked_check_receipt_id": "TEXT",
            "ownership": "TEXT",
            "forwarding_code": "TEXT",
        }
        for name, definition in migrations.items():
            if name not in columns:
                self.connection.execute(
                    f"ALTER TABLE inbound_receipts ADD COLUMN {name} {definition}"
                )
        answer_columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(receipt_answers)")
        }
        if "delivery_attempts" not in answer_columns:
            self.connection.execute(
                "ALTER TABLE receipt_answers ADD COLUMN delivery_attempts INTEGER NOT NULL DEFAULT 0"
            )
        if "last_delivery_error" not in answer_columns:
            self.connection.execute(
                "ALTER TABLE receipt_answers ADD COLUMN last_delivery_error TEXT"
            )
        self.connection.commit()

    @staticmethod
    def _receipt(row: sqlite3.Row) -> InboundReceipt:
        names = InboundReceipt.__dataclass_fields__
        return InboundReceipt(**{name: row[name] for name in names})

    def begin_signup(self, email: str) -> tuple[str, str]:
        """Create or replace the one-step confirmation challenge.

        The returned token belongs in the confirmation email. Only its digest
        is persisted.
        """
        address = _address(email)
        user_id = "usr_" + hashlib.sha256(address.encode()).hexdigest()[:20]
        token = secrets.token_urlsafe(24)
        digest = hashlib.sha256(token.encode()).hexdigest()
        self.connection.execute(
            "INSERT INTO inbound_users(user_id,email,confirmation_digest,confirmed_at) "
            "VALUES(?,?,?,NULL) ON CONFLICT(email) DO UPDATE SET "
            "confirmation_digest=excluded.confirmation_digest, confirmed_at=NULL",
            (user_id, address, digest),
        )
        self.connection.commit()
        return user_id, token

    def confirm_signup(self, email: str, token: str) -> str:
        address = _address(email)
        row = self.connection.execute(
            "SELECT user_id,confirmation_digest FROM inbound_users WHERE email=?", (address,)
        ).fetchone()
        if row is None or not secrets.compare_digest(
            row["confirmation_digest"], hashlib.sha256(token.encode()).hexdigest()
        ):
            raise PermissionError("signup_confirmation_invalid")
        self.connection.execute(
            "UPDATE inbound_users SET confirmed_at=? WHERE user_id=?", (_now(), row["user_id"])
        )
        self.connection.commit()
        return row["user_id"]

    def authenticate(self, email: str, token: str) -> str:
        """Authenticate a confirmed enrollee for private receipt retrieval."""
        address = _address(email)
        row = self.connection.execute(
            "SELECT user_id,confirmation_digest,confirmed_at FROM inbound_users WHERE email=?",
            (address,),
        ).fetchone()
        if row is None or row["confirmed_at"] is None or not secrets.compare_digest(
            row["confirmation_digest"], hashlib.sha256(token.encode()).hexdigest()
        ):
            raise PermissionError("enrollment_authentication_invalid")
        return row["user_id"]

    def configure_agent_recipient(self, email: str, token: str, recipient: str) -> str:
        """Bind an enrolled user to one operator-authenticated agent address."""
        user_id = self.authenticate(email, token)
        agent_recipient = _address(recipient)
        self.connection.execute(
            "UPDATE inbound_users SET agent_recipient=? WHERE user_id=?",
            (agent_recipient, user_id),
        )
        self.connection.commit()
        return agent_recipient

    def ingest(self, raw_message: bytes) -> InboundReceipt:
        message = BytesParser(policy=policy.default).parsebytes(raw_message)
        sender = _address(message.get("From", ""))
        row = self.connection.execute(
            "SELECT user_id FROM inbound_users WHERE email=? AND confirmed_at IS NOT NULL", (sender,)
        ).fetchone()
        if row is None:
            raise PermissionError("sender_not_confirmed")
        subject = str(message.get("Subject", "")).strip()
        is_forward = bool(_FORWARD_PREFIX.match(subject))
        if subject == CHECK_SUBJECT:
            kind = "check"
        elif is_forward:
            kind = "forwarded_confirmation"
        else:
            raise ValueError("subject_not_supported")
        digest = hashlib.sha256(raw_message).hexdigest()
        prior = self.connection.execute(
            "SELECT * FROM inbound_receipts WHERE message_sha256=?", (digest,)
        ).fetchone()
        if prior is not None:
            return self._receipt(prior)
        raw_original = original_message(raw_message) if kind == "forwarded_confirmation" else raw_message
        original = BytesParser(policy=policy.default).parsebytes(raw_original)
        body_text = _body(original)
        dkim = assess_dkim(raw_original, self.dkim_verifier) if kind == "forwarded_confirmation" else None
        key = correlation_key(body_text)
        if kind == "check" and key is not None:
            existing = self.connection.execute(
                "SELECT correlation_key,body_text FROM inbound_receipts "
                "WHERE user_id=? AND kind='check'", (row["user_id"],),
            ).fetchall()
            for prior_check in existing:
                prior_key = prior_check["correlation_key"]
                if prior_key is None:
                    prior_key = correlation_key(prior_check["body_text"])
                if prior_key == key:
                    raise ValueError("check_id_reused")
        receipt = InboundReceipt(
            receipt_id="in_" + digest[:24], user_id=row["user_id"], sender_address=sender,
            subject=subject, kind=kind, received_at=_now(), message_sha256=digest,
            body_text=body_text, correlation_key=key,
            original_sha256=hashlib.sha256(raw_original).hexdigest(),
            dkim_signing_domain=dkim.signing_domain if dkim else None,
            dkim_result=dkim.result if dkim else "missing",
            link_state="unlinked" if kind == "forwarded_confirmation" else "not_applicable",
            linked_check_receipt_id=None,
            ownership=None,
            forwarding_code=gmail_forwarding_code(str(original.get("Subject", "")), body_text),
        )
        try:
            self.connection.execute(
                "INSERT INTO inbound_receipts "
                "(receipt_id,user_id,sender_address,subject,kind,received_at,message_sha256,body_text,"
                "correlation_key,original_sha256,dkim_signing_domain,dkim_result,link_state,"
                "linked_check_receipt_id,ownership,forwarding_code,mode,raw_original) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*asdict(receipt).values(), raw_original),
            )
            self.connection.commit()
        except sqlite3.IntegrityError as exc:
            prior = self.connection.execute(
                "SELECT * FROM inbound_receipts WHERE message_sha256=?", (digest,)
            ).fetchone()
            if prior is None:
                raise
            receipt = self._receipt(prior)
        return receipt

    def receipts_for(self, email: str) -> list[dict[str, str]]:
        rows = self.connection.execute(
            "SELECT r.receipt_id,r.user_id,r.sender_address,r.subject,r.kind,r.received_at,"
            "r.message_sha256,r.body_text,r.correlation_key,r.original_sha256,"
            "r.dkim_signing_domain,r.dkim_result,r.link_state,r.linked_check_receipt_id,"
            "r.ownership,r.forwarding_code,r.mode "
            "FROM inbound_receipts r JOIN inbound_users u USING(user_id) "
            "WHERE u.email=? ORDER BY received_at,receipt_id", (_address(email),)
        ).fetchall()
        receipts = [dict(row) for row in rows]
        for receipt in receipts:
            if receipt.get("correlation_key") is None:
                try:
                    receipt["correlation_key"] = correlation_key(receipt["body_text"])
                except ValueError:
                    receipt["correlation_key"] = None
                    receipt["correlation_error"] = "ambiguous"
            answer = self.connection.execute(
                "SELECT answer,answered_at,channel,authorship,delivery_id,delivery_attempts,last_delivery_error FROM receipt_answers WHERE check_id=? AND user_id=?",
                (receipt["receipt_id"], receipt["user_id"]),
            ).fetchone()
            if answer is not None:
                receipt["authenticated_answer"] = dict(answer)
        return receipts

    def link_confirmation(self, receipt_id: str) -> str:
        confirmation = self.connection.execute(
            "SELECT receipt_id,user_id,body_text FROM inbound_receipts "
            "WHERE receipt_id=? AND kind='forwarded_confirmation'",
            (receipt_id,),
        ).fetchone()
        if confirmation is None:
            raise ValueError("confirmation_not_found")
        try:
            facts = confirmation_facts(confirmation["body_text"])
        except ValueError:
            return "unlinked"
        checks = self.connection.execute(
            "SELECT receipt_id,body_text FROM inbound_receipts WHERE user_id=? AND kind='check'",
            (confirmation["user_id"],),
        ).fetchall()
        matches = [row for row in checks if check_matches_confirmation(row["body_text"], facts)]
        if len(matches) != 1:
            return "unlinked"
        self.connection.execute(
            "UPDATE inbound_receipts SET link_state='linked',linked_check_receipt_id=? "
            "WHERE receipt_id=?",
            (matches[0]["receipt_id"], receipt_id),
        )
        self.connection.commit()
        return "linked"

    def classify_unlinked(self, email: str, receipt_id: str, ownership: str) -> str:
        if ownership not in {"own_booking", "agent_booking"}:
            raise ValueError("ownership_invalid")
        row = self.connection.execute(
            "SELECT r.link_state FROM inbound_receipts r JOIN inbound_users u USING(user_id) "
            "WHERE u.email=? AND r.receipt_id=? AND r.kind='forwarded_confirmation'",
            (_address(email), receipt_id),
        ).fetchone()
        if row is None:
            raise ValueError("confirmation_not_found")
        if row["link_state"] != "unlinked":
            raise ValueError("confirmation_already_linked")
        state = "skipped_check" if ownership == "agent_booking" else "own_booking"
        self.connection.execute(
            "UPDATE inbound_receipts SET ownership=?,link_state=? WHERE receipt_id=?",
            (ownership, state, receipt_id),
        )
        self.connection.commit()
        return state
    def record_authenticated_answer(self, email: str, token: str, check_id: str, answer: str) -> dict[str, str | None]:
        """Persist a signed-in page decision before any outbound notification."""
        user_id = self.authenticate(email, token)
        normalized = answer.strip().upper()
        if normalized not in {"OK", "STOP"}:
            raise ValueError("answer_not_supported")
        check = self.connection.execute(
            "SELECT receipt_id FROM inbound_receipts WHERE receipt_id=? AND user_id=? AND kind='check'",
            (check_id, user_id),
        ).fetchone()
        if check is None:
            raise ValueError("check_not_found")
        existing = self.connection.execute(
            "SELECT * FROM receipt_answers WHERE check_id=?", (check_id,)
        ).fetchone()
        if existing is not None:
            if existing["user_id"] != user_id or existing["answer"] != normalized:
                raise ValueError("answer_already_recorded")
            return dict(existing)
        self.connection.execute(
            "INSERT INTO receipt_answers(check_id,user_id,answer,answered_at,channel,authorship,delivery_id) VALUES(?,?,?,?,?,?,NULL)",
            (check_id, user_id, normalized, _now(), "receipt_page", "authenticated"),
        )
        self.connection.commit()
        return dict(self.connection.execute("SELECT * FROM receipt_answers WHERE check_id=?", (check_id,)).fetchone())

    def record_delivery_id(self, check_id: str, delivery_id: str) -> None:
        if not delivery_id or delivery_id == "demo-no-delivery":
            raise ValueError("delivery_id_missing")
        self.connection.execute(
            "UPDATE receipt_answers SET delivery_id=?,delivery_attempts=delivery_attempts+1,last_delivery_error=NULL "
            "WHERE check_id=? AND delivery_id IS NULL", (delivery_id, check_id)
        )
        self.connection.commit()

    def record_delivery_failure(self, check_id: str, error: str) -> None:
        self.connection.execute(
            "UPDATE receipt_answers SET delivery_attempts=delivery_attempts+1,last_delivery_error=? "
            "WHERE check_id=? AND delivery_id IS NULL", (error[:120], check_id)
        )
        self.connection.commit()

    def answer_relay_payload(self, check_id: str) -> dict[str, str]:
        """Load the immutable check and registered agent destination for relay."""
        row = self.connection.execute(
            "SELECT r.receipt_id,r.correlation_key,r.body_text,u.agent_recipient,a.answer "
            "FROM inbound_receipts r JOIN inbound_users u USING(user_id) "
            "JOIN receipt_answers a ON a.check_id=r.receipt_id "
            "WHERE r.receipt_id=? AND r.kind='check'",
            (check_id,),
        ).fetchone()
        if row is None:
            raise ValueError("answered_check_not_found")
        if not row["agent_recipient"]:
            raise ValueError("agent_recipient_not_configured")
        reference = row["correlation_key"] or correlation_key(row["body_text"])
        if not reference:
            raise ValueError("check_reference_missing")
        return {
            "agent_recipient": row["agent_recipient"],
            "answer": row["answer"],
            "reference": reference,
            "body_text": row["body_text"],
        }
    def close(self) -> None:
        self.connection.close()


def ingest_json(store: InboundStore, raw_message: bytes) -> str:
    return json.dumps(asdict(store.ingest(raw_message)), sort_keys=True)
