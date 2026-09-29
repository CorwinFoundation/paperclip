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

from .correlation import correlation_key


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
    mode: str = "DEMO"


class InboundStore:
    """SQLite store for confirmed sender identities and immutable receipts."""

    def __init__(self, path: str | Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS inbound_users (
              user_id TEXT PRIMARY KEY,
              email TEXT NOT NULL UNIQUE,
              confirmation_digest TEXT NOT NULL,
              confirmed_at TEXT
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
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(inbound_receipts)")}
        if "correlation_key" not in columns:
            self.connection.execute("ALTER TABLE inbound_receipts ADD COLUMN correlation_key TEXT")
            self.connection.commit()

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

    def ingest(self, raw_message: bytes) -> InboundReceipt:
        message = BytesParser(policy=policy.default).parsebytes(raw_message)
        sender = _address(message.get("From", ""))
        row = self.connection.execute(
            "SELECT user_id FROM inbound_users WHERE email=? AND confirmed_at IS NOT NULL", (sender,)
        ).fetchone()
        if row is None:
            raise PermissionError("sender_not_confirmed")
        subject = str(message.get("Subject", "")).strip()
        normalized = _FORWARD_PREFIX.sub("", subject).strip()
        if normalized != CHECK_SUBJECT:
            raise ValueError("subject_not_supported")
        kind = "check" if subject == CHECK_SUBJECT else "forwarded_confirmation"
        digest = hashlib.sha256(raw_message).hexdigest()
        prior = self.connection.execute(
            "SELECT * FROM inbound_receipts WHERE message_sha256=?", (digest,)
        ).fetchone()
        if prior is not None:
            return InboundReceipt(**dict(prior))
        body_text = _body(message)
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
        )
        try:
            self.connection.execute(
                "INSERT INTO inbound_receipts "
                "(receipt_id,user_id,sender_address,subject,kind,received_at,message_sha256,body_text,correlation_key,mode) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                tuple(asdict(receipt).values()),
            )
            self.connection.commit()
        except sqlite3.IntegrityError as exc:
            prior = self.connection.execute(
                "SELECT * FROM inbound_receipts WHERE message_sha256=?", (digest,)
            ).fetchone()
            if prior is None:
                raise
            receipt = InboundReceipt(**dict(prior))
        return receipt

    def receipts_for(self, email: str) -> list[dict[str, str]]:
        rows = self.connection.execute(
            "SELECT r.* FROM inbound_receipts r JOIN inbound_users u USING(user_id) "
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
        return receipts

    def close(self) -> None:
        self.connection.close()


def ingest_json(store: InboundStore, raw_message: bytes) -> str:
    return json.dumps(asdict(store.ingest(raw_message)), sort_keys=True)
