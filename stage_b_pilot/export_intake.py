"""One-shot, account-free synthetic export intake for Stage B compatibility tests."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path
from typing import Callable

from .history_import import REPORT_VERSION, _iso, _parse_time, _seal, row_refs

EXPORT_RECIPIENT = "history+export@inbound.backbond.ai"
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
SUBJECT = re.compile(r"^BackBond export ([23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6})$")
MAX_BODY_BYTES = 16 * 1024


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExportIntakeStore:
    def __init__(self, path: str | Path, *, encryption_key: bytes,
                 now: Callable[[], datetime] = _utcnow) -> None:
        if len(encryption_key) < 32:
            raise ValueError("history_secret_too_short")
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.encryption_key, self.now = encryption_key, now
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS export_codes (
          code_digest TEXT PRIMARY KEY, created_at TEXT NOT NULL,
          expires_at TEXT NOT NULL, used_at TEXT);
        CREATE TABLE IF NOT EXISTS export_intakes (
          intake_id TEXT PRIMARY KEY, code_digest TEXT NOT NULL UNIQUE,
          sender_hash TEXT NOT NULL, received_at TEXT NOT NULL, state TEXT NOT NULL,
          row_count INTEGER NOT NULL, row_refs TEXT NOT NULL, rows_encrypted BLOB NOT NULL,
          retention_expires_at TEXT NOT NULL);
        """)
        self.connection.commit()

    def issue_code(self) -> str:
        now = self.now()
        while True:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
            digest = hashlib.sha256(code.encode()).hexdigest()
            try:
                self.connection.execute("INSERT INTO export_codes VALUES(?,?,?,NULL)",
                                        (digest, _iso(now), _iso(now + timedelta(hours=1))))
                self.connection.commit()
                return code
            except sqlite3.IntegrityError:
                continue

    def ingest(self, raw_message: bytes, *, envelope_recipient: str) -> str:
        if envelope_recipient.lower() != EXPORT_RECIPIENT:
            raise ValueError("export_recipient_invalid")
        if len(raw_message) > MAX_BODY_BYTES:
            raise ValueError("export_body_too_large")
        message = BytesParser(policy=policy.default).parsebytes(raw_message)
        if message.get("Cc") or message.get("Bcc") or message.is_multipart():
            raise ValueError("export_recipients_or_attachments_invalid")
        match = SUBJECT.fullmatch(str(message.get("Subject", "")))
        code = match.group(1) if match else ""
        digest = hashlib.sha256(code.encode()).hexdigest()
        record = self.connection.execute("SELECT * FROM export_codes WHERE code_digest=?",
                                         (digest,)).fetchone()
        if record is None:
            raise ValueError("export_code_unknown")
        if record["used_at"]:
            raise ValueError("export_code_used")
        if _parse_time(record["expires_at"]) <= self.now():
            raise ValueError("export_code_expired")
        if message.get_content_type() != "text/plain":
            raise ValueError("export_body_invalid")
        try:
            report = json.loads(message.get_content())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("export_body_invalid") from exc
        if not isinstance(report, dict) or report.get("schema_version") != REPORT_VERSION \
                or report.get("end_marker") != "END INSTINCT HISTORY":
            raise ValueError("export_body_invalid")
        rows = report.get("rows")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 3 \
                or report.get("row_count") != len(rows):
            raise ValueError("export_rows_out_of_range")
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or row.get("synthetic") is not True:
                raise ValueError(f"export_row_not_synthetic:{index}")
            if row.get("evidence_origin") != "agent_reported":
                raise ValueError("export_value_invalid:evidence_origin")
            for field, value in row.items():
                if field == "synthetic":
                    continue
                if not isinstance(value, str) or len(value) > 500:
                    raise ValueError(f"export_value_invalid:{field}")
        sender = parseaddr(str(message.get("From", "")))[1].strip().lower()
        if not sender:
            raise ValueError("export_sender_invalid")
        received = _iso(self.now())
        intake_id = "ex_" + secrets.token_hex(12)
        self.connection.execute("""INSERT INTO export_intakes
          VALUES(?,?,?,?,?,?,?,?,?)""", (intake_id, digest,
          hashlib.sha256(sender.encode()).hexdigest(), received, "accepted", len(rows),
          row_refs(rows), _seal(self.encryption_key, json.dumps(rows).encode()),
          _iso(self.now() + timedelta(hours=24))))
        self.connection.execute("UPDATE export_codes SET used_at=? WHERE code_digest=?",
                                (received, digest))
        self.connection.commit()
        return intake_id

    def purge_expired(self) -> int:
        now = _iso(self.now())
        count = self.connection.execute(
            "DELETE FROM export_intakes WHERE retention_expires_at<=?", (now,)).rowcount
        self.connection.execute("DELETE FROM export_codes WHERE expires_at<=?", (now,))
        self.connection.commit()
        return count

    def close(self) -> None:
        self.connection.close()
