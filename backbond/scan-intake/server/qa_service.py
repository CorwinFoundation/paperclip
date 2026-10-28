"""Private partner QA intake core for BEAAA-38254.

Framework-neutral domain layer. HTTP wiring supplies the scanner and mail clients;
credential material is accepted only by ``start_scan`` and is never persisted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import re
import os
import time
from functools import wraps
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
from report_extensions import ensure_schema, extend
from quota_policy import quota_counted_sql


LIMIT_MESSAGE = ("You've used your {limit} free scans. For a full scan of your "
                 "organization's agent, contact info@backbond.ai.")


def safe_provenance(value):
    """Keep only bounded run identities; never credentials, prompts or endpoints."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ('scanner_version', 'scanner_release_commit', 'scanner_image_digest',
                'scanner_identity_observed_at', 'scanner_client_version',
                'corpus_version', 'corpus_snapshot_hash'):
        item = value.get(key)
        if isinstance(item, str) and 0 < len(item) <= 200 and not any(ord(c) < 32 for c in item):
            result[key] = item
    seed = value.get('seed')
    if isinstance(seed, int) and not isinstance(seed, bool) and 0 <= seed < 2**63:
        result['seed'] = seed
    models = value.get('model_versions')
    if isinstance(models, dict):
        kept = {key: item for key, item in models.items()
                if key in {'target_requested', 'target_resolved', 'judge'}
                and isinstance(item, str) and 0 < len(item) <= 200
                and not any(ord(c) < 32 for c in item)}
        if kept:
            result['model_versions'] = kept
    return result


class IntakeError(ValueError):
    pass


def synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


@dataclass(frozen=True)
class Code:
    code: str
    partner: str
    partner_label: str
    kind: str
    max_emails: int
    scans_per_email: int
    expires_at: str
    active: bool = True


class IntakeStore:
    def __init__(self, path: str):
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS codes(
          code TEXT PRIMARY KEY, partner TEXT NOT NULL, partner_label TEXT NOT NULL,
          kind TEXT NOT NULL CHECK(kind IN ('standard','instructor')),
          max_emails INTEGER NOT NULL, scans_per_email INTEGER NOT NULL,
          expires_at TEXT NOT NULL, active INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS scans(
          id TEXT PRIMARY KEY, code TEXT NOT NULL REFERENCES codes(code),
          partner TEXT NOT NULL, email TEXT NOT NULL, agent_label TEXT NOT NULL,
          endpoint_host TEXT NOT NULL, transport TEXT NOT NULL,
          pack_version TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL,
          report_token TEXT UNIQUE, tests_json TEXT);
        DROP INDEX IF EXISTS one_running_per_partner_email;
        CREATE UNIQUE INDEX IF NOT EXISTS one_running_per_partner_email
          ON scans(partner,email) WHERE status IN ('reserved','queued','starting','running','dispatch_unknown');
        DROP INDEX IF EXISTS one_running_per_email;
        CREATE UNIQUE INDEX IF NOT EXISTS one_running_per_email
          ON scans(email) WHERE status IN ('reserved','queued','starting','running','dispatch_unknown');
        """)
        if 'finished_at' not in {row[1] for row in self.db.execute('PRAGMA table_info(scans)')}:
            self.db.execute('ALTER TABLE scans ADD COLUMN finished_at TEXT')
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(scans)')}
        if 'scanner_id' not in columns:
            self.db.execute('ALTER TABLE scans ADD COLUMN scanner_id TEXT')
        self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS scanner_id_unique ON scans(scanner_id)')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS callbacks(scanner_id TEXT PRIMARY KEY, tests_json TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS mail_outbox(scan_id TEXT PRIMARY KEY REFERENCES scans(id),
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt REAL NOT NULL DEFAULT 0);
        ''')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS acceptances(scan_id TEXT PRIMARY KEY REFERENCES scans(id),
            terms_version TEXT NOT NULL, accepted_at TEXT NOT NULL,
            authorization_confirmed INTEGER NOT NULL CHECK(authorization_confirmed=1),
            improvement_consent INTEGER NOT NULL DEFAULT 0 CHECK(improvement_consent IN (0,1)));
          CREATE TABLE IF NOT EXISTS improvement_samples(
            scan_id TEXT PRIMARY KEY, pack_version TEXT NOT NULL,
            tests_json TEXT NOT NULL, copied_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS report_verifications(
            id TEXT PRIMARY KEY, report_token TEXT NOT NULL, code_hash TEXT NOT NULL,
            expires_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            used_at REAL, issued_at REAL NOT NULL, request_key TEXT NOT NULL DEFAULT '');
          CREATE TABLE IF NOT EXISTS report_sessions(
            session_hash TEXT PRIMARY KEY, report_token TEXT NOT NULL,
            recipient_hash TEXT NOT NULL, created_at REAL NOT NULL,
            last_seen_at REAL NOT NULL, expires_at REAL NOT NULL, revoked_at REAL);
        ''')
        verification_cols = {row[1] for row in self.db.execute('PRAGMA table_info(report_verifications)')}
        if 'request_key' not in verification_cols:
            self.db.execute("ALTER TABLE report_verifications ADD COLUMN request_key TEXT NOT NULL DEFAULT ''")
        if 'status_token_hash' not in {row[1] for row in self.db.execute('PRAGMA table_info(scans)')}:
            self.db.execute('ALTER TABLE scans ADD COLUMN status_token_hash TEXT')
        # BEAAA-38895: the engine's terminal outcome rides next to status='finished'.
        scan_cols = {row[1] for row in self.db.execute('PRAGMA table_info(scans)')}
        if 'outcome' not in scan_cols:
            self.db.execute('ALTER TABLE scans ADD COLUMN outcome TEXT')
        if 'incomplete_reason' not in scan_cols:
            self.db.execute('ALTER TABLE scans ADD COLUMN incomplete_reason TEXT')
        if 'provenance_json' not in scan_cols:
            self.db.execute("ALTER TABLE scans ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'")
        if 'source_intake_id' not in scan_cols:
            self.db.execute('ALTER TABLE scans ADD COLUMN source_intake_id TEXT')
        self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS one_retained_voice_admission '
                        "ON scans(source_intake_id,pack_version) WHERE source_intake_id IS NOT NULL")
        self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS status_token_unique ON scans(status_token_hash)')
        ensure_schema(self.db)

    @synchronized
    def put_code(self, value: Code) -> None:
        self.db.execute("""INSERT INTO codes VALUES(?,?,?,?,?,?,?,?)
          ON CONFLICT(code) DO UPDATE SET partner=excluded.partner,
          partner_label=excluded.partner_label,kind=excluded.kind,
          max_emails=excluded.max_emails,scans_per_email=excluded.scans_per_email,
          expires_at=excluded.expires_at,active=excluded.active""",
                        (value.code, value.partner, value.partner_label, value.kind,
                         value.max_emails, value.scans_per_email, value.expires_at,
                         int(value.active)))

    @synchronized
    def check_code(self, code: str, now: str | None = None) -> dict:
        now = now or datetime.now(timezone.utc).date().isoformat()
        row = self.db.execute("SELECT * FROM codes WHERE code=?", (code,)).fetchone()
        valid = bool(row and row["active"] and row["expires_at"] >= now)
        return {"valid": valid, "partner_label": row["partner_label"] if valid else None}

    @synchronized
    def reserve(self, *, code: str, email: str, agent_label: str,
                endpoint_url: str, transport: str, now: str | None = None,
                terms_version: str | None = None, improvement_consent: bool = False,
                pack_version: str | None = None,
                source_intake_id: str | None = None,
                provenance: dict | None = None) -> dict:
        created = now or datetime.now(timezone.utc).isoformat()
        email = email.strip().lower()
        endpoint = urlsplit(endpoint_url)
        host = endpoint.hostname
        if not host or endpoint.scheme != "https" or endpoint.username or endpoint.password or endpoint.fragment:
            raise IntakeError("endpoint_url must be an https URL")
        scan_id = secrets.token_hex(16)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM codes WHERE code=?", (code,)).fetchone()
            if not row or not row["active"] or row["expires_at"] < created[:10]:
                raise IntakeError("invalid or expired code")
            used = self.db.execute(
                "SELECT count(*) FROM scans WHERE partner=? AND email=? AND " + quota_counted_sql(),
                (row["partner"], email)).fetchone()[0]
            if used >= row["scans_per_email"]:
                raise IntakeError(LIMIT_MESSAGE.format(limit=row["scans_per_email"]))
            emails = self.db.execute(
                "SELECT count(DISTINCT email) FROM scans WHERE code=? AND " + quota_counted_sql(),
                (code,)).fetchone()[0]
            known = self.db.execute("SELECT 1 FROM scans WHERE code=? AND email=? AND " + quota_counted_sql() + " LIMIT 1", (code, email)).fetchone()
            if not known and emails >= row["max_emails"]:
                raise IntakeError("This partner code has reached its email limit.")
            if self.db.execute("SELECT 1 FROM scans WHERE email=? AND status IN ('reserved','queued','starting','running','dispatch_unknown') LIMIT 1", (email,)).fetchone():
                raise IntakeError("A scan is already running for this email.")
            self.db.execute("""INSERT INTO scans
              (id,code,partner,email,agent_label,endpoint_host,transport,pack_version,created_at,status,source_intake_id)
              VALUES(?,?,?,?,?,?,?,?,?, 'reserved',?)""",
              (scan_id, code, row["partner"], email, agent_label, host, transport,
               pack_version or "starter-100@4", created, source_intake_id))
            self.record_provenance(scan_id, provenance or {})
            # Only a digest of the status token is stored; the caller holds the token.
            status_token = secrets.token_urlsafe(24)
            self.db.execute('UPDATE scans SET status_token_hash=? WHERE id=?',
                            (hashlib.sha256(status_token.encode()).hexdigest(), scan_id))
            if terms_version is not None:
                # Same transaction as the reservation: no scan exists without its acceptance row.
                self.db.execute('INSERT INTO acceptances VALUES(?,?,?,1,?)',
                                (scan_id, terms_version, created, int(bool(improvement_consent))))
            self.db.execute("COMMIT")
            return {"scan_id": scan_id, "partner": row["partner"], "status_token": status_token}
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    @synchronized
    def connection_failed(self, scan_id: str) -> None:
        self.db.execute("UPDATE scans SET status='connection_failed' WHERE id=?", (scan_id,))

    @synchronized
    def running(self, scan_id: str) -> None:
        self.db.execute("UPDATE scans SET status='running' WHERE id=? AND status IN "
                        "('reserved','queued','starting','dispatch_unknown')", (scan_id,))

    @synchronized
    def starting(self, scan_id: str) -> None:
        self.db.execute("UPDATE scans SET status='starting' WHERE id=? AND status IN ('reserved','queued')", (scan_id,))

    @synchronized
    def dispatch_unknown(self, scan_id: str) -> None:
        # Counted against quota: the scanner may be sending requests to the agent.
        self.db.execute("UPDATE scans SET status='dispatch_unknown' WHERE id=? AND status IN "
                        "('reserved','starting')", (scan_id,))

    @synchronized
    def queued(self, scan_id: str) -> None:
        self.db.execute("UPDATE scans SET status='queued' WHERE id=?", (scan_id,))

    @synchronized
    def scan(self, scan_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()

    @synchronized
    def bind_scanner(self, scan_id: str, scanner_id: str) -> list[dict] | None:
        self.db.execute('UPDATE scans SET scanner_id=? WHERE id=?', (scanner_id, scan_id))
        callback = self.db.execute('SELECT tests_json FROM callbacks WHERE scanner_id=?', (scanner_id,)).fetchone()
        if callback:
            self.db.execute('DELETE FROM callbacks WHERE scanner_id=?', (scanner_id,))
            return json.loads(callback[0])
        return None

    @synchronized
    def scan_for_scanner(self, scanner_id: str) -> sqlite3.Row | None:
        return self.db.execute('SELECT * FROM scans WHERE scanner_id=?', (scanner_id,)).fetchone()

    @synchronized
    def early_callback(self, scanner_id: str, tests: list[dict]) -> None:
        # Only translated, signed result fields are retained, never the raw body.
        if self.db.execute('SELECT count(*) FROM callbacks').fetchone()[0] >= 20:
            raise IntakeError('callback correlation queue full')
        self.db.execute('INSERT OR IGNORE INTO callbacks VALUES(?,?)', (scanner_id, json.dumps(tests)))

    @synchronized
    def launch_failed(self, scan_id: str) -> None:
        # Only provably unsent/rejected dispatches use this state. Unknown dispatch stays counted.
        self.db.execute("UPDATE scans SET status='launch_failed' WHERE id=? AND status IN "
                        "('reserved','starting','dispatch_unknown')", (scan_id,))

    @synchronized
    def record_provenance(self, scan_id: str, value: dict) -> None:
        row = self.scan(scan_id)
        if not row or row['status'] == 'finished':
            return
        merged = safe_provenance(json.loads(row['provenance_json'] or '{}'))
        merged.update(safe_provenance(value))
        self.db.execute('UPDATE scans SET provenance_json=? WHERE id=?',
                        (json.dumps(merged), scan_id))

    @synchronized
    def finish(self, scan_id: str, tests: list[dict] | dict, *, now: str | None = None) -> str:
        # BEAAA-38895: accepts translate_callback()'s {tests, meta}; a bare list is legacy.
        meta = tests.get("meta") or {} if isinstance(tests, dict) else {}
        tests = tests.get("tests") or [] if isinstance(tests, dict) else tests
        row = self.scan(scan_id)
        if not row:
            raise IntakeError("scan not found")
        if row['status'] == 'finished' and row['report_token']:
            return row['report_token']
        self.record_provenance(scan_id, meta.get('provenance') or {})
        token = secrets.token_hex(16)  # 128 random bits; matches the viewer
        safe = [{k: v for k, v in test.items() if k in
                 {"id", "family", "prompt", "reply", "verdict", "error", "reason"}}
                for test in tests]
        self.db.execute("UPDATE scans SET status='finished',report_token=?,tests_json=?,finished_at=?,"
                        "outcome=?,incomplete_reason=?,pack_version=COALESCE(?,pack_version) WHERE id=?",
                        (token, json.dumps(safe), now or datetime.now(timezone.utc).isoformat(),
                         meta.get("outcome") or "complete", meta.get("incomplete_reason"),
                         meta.get("pack_version"), scan_id))
        consent = self.db.execute(
            'SELECT improvement_consent FROM acceptances WHERE scan_id=?', (scan_id,)).fetchone()
        if consent and consent[0]:
            # Deliberately standalone: retention deletes the intake record but not this
            # consented, de-identified test/result sample.
            self.db.execute('''INSERT OR REPLACE INTO improvement_samples
              (scan_id,pack_version,tests_json,copied_at) VALUES(?,?,?,?)''',
              (scan_id, row['pack_version'], json.dumps(safe),
               now or datetime.now(timezone.utc).isoformat()))
        # BEAAA-42596: no customer mail_outbox row, and intake sends no report mail at all;
        # the Operations workflow writes the final report and sends it to info@backbond.ai.
        return token

    @synchronized
    def refresh_report_failure(self, scan_id: str, scanner_id: str, payload: dict) -> bool:
        """Refresh only the report outcome of an identical, completed execution."""
        row = self.scan(scan_id)
        if not row or row['scanner_id'] != scanner_id or payload.get('scan_id') != scanner_id:
            raise IntakeError("scan/scanner binding mismatch")
        if not report_failure(row):
            return False
        translated = translate_callback(payload)
        meta = translated['meta']
        if meta['outcome'] != 'complete' or meta['pack_version'] != row['pack_version']:
            return False
        # Report preparation must never rewrite execution evidence or accepted scope.
        if translated['tests'] != json.loads(row['tests_json'] or '[]'):
            raise IntakeError("recovered execution evidence mismatch")
        self.db.execute("UPDATE scans SET outcome='complete',incomplete_reason=NULL WHERE id=? "
                        "AND status='finished' AND incomplete_reason LIKE 'report_preparation_error:%'",
                        (scan_id,))
        return True

    @synchronized
    def reconcile_failed(self, scan_id: str, scanner_id: str, reason: str,
                         *, now: str | None = None) -> str:
        """Idempotently apply an independently verified zero-row terminal failure."""
        row = self.scan(scan_id)
        if not row or row["scanner_id"] != scanner_id:
            raise IntakeError("scan/scanner binding mismatch")
        if row["status"] == "finished":
            if row["outcome"] == "failed" and row["incomplete_reason"] == reason:
                return row["report_token"]
            raise IntakeError("scan already has a different terminal outcome")
        if row["status"] not in {"running", "dispatch_unknown"}:
            raise IntakeError("scan is not reconcilable")
        return self.finish(scan_id, {"tests": [], "meta": {
            "outcome": "failed", "incomplete_reason": reason,
            "pack_version": None}}, now=now)

    @synchronized
    def mail_jobs(self, now: float) -> list[dict]:
        return [dict(row) for row in self.db.execute('''SELECT s.id,s.email,s.agent_label,s.report_token,m.attempts
          FROM mail_outbox m JOIN scans s ON s.id=m.scan_id
          WHERE m.status='pending' AND m.next_attempt<=? LIMIT 10''', (now,))]

    @synchronized
    def mail_result(self, scan_id: str, success: bool, now: float) -> None:
        if success:
            self.db.execute("UPDATE mail_outbox SET status='sent' WHERE scan_id=?", (scan_id,))
        else:
            attempts = self.db.execute('SELECT attempts FROM mail_outbox WHERE scan_id=?', (scan_id,)).fetchone()[0] + 1
            self.db.execute('UPDATE mail_outbox SET attempts=?,next_attempt=? WHERE scan_id=?',
                            (attempts, now + min(3600, 30 * 2 ** min(attempts, 7)), scan_id))

    @synchronized
    def report(self, token: str) -> dict | None:
        if not re.fullmatch(r'[a-f0-9]{32}', token):
            return None
        row = self.db.execute("SELECT * FROM scans WHERE report_token=? AND status='finished'", (token,)).fetchone()
        if not row:
            return None
        created = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= created + timedelta(days=180):
            return None
        value = {"agent_label": row["agent_label"], "endpoint_host": row["endpoint_host"],
                "pack_version": row["pack_version"], "created_at": row["created_at"],
                "partner": row["partner"], "tests": json.loads(row["tests_json"]),
                **report_outcome(row["outcome"], row["incomplete_reason"], json.loads(row["tests_json"])),
                **safe_provenance(json.loads(row["provenance_json"] or "{}"))}
        return extend(self.db, row, value)

    @synchronized
    def scan_for_report(self, token: str) -> str | None:
        if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}', token):
            return None
        row = self.db.execute("SELECT id FROM scans WHERE report_token=? AND status='finished'", (token,)).fetchone()
        return row['id'] if row else None

    @synchronized
    def legacy_released(self, scan_id: str) -> bool:
        """Pre-BAC report whose link the legacy outbox already mailed to its customer.

        No new row reaches status 'sent' (finish() no longer enqueues, deliver_pending is
        disabled), so this admits only historically released reports."""
        row = self.db.execute("SELECT status FROM mail_outbox WHERE scan_id=?", (scan_id,)).fetchone()
        return bool(row and row['status'] == 'sent')

    @synchronized
    def report_recipient_hint(self, token: str) -> str | None:
        row = self.db.execute("SELECT email FROM scans WHERE report_token=? AND status='finished'", (token,)).fetchone()
        if not row or not row['email'] or '@' not in row['email']:
            return None
        local, domain = row['email'].split('@', 1)
        return (local[:1] + '*' * max(2, len(local) - 1) + '@' + domain)

    @synchronized
    def issue_report_code(self, token: str, *, request_key: str | None = None,
                          now: float | None = None) -> tuple[str, str] | None:
        now = time.time() if now is None else now
        row = self.db.execute("SELECT email FROM scans WHERE report_token=? AND status='finished'", (token,)).fetchone()
        if not row or not row['email']:
            return None
        # A forwarded report URL must not let one browser exhaust the recipient's
        # code budget. Keep a small per-client budget and a larger report-wide
        # safety ceiling for distributed abuse.
        request_key = request_key or token
        recent_for_client = self.db.execute(
            "SELECT count(*) FROM report_verifications WHERE report_token=? AND request_key=? AND issued_at>?",
            (token, request_key, now - 3600)).fetchone()[0]
        recent_total = self.db.execute(
            "SELECT count(*) FROM report_verifications WHERE report_token=? AND issued_at>?",
            (token, now - 3600)).fetchone()[0]
        if recent_for_client >= 5 or recent_total >= 50:
            return None
        code = f'{secrets.randbelow(1_000_000):06d}'
        nonce = secrets.token_hex(16)
        digest = hashlib.sha256((nonce + ':' + code).encode()).hexdigest()
        self.db.execute("""INSERT INTO report_verifications
                         (id, report_token, code_hash, expires_at, attempts, used_at, issued_at, request_key)
                         VALUES(?,?,?,?,0,NULL,?,?)""",
                        (nonce, token, digest, now + 600, now, request_key))
        return row['email'], nonce + '.' + code

    @synchronized
    def verify_report_code(self, token: str, presented: str, *, request_key: str | None = None,
                           now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        try:
            nonce, code = presented.split('.', 1)
        except ValueError:
            return None
        row = self.db.execute("SELECT * FROM report_verifications WHERE id=? AND report_token=?", (nonce, token)).fetchone()
        if not row or row['used_at'] is not None or row['expires_at'] <= now or row['attempts'] >= 5:
            return None
        if not hmac.compare_digest(row['code_hash'], hashlib.sha256((nonce + ':' + code).encode()).hexdigest()):
            # Every failed guess consumes this challenge's budget, including
            # failures after the report-wide ceiling has been reached.  The
            # ceiling must not turn a fresh challenge into an unlimited oracle.
            self.db.execute("UPDATE report_verifications SET attempts=attempts+1 WHERE id=?", (nonce,))
            total_failures = self.db.execute(
                "SELECT COALESCE(sum(attempts),0) FROM report_verifications WHERE report_token=? AND issued_at>?",
                (token, now - 3600)).fetchone()[0]
            if total_failures >= 100:
                return None
            # Count only failed guesses. A successful verification is not a
            # failed-attempt budget hit for a later challenge. Check the
            # report-wide ceiling only on the failure path so distributed
            # abuse cannot lock out a recipient holding a valid fresh code.
            return None
        recipient = self.db.execute("SELECT email FROM scans WHERE report_token=?", (token,)).fetchone()
        if not recipient:
            return None
        self.db.execute("UPDATE report_verifications SET used_at=? WHERE id=?", (now, nonce))
        raw = secrets.token_urlsafe(32)
        self.db.execute("INSERT INTO report_sessions VALUES(?,?,?,?,?,?,NULL)",
                        (hashlib.sha256(raw.encode()).hexdigest(), token,
                         hashlib.sha256(recipient['email'].encode()).hexdigest(), now, now, now + 1800))
        return raw

    @synchronized
    def authorize_report(self, token: str, session: str | None, *, now: float | None = None) -> bool:
        if not session:
            return False
        now = time.time() if now is None else now
        digest = hashlib.sha256(session.encode()).hexdigest()
        row = self.db.execute("SELECT * FROM report_sessions WHERE session_hash=? AND report_token=?",
                              (digest, token)).fetchone()
        if not row or row['revoked_at'] is not None or row['expires_at'] <= now or row['last_seen_at'] + 600 <= now:
            return False
        self.db.execute("UPDATE report_sessions SET last_seen_at=? WHERE session_hash=?", (now, digest))
        return True

    @synchronized
    def revoke_report_session(self, session: str | None, *, now: float | None = None) -> None:
        if session:
            self.db.execute("UPDATE report_sessions SET revoked_at=? WHERE session_hash=?",
                            (time.time() if now is None else now, hashlib.sha256(session.encode()).hexdigest()))

    @synchronized
    def interrupted(self, scan_id: str) -> None:
        # A queued payload lost to shutdown/restart never reached the scanner: not counted.
        self.db.execute("UPDATE scans SET status='interrupted' WHERE id=? AND status='queued'", (scan_id,))

    @synchronized
    def cancel_unsent(self, scan_id: str) -> bool:
        cursor = self.db.execute("UPDATE scans SET status='cancelled' WHERE id=? AND status IN ('reserved','queued')",
                                 (scan_id,))
        return cursor.rowcount == 1

    @synchronized
    def acceptance(self, scan_id: str) -> dict | None:
        row = self.db.execute('SELECT * FROM acceptances WHERE scan_id=?', (scan_id,)).fetchone()
        return dict(row) if row else None

    @synchronized
    def scans_since(self, partner: str, since: str) -> int:
        return self.db.execute("SELECT count(*) FROM scans WHERE partner=? AND created_at>=? AND "
                               + quota_counted_sql(refund_terminal_failures=False), (partner, since)).fetchone()[0]

    @synchronized
    def status(self, token: str) -> dict | None:
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,64}', token):
            return None
        row = self.db.execute("""SELECT s.id, s.status, s.outcome, s.incomplete_reason, s.created_at, s.finished_at, m.status AS mail
          FROM scans s LEFT JOIN mail_outbox m ON m.scan_id=s.id WHERE s.status_token_hash=?""",
          (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row:
            return None
        status = row["status"]
        if status == "finished" and row["outcome"] == "incomplete":
            status = "incomplete"
        elif status == "finished" and row["outcome"] == "failed":
            status = "error"
        return {"scan_id": row["id"], "status": status, "created_at": row["created_at"],
                "finished_at": row["finished_at"], "report_emailed": row["mail"] == "sent",
                "retry_allowed": row["outcome"] == "failed" and not str(row["incomplete_reason"] or "").startswith("report_preparation_error:")}

    @synchronized
    def delete_by_status_token(self, token: str) -> bool:
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,64}', token):
            return False
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = self.db.execute("SELECT id,status FROM scans WHERE status_token_hash=?", (digest,)).fetchone()
        if not row or row['status'] in {'reserved','queued','starting','running','dispatch_unknown'}:
            return False
        self.db.execute("DELETE FROM mail_outbox WHERE scan_id=?", (row['id'],))
        self.db.execute("DELETE FROM acceptances WHERE scan_id=?", (row['id'],))
        self.db.execute("DELETE FROM improvement_samples WHERE scan_id=?", (row['id'],))
        self.db.execute("DELETE FROM scans WHERE id=?", (row['id'],))
        return True

    @synchronized
    def improvement_list(self) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            'SELECT scan_id,pack_version,copied_at FROM improvement_samples ORDER BY copied_at')]

    @synchronized
    def improvement_get(self, scan_id: str) -> dict | None:
        row = self.db.execute('SELECT * FROM improvement_samples WHERE scan_id=?', (scan_id,)).fetchone()
        if not row:
            return None
        return {"scan_id": row['scan_id'], "pack_version": row['pack_version'],
                "tests": json.loads(row['tests_json']), "copied_at": row['copied_at']}

    @synchronized
    def improvement_withdraw(self, scan_id: str) -> bool:
        return self.db.execute('DELETE FROM improvement_samples WHERE scan_id=?', (scan_id,)).rowcount == 1

    @synchronized
    def purge_before(self, cutoff: str) -> int:
        ids = [row[0] for row in self.db.execute("SELECT id FROM scans WHERE created_at < ? AND status NOT IN "
               "('reserved','queued','starting','running','dispatch_unknown')", (cutoff,))]
        for scan_id in ids:
            self.db.execute("DELETE FROM mail_outbox WHERE scan_id=?", (scan_id,))
            self.db.execute("DELETE FROM acceptances WHERE scan_id=?", (scan_id,))
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='findings_report_outbox'").fetchone():
                self.db.execute('DELETE FROM findings_report_outbox WHERE scan_id=?', (scan_id,))
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='findings_capture_errors'").fetchone():
                self.db.execute('DELETE FROM findings_capture_errors WHERE scan_id=?', (scan_id,))
        cursor = self.db.execute("DELETE FROM scans WHERE created_at < ? AND status NOT IN "
                                 "('reserved','queued','starting','running','dispatch_unknown')", (cutoff,))
        self.db.execute("DELETE FROM callbacks")
        return cursor.rowcount


def verify_webhook(raw_body: bytes, supplied: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied.removeprefix("sha256="))


def scanner_payload(data: dict, *, callback_url: str, callback_secret_ref: str) -> dict:
    """Build the ephemeral WP1 request; callers must discard ``data`` after use."""
    auth = data.get("auth", {"type": "none"})
    agent_id = hashlib.sha256((data['email'].strip().lower() + ':' + data['agent_label']).encode()).hexdigest()
    endpoint_auth = {'type': 'custom_header' if auth['type'] == 'header' else auth['type']}
    if auth['type'] == 'header':
        endpoint_auth['header_name'] = auth['header']
    endpoint = {'url': data['endpoint_url'], 'transport': 'rest' if data['transport'] == 'vapi' else data['transport'],
                'auth': endpoint_auth}
    for key in ('model', 'system_prompt', 'response_path'):
        if data.get(key) is not None:
            endpoint[key] = data[key]
    if data.get('request_template'):
        endpoint['request_template'] = json.loads(data['request_template'])
    return {"tenant_id": f"partner-{data['partner']}",
            "agent_id": data.get('vapi_assistant_id') or agent_id,
            "agent_endpoint": endpoint,
            "credential_ref": 'qa-' + agent_id,
            # The deployed vault requires a nonempty value even for auth:none.
            # This public marker is ignored by the no-auth HTTP adapter; it is
            # not an endpoint credential and must never become a header.
            "credential_secret": 'qa-no-auth' if auth['type'] == 'none' else auth.get('value') or '',
            "scanner_version": os.environ.get('QA_SCANNER_VERSION', 'partner-qa-v1'),
            "scan_mode": "live_probe", "scan_tier": "smoke",
            "pack_id": data.get("pack_id") or "starter-100@4", "include_result_rows": True,
            **({"expected_total": data["expected_total"]} if "expected_total" in data else {}),
            "seed": int(os.environ.get('QA_SCANNER_CORPUS_SEED', '42')),
            "webhook": {"url": callback_url, "secret_ref": callback_secret_ref}}


def translate_tests(payload: dict) -> list[dict]:
    """Normalize scanner webhook rows to the public report contract."""
    source = payload.get("result_rows") or []
    translated = []
    for row in source:
        error = row.get("error")
        raw_verdict = str(row.get("verdict") or row.get("status") or "").lower()
        if raw_verdict == "could_not_run":
            # BEAAA-38895: its own verdict, never folded into fail.
            verdict = "could_not_run"
        elif error or raw_verdict in {"connection_error", "timeout", "transport_error"}:
            verdict = "connection_error"
        elif raw_verdict in {"pass", "passed", "ok", "safe"}:
            verdict = "pass"
        else:
            verdict = "fail"
        item = {"id": str(row.get("test_id") or row.get("id") or row.get("probe_id") or "unknown"),
                "family": str(row.get("family") or row.get("probe_family") or "other"),
                "prompt": str(row.get("prompt") or row.get("request") or ""),
                "reply": str(row.get("reply") or row.get("response") or ""),
                "verdict": verdict}
        if error:
            item["error"] = str(error)
        if row.get("reason"):
            item["reason"] = str(row["reason"])[:500]
        translated.append(item)
    return translated


def translate_callback(payload: dict) -> dict:
    """BEAAA-38895: tests plus the engine's terminal outcome and actual pack version."""
    status = str(payload.get("status") or "").lower()
    incomplete = bool(payload.get("incomplete")) or status in {"incomplete", "partial", "failed"}
    source = payload.get("result_rows") or []
    test_ids = [str(row.get("test_id") or row.get("id") or row.get("probe_id") or "") for row in source]
    if any(not test_id for test_id in test_ids) or len(test_ids) != len(set(test_ids)):
        raise IntakeError("callback result rows require unique test IDs")

    # The rows describe the pack that actually executed.  Prefer that frozen
    # identity over an envelope default (the voice callback exposed why this
    # matters), while still rejecting internally contradictory callbacks.
    row_packs = {str(row.get("pack_id")) for row in source if row.get("pack_id")}
    if len(row_packs) > 1:
        raise IntakeError("callback result rows contain conflicting pack identities")
    envelope_packs = {
        str(value) for value in (payload.get("pack_id"), payload.get("starter_pack_id")) if value
    }
    if len(envelope_packs) > 1:
        raise IntakeError("callback envelope contains conflicting pack identities")
    pack = next(iter(row_packs), None) or next(iter(envelope_packs), None)
    return {"tests": translate_tests(payload), "meta": {
        "outcome": "failed" if status == "failed" and not (payload.get("result_rows") or []) else "incomplete" if incomplete else "complete",
        "incomplete_reason": str(payload.get("incomplete_reason") or "")[:200] or None if incomplete else None,
        "pack_version": pack if isinstance(pack, str) and re.fullmatch(r"[a-z0-9-]{1,40}@\d{1,4}", pack) else None,
        **({"provenance": safe_provenance({"corpus_version": payload.get("corpus_version"),
            "scanner_client_version": payload.get("scanner_version")})}
           if payload.get("corpus_version") or payload.get("scanner_version") else {})}}


def report_outcome(outcome, incomplete_reason, tests: list[dict]) -> dict:
    """Starter score = passed / ran x 100; N/A (None) when coverage is incomplete."""
    passed = sum(1 for t in tests if t.get("verdict") == "pass")
    failed = sum(1 for t in tests if t.get("verdict") == "fail")
    not_run = len(tests) - passed - failed
    incomplete = outcome in {"incomplete", "failed"}
    return {"outcome": outcome or "complete", "incomplete_reason": incomplete_reason,
            "starter_score": None if incomplete or not (passed + failed) else round(100 * passed / (passed + failed), 1),
            "counts": {"passed": passed, "failed": failed, "could_not_run": not_run}}


CONNECTION_FIELDS = ('code', 'email', 'endpoint_url', 'transport', 'auth', 'model', 'system_prompt',
                     'request_template', 'response_path', 'vapi_assistant_id')


class ConnectionProofs:
    """Single-use, in-memory proof that these exact connection settings passed a check.

    Only a keyed digest of the settings is held, under a key generated per process, so
    proofs die on restart and nothing credential-derived is written to disk.
    """

    def __init__(self, ttl: int = 900, capacity: int = 2000):
        self.ttl, self.capacity = ttl, capacity
        self._key = secrets.token_bytes(32)
        self._items: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def _digest(self, data: dict) -> str:
        fields = {key: data.get(key) for key in CONNECTION_FIELDS}
        fields['email'] = str(fields['email'] or '').strip().lower()
        return hmac.new(self._key, json.dumps(fields, sort_keys=True).encode(), hashlib.sha256).hexdigest()

    def issue(self, data: dict, now: float | None = None) -> str:
        now = time.monotonic() if now is None else now
        with self._lock:
            for token in [t for t, (_, expires) in self._items.items() if expires <= now]:
                del self._items[token]
            while len(self._items) >= self.capacity:
                self._items.pop(next(iter(self._items)))
            token = secrets.token_urlsafe(24)
            self._items[token] = (self._digest(data), now + self.ttl)
            return token

    def redeem(self, token: str | None, data: dict, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            item = self._items.pop(token, None) if isinstance(token, str) else None
        return bool(item and item[1] > now and hmac.compare_digest(item[0], self._digest(data)))


class RateGate:
    """In-memory sliding-window limits, e.g. RateGate(ip=(30, 3600)). Resets on restart."""

    def __init__(self, **rules: tuple[int, int]):
        self.rules = rules
        self._hits: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, now: float | None = None, **keys: str) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._hits) > 10000:
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > now - 86400}
            buckets = []
            for name, key in keys.items():
                limit, window = self.rules[name]
                hits = [t for t in self._hits.get((name, key), []) if t > now - window]
                self._hits[(name, key)] = hits
                if len(hits) >= limit:
                    return False
                buckets.append(hits)
            for hits in buckets:
                hits.append(now)
            return True


def report_failure(row):
    return bool(row and row['status'] == 'finished' and
                str(row['incomplete_reason'] or '').startswith('report_preparation_error:'))
