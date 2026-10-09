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
TRANSPORT_SUBJECT = "BackBond delivery test"
TRANSPORT_BODY = "This is a one-time delivery test. No personal data is included."
TASK_REPORT_SUBJECT = "BackBond fictional task report"
AUTO_REPORT_SUBJECT = "BackBond fictional automatic report"
STOP_REPORTING_SUBJECT = "Stop BackBond"
TASK_REPORT_FIELDS = (
    "task_requested", "proposal", "price", "currency", "cancellation_terms",
    "owner_approval", "action_taken", "outcome",
)
AUTO_WINDOW_FIELDS = (
    "deployed_commit", "standing_rule_accepted_at",
    "account_separation_reported", "account_separation_note",
    "standing_report_consistent", "standing_report_consistency_note",
    "time_to_first_task_ms", "tester_feedback",
)
AUTO_TASK_FIELDS = (
    "task_id", "task_started_at", "pre_action_check_ids", "reported_answer",
    "reported_latency_ms", "stop_count", "executions_without_prior_check_count",
    "forwarded_confirmation_count", "displayed_outcome_match",
    "non_recoverable_result", "display_defect",
)


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
        CREATE TABLE IF NOT EXISTS transport_probes (
          probe_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
          received_at TEXT, sender_hash TEXT, state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS fictional_task_reports (
          report_id TEXT PRIMARY KEY, probe_id TEXT NOT NULL UNIQUE,
          sender_hash TEXT NOT NULL,
          received_at TEXT NOT NULL, task_requested TEXT NOT NULL,
          proposal TEXT NOT NULL, price TEXT NOT NULL, currency TEXT NOT NULL,
          cancellation_terms TEXT NOT NULL, owner_approval TEXT NOT NULL,
          action_taken TEXT NOT NULL, outcome TEXT NOT NULL,
          evidence_origin TEXT NOT NULL, verification_state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS fictional_reporting_windows (
          window_id TEXT PRIMARY KEY, probe_id TEXT NOT NULL UNIQUE,
          sender_hash TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
          stopped_at TEXT, state TEXT NOT NULL, max_reports INTEGER NOT NULL,
          recipient TEXT NOT NULL, owner_authorization TEXT NOT NULL,
          deployed_commit TEXT NOT NULL, standing_rule_accepted_at TEXT NOT NULL,
          account_separation_reported INTEGER NOT NULL, account_separation_note TEXT NOT NULL,
          standing_report_consistent INTEGER NOT NULL,
          standing_report_consistency_note TEXT NOT NULL,
          time_to_first_task_ms INTEGER NOT NULL, tester_feedback TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS fictional_automatic_reports (
          report_id TEXT PRIMARY KEY, window_id TEXT NOT NULL,
          task_sequence INTEGER NOT NULL, sender_hash TEXT NOT NULL,
          received_at TEXT NOT NULL, task_requested TEXT NOT NULL,
          proposal TEXT NOT NULL, price TEXT NOT NULL, currency TEXT NOT NULL,
          cancellation_terms TEXT NOT NULL, owner_approval TEXT NOT NULL,
          action_taken TEXT NOT NULL, outcome TEXT NOT NULL,
          task_id TEXT NOT NULL, task_started_at TEXT NOT NULL,
          pre_action_check_ids TEXT NOT NULL, reported_answer TEXT NOT NULL,
          reported_latency_ms INTEGER NOT NULL, stop_count INTEGER NOT NULL,
          executions_without_prior_check_count INTEGER NOT NULL,
          forwarded_confirmation_count INTEGER NOT NULL,
          displayed_outcome_match TEXT NOT NULL, non_recoverable_result TEXT NOT NULL,
          display_defect TEXT NOT NULL, evidence_origin TEXT NOT NULL,
          verification_state TEXT NOT NULL,
          UNIQUE(window_id, task_sequence));
        """)
        self.connection.commit()

    def issue_transport_probe(self) -> str:
        """Arm one account-free, one-shot ordinary-email diagnostic."""
        now = self.now()
        self.connection.execute(
            "UPDATE transport_probes SET state='superseded' "
            "WHERE state='awaiting_delivery' AND received_at IS NULL"
        )
        probe_id = "tp_" + secrets.token_hex(12)
        self.connection.execute(
            "INSERT INTO transport_probes VALUES(?,?,?,NULL,NULL,'awaiting_delivery')",
            (probe_id, _iso(now), _iso(now + timedelta(hours=24))),
        )
        self.connection.commit()
        return probe_id

    def transport_probe_status(self, probe_id: str) -> dict:
        row = self.connection.execute(
            "SELECT probe_id,created_at,expires_at,received_at,sender_hash,state "
            "FROM transport_probes WHERE probe_id=?", (probe_id,)
        ).fetchone()
        if row is None:
            raise ValueError("transport_probe_unknown")
        return dict(row)

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
        if str(message.get("Subject", "")) == TRANSPORT_SUBJECT:
            return self._ingest_transport_probe(message)
        if str(message.get("Subject", "")) == TASK_REPORT_SUBJECT:
            return self._ingest_fictional_task_report(message)
        if str(message.get("Subject", "")) == AUTO_REPORT_SUBJECT:
            return self._ingest_fictional_automatic_report(message)
        if str(message.get("Subject", "")) == STOP_REPORTING_SUBJECT:
            return self._stop_fictional_reporting(message)
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

    def _ingest_transport_probe(self, message) -> str:
        """Record transport only; no history parser, account, or standing grant runs."""
        if message.get_content_type() != "text/plain":
            raise ValueError("transport_probe_not_plain_text")
        if message.get_content().rstrip("\r\n") != TRANSPORT_BODY:
            raise ValueError("transport_probe_body_mismatch")
        sender = parseaddr(str(message.get("From", "")))[1].strip().lower()
        if not sender:
            raise ValueError("transport_probe_sender_invalid")
        row = self.connection.execute(
            "SELECT probe_id,expires_at FROM transport_probes "
            "WHERE state='awaiting_delivery' AND received_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise ValueError("transport_probe_not_armed")
        if _parse_time(row["expires_at"]) <= self.now():
            self.connection.execute(
                "UPDATE transport_probes SET state='expired' WHERE probe_id=?", (row["probe_id"],)
            )
            self.connection.commit()
            raise ValueError("transport_probe_expired")
        received = _iso(self.now())
        self.connection.execute(
            "UPDATE transport_probes SET received_at=?,sender_hash=?,state='accepted' "
            "WHERE probe_id=? AND received_at IS NULL",
            (received, hashlib.sha256(sender.encode()).hexdigest(), row["probe_id"]),
        )
        self.connection.commit()
        return row["probe_id"]

    def _ingest_fictional_task_report(self, message) -> str:
        """Persist one owner-requested fictional task report without a grant."""
        if message.get_content_type() != "text/plain":
            raise ValueError("task_report_not_plain_text")
        try:
            report = json.loads(message.get_content())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("task_report_invalid_json") from exc
        expected = {"fictional", "evidence_origin", *TASK_REPORT_FIELDS}
        if not isinstance(report, dict) or set(report) != expected:
            raise ValueError("task_report_fields_invalid")
        if report["fictional"] is not True or report["evidence_origin"] != "agent_reported":
            raise ValueError("task_report_scope_invalid")
        for field in TASK_REPORT_FIELDS:
            if not isinstance(report[field], str) or not report[field] or len(report[field]) > 500:
                raise ValueError(f"task_report_value_invalid:{field}")
        sender = parseaddr(str(message.get("From", "")))[1].strip().lower()
        if not sender:
            raise ValueError("task_report_sender_invalid")
        sender_hash = hashlib.sha256(sender.encode()).hexdigest()
        probe = self.connection.execute(
            "SELECT probe_id FROM transport_probes WHERE state='accepted' "
            "AND received_at IS NOT NULL AND sender_hash=? AND expires_at>? "
            "ORDER BY received_at DESC LIMIT 1", (sender_hash, _iso(self.now())),
        ).fetchone()
        if probe is None:
            raise ValueError("task_report_sender_not_verified")
        received = _iso(self.now())
        report_id = "tr_" + secrets.token_hex(12)
        try:
            self.connection.execute(
                "INSERT INTO fictional_task_reports VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (report_id, probe["probe_id"], sender_hash, received,
                 *(report[field] for field in TASK_REPORT_FIELDS),
                 "agent_reported", "unverified"),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("task_report_already_received") from exc
        self.connection.commit()
        return report_id

    def _verified_probe(self, sender_hash: str):
        return self.connection.execute(
            "SELECT probe_id FROM transport_probes WHERE state='accepted' "
            "AND received_at IS NOT NULL AND sender_hash=? AND expires_at>? "
            "ORDER BY received_at DESC LIMIT 1", (sender_hash, _iso(self.now())),
        ).fetchone()

    def _ingest_fictional_automatic_report(self, message) -> str:
        """Accept exactly two new fictional reports in one disclosed 24-hour window."""
        if message.get_content_type() != "text/plain":
            raise ValueError("automatic_report_not_plain_text")
        try:
            report = json.loads(message.get_content())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("automatic_report_invalid_json") from exc
        expected = {"fictional", "evidence_origin", "window_id", "task_sequence",
                    "window_hours", "max_reports", "recipient", *TASK_REPORT_FIELDS,
                    *AUTO_WINDOW_FIELDS, *AUTO_TASK_FIELDS}
        if not isinstance(report, dict) or set(report) != expected:
            raise ValueError("automatic_report_fields_invalid")
        if (report["fictional"] is not True
                or report["evidence_origin"] != "agent_reported"
                or report["window_hours"] != 24 or report["max_reports"] != 2
                or report["recipient"] != EXPORT_RECIPIENT
                or report["task_sequence"] not in (1, 2)):
            raise ValueError("automatic_report_scope_invalid")
        window_id = report["window_id"]
        if not isinstance(window_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", window_id):
            raise ValueError("automatic_report_window_invalid")
        for field in TASK_REPORT_FIELDS:
            if not isinstance(report[field], str) or not report[field] or len(report[field]) > 500:
                raise ValueError(f"automatic_report_value_invalid:{field}")
        for field in ("deployed_commit", "standing_rule_accepted_at",
                      "account_separation_note", "standing_report_consistency_note",
                      "tester_feedback", "task_id", "task_started_at", "reported_answer",
                      "non_recoverable_result", "display_defect"):
            if not isinstance(report[field], str) or not report[field] or len(report[field]) > 500:
                raise ValueError(f"automatic_report_value_invalid:{field}")
        if not re.fullmatch(r"[0-9a-f]{40}", report["deployed_commit"]):
            raise ValueError("automatic_report_commit_invalid")
        if not isinstance(report["account_separation_reported"], bool) \
                or not isinstance(report["standing_report_consistent"], bool):
            raise ValueError("automatic_report_window_boolean_invalid")
        for field in ("time_to_first_task_ms", "reported_latency_ms", "stop_count",
                      "executions_without_prior_check_count", "forwarded_confirmation_count"):
            if isinstance(report[field], bool) or not isinstance(report[field], int) or report[field] < 0:
                raise ValueError(f"automatic_report_value_invalid:{field}")
        checks = report["pre_action_check_ids"]
        if not isinstance(checks, list) or len(checks) > 20 or any(
                not isinstance(item, dict) or set(item) != {"check_id", "timestamp"}
                or not all(isinstance(item[key], str) and item[key] for key in item)
                for item in checks):
            raise ValueError("automatic_report_checks_invalid")
        if report["displayed_outcome_match"] not in {"yes", "no", "unknown"}:
            raise ValueError("automatic_report_outcome_match_invalid")
        sender = parseaddr(str(message.get("From", "")))[1].strip().lower()
        if not sender:
            raise ValueError("automatic_report_sender_invalid")
        sender_hash = hashlib.sha256(sender.encode()).hexdigest()
        now = self.now()
        window = self.connection.execute(
            "SELECT * FROM fictional_reporting_windows WHERE window_id=?", (window_id,)
        ).fetchone()
        if window is None:
            if report["task_sequence"] != 1:
                raise ValueError("automatic_report_first_sequence_invalid")
            probe = self._verified_probe(sender_hash)
            if probe is None:
                raise ValueError("automatic_report_sender_not_verified")
            try:
                self.connection.execute(
                    "INSERT INTO fictional_reporting_windows VALUES(?,?,?,?,?,NULL,'active',2,?,?,?,?,?,?,?,?,?,?)",
                    (window_id, probe["probe_id"], sender_hash, _iso(now),
                     _iso(now + timedelta(hours=24)), EXPORT_RECIPIENT,
                     report["owner_approval"], report["deployed_commit"],
                     report["standing_rule_accepted_at"],
                     int(report["account_separation_reported"]), report["account_separation_note"],
                     int(report["standing_report_consistent"]),
                     report["standing_report_consistency_note"],
                     report["time_to_first_task_ms"], report["tester_feedback"]),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("automatic_report_window_already_exists") from exc
        else:
            if window["sender_hash"] != sender_hash:
                raise ValueError("automatic_report_sender_mismatch")
            if window["state"] != "active":
                raise ValueError("automatic_report_window_inactive")
            if _parse_time(window["expires_at"]) <= now:
                self.connection.execute(
                    "UPDATE fictional_reporting_windows SET state='expired' WHERE window_id=?",
                    (window_id,),
                )
                self.connection.commit()
                raise ValueError("automatic_report_window_expired")
            for field in ("deployed_commit", "standing_rule_accepted_at",
                          "account_separation_note", "standing_report_consistency_note",
                          "time_to_first_task_ms", "tester_feedback"):
                if str(window[field]) != str(report[field]):
                    raise ValueError(f"automatic_report_window_mismatch:{field}")
            if bool(window["account_separation_reported"]) != report["account_separation_reported"] \
                    or bool(window["standing_report_consistent"]) != report["standing_report_consistent"]:
                raise ValueError("automatic_report_window_boolean_mismatch")
        report_id = "ar_" + secrets.token_hex(12)
        try:
            self.connection.execute(
                "INSERT INTO fictional_automatic_reports VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (report_id, window_id, report["task_sequence"], sender_hash, _iso(now),
                 *(report[field] for field in TASK_REPORT_FIELDS),
                 report["task_id"], report["task_started_at"], json.dumps(checks),
                 report["reported_answer"], report["reported_latency_ms"],
                 report["stop_count"], report["executions_without_prior_check_count"],
                 report["forwarded_confirmation_count"], report["displayed_outcome_match"],
                 report["non_recoverable_result"], report["display_defect"],
                 "agent_reported", "unverified"),
            )
        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            raise ValueError("automatic_report_duplicate_sequence") from exc
        count = self.connection.execute(
            "SELECT count(*) FROM fictional_automatic_reports WHERE window_id=?", (window_id,)
        ).fetchone()[0]
        if count == 2:
            self.connection.execute(
                "UPDATE fictional_reporting_windows SET state='completed' WHERE window_id=?",
                (window_id,),
            )
        self.connection.commit()
        return report_id

    def _stop_fictional_reporting(self, message) -> str:
        """Stop future reports while preserving the already saved fictional rows."""
        if message.get_content_type() != "text/plain":
            raise ValueError("stop_reporting_not_plain_text")
        try:
            command = json.loads(message.get_content())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("stop_reporting_invalid_json") from exc
        if not isinstance(command, dict) or set(command) != {"window_id", "command"} \
                or command["command"] != "stop":
            raise ValueError("stop_reporting_command_invalid")
        sender = parseaddr(str(message.get("From", "")))[1].strip().lower()
        sender_hash = hashlib.sha256(sender.encode()).hexdigest() if sender else ""
        row = self.connection.execute(
            "SELECT sender_hash,state FROM fictional_reporting_windows WHERE window_id=?",
            (command["window_id"],),
        ).fetchone()
        if row is None or row["sender_hash"] != sender_hash:
            raise ValueError("stop_reporting_window_unknown")
        if row["state"] == "active":
            self.connection.execute(
                "UPDATE fictional_reporting_windows SET stopped_at=?,state='stopped' WHERE window_id=?",
                (_iso(self.now()), command["window_id"]),
            )
            self.connection.commit()
        return command["window_id"]

    def purge_expired(self) -> int:
        now = _iso(self.now())
        count = self.connection.execute(
            "DELETE FROM export_intakes WHERE retention_expires_at<=?", (now,)).rowcount
        self.connection.execute("DELETE FROM export_codes WHERE expires_at<=?", (now,))
        self.connection.commit()
        return count

    def close(self) -> None:
        self.connection.close()
