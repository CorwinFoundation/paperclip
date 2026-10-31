"""Private, immutable SHIP artifacts and recipient-authenticated website sessions."""
from __future__ import annotations

import base64
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time

from report_mail_transport import SENDER, REPLY_TO

from findings_workflow import WorkflowError, canonical, digest

from report_recipient_policy import RECIPIENT
TOKEN = re.compile(r'[a-f0-9]{32}\Z')


class NativeReports:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=20, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS native_report_publications(
            workflow_id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE,
            artifacts TEXT NOT NULL, recipient TEXT NOT NULL, created_at REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS native_report_challenges(
            nonce TEXT PRIMARY KEY, token TEXT NOT NULL, code_hash TEXT NOT NULL,
            client TEXT NOT NULL, created_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            used INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS native_report_sessions(
            session_hash TEXT PRIMARY KEY, token TEXT NOT NULL,
            created_at REAL NOT NULL, last_seen REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS native_report_notifications(
            token TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
            started_at REAL, next_at REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
            message_id TEXT, error TEXT);
        ''')

    def publish(self, workflow_id, remote):
        if remote.get('state') not in {'approved', 'sent'} or remote.get('workflow_id') != workflow_id:
            raise WorkflowError('report_not_approved')
        artifacts = remote.get('artifacts')
        if not isinstance(artifacts, dict) or set(artifacts) != {'pdf', 'html'}:
            raise WorkflowError('missing_approved_artifacts')
        for kind, item in artifacts.items():
            data = base64.b64decode(item['content'], validate=True)
            if (not 1 <= len(data) <= 6_000_000 or digest(data) != item['sha256']
                    or (kind == 'pdf' and not data.startswith(b'%PDF-'))):
                raise WorkflowError('approved_artifact_mismatch')
        frozen = canonical(artifacts)
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                row = self.db.execute('SELECT * FROM native_report_publications WHERE workflow_id=?', (workflow_id,)).fetchone()
                if row and (row['artifacts'] != frozen or row['recipient'] != RECIPIENT):
                    raise WorkflowError('published_artifact_conflict')
                if not row:
                    token = secrets.token_hex(16)
                    self.db.execute('INSERT INTO native_report_publications VALUES(?,?,?,?,?)',
                                    (workflow_id, token, frozen, RECIPIENT, time.time()))
                else:
                    token = row['token']
                body = {'from': SENDER, 'reply_to': REPLY_TO, 'to': [RECIPIENT],
                        'subject': 'BackBond Findings Report - ' + token,
                        'text': 'Your approved BackBond Findings Report is ready.\n\n'
                                + 'https://backbond.ai/scan/r/' + token
                                + '\n\nOpen the link and verify access with the code emailed to ' + RECIPIENT + '.'
                                + '\n\nQuestions: info@backbond.ai'}
                self.db.execute('INSERT OR IGNORE INTO native_report_notifications(token,payload) VALUES(?,?)',
                                (token, canonical(body)))
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
        return token

    def notify(self, token, http, mail_credentials, *, now=None):
        now = time.time() if now is None else now
        for kind in ('pdf', 'html'):
            if self.artifact(token, kind) is None:
                raise WorkflowError('published_artifact_unavailable')
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                row = self.db.execute('SELECT * FROM native_report_notifications WHERE token=?', (token,)).fetchone()
                if row['message_id']:
                    self.db.execute('COMMIT')
                    return row['message_id']
                if row['state'] in {'failed', 'held'} or row['lease_until'] > now or row['next_at'] > now:
                    self.db.execute('COMMIT')
                    return None
                if row['started_at'] is not None and now - row['started_at'] >= 23 * 3600:
                    self.db.execute("UPDATE native_report_notifications SET state='held',error='provider_idempotency_window_expired' WHERE token=?", (token,))
                    self.db.execute('COMMIT')
                    return None
                self.db.execute("UPDATE native_report_notifications SET state='sending',started_at=COALESCE(started_at,?),lease_until=? WHERE token=?",
                                (now, now + 180, token))
                body = json.loads(row['payload'])
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
        state, message, error = 'unknown', None, 'provider_outcome_unknown'
        credentials = {}
        try:
            credentials = mail_credentials()
            if credentials.get('provider') != 'resend' or not credentials.get('api_key'):
                raise WorkflowError('mail_not_configured')
            response = http.post('https://api.resend.com/emails', headers={
                'Authorization': 'Bearer ' + credentials['api_key'],
                'Idempotency-Key': 'findings-report-link:' + token,
                'User-Agent': 'BackBond-Findings-Delivery/2.0'},
                json=body, timeout=30, follow_redirects=False)
            if response.status_code in {200, 201}:
                candidate = response.json().get('id')
                if isinstance(candidate, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', candidate):
                    state, message, error = 'sent', candidate, None
            elif response.status_code in {400, 401, 403, 404, 405, 413, 415, 422}:
                state, error = 'failed', 'provider_rejected'
        except Exception:
            pass
        finally:
            credentials.clear()
        with self.lock:
            self.db.execute('UPDATE native_report_notifications SET state=?,message_id=?,error=?,lease_until=0,next_at=? WHERE token=?',
                            (state, message, error, now + 60, token))
        return message

    def flush(self, http, mail_credentials, *, now=None):
        now = time.time() if now is None else now
        self.purge_expired(now=now)
        with self.lock:
            rows = self.db.execute("SELECT token FROM native_report_notifications WHERE state IN ('pending','unknown','sending') AND next_at<=? AND lease_until<=? LIMIT 2", (now, now)).fetchall()
        for row in rows:
            self.notify(row['token'], http, mail_credentials, now=now)

    def purge_expired(self, *, now=None):
        now = time.time() if now is None else now
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.db.execute('DELETE FROM native_report_challenges WHERE created_at<=?', (now - 3600,))
                self.db.execute('DELETE FROM native_report_sessions WHERE created_at<=? OR last_seen<=?', (now - 1800, now - 600))
                self.db.execute('DELETE FROM native_report_notifications WHERE token IN (SELECT token FROM native_report_publications WHERE created_at<=?)', (now - 180 * 86400,))
                self.db.execute('DELETE FROM native_report_publications WHERE created_at<=?', (now - 180 * 86400,))
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def publication(self, token, *, now=None):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return None
        now = time.time() if now is None else now
        with self.lock:
            row = self.db.execute('SELECT * FROM native_report_publications WHERE token=?', (token,)).fetchone()
        return dict(row) if row and row['created_at'] + 180 * 86400 > now and row['recipient'] == RECIPIENT else None

    def token_for(self, workflow_id):
        with self.lock:
            row = self.db.execute('SELECT token FROM native_report_publications WHERE workflow_id=?', (workflow_id,)).fetchone()
        return row['token'] if row else None

    def issue_code(self, token, client, *, now=None):
        now = time.time() if now is None else now
        if not self.publication(token, now=now):
            return None
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                recent = self.db.execute('SELECT client FROM native_report_challenges WHERE token=? AND created_at>?',
                                         (token, now - 3600)).fetchall()
                if len(recent) >= 50 or sum(row['client'] == client for row in recent) >= 5:
                    self.db.execute('COMMIT')
                    return None
                nonce, code = secrets.token_hex(16), f'{secrets.randbelow(1_000_000):06d}'
                self.db.execute('INSERT INTO native_report_challenges(nonce,token,code_hash,client,created_at) VALUES(?,?,?,?,?)',
                                (nonce, token, digest((nonce + ':' + code).encode()), client, now))
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
        return RECIPIENT, nonce + '.' + code

    def verify_code(self, token, presented, *, now=None):
        now = time.time() if now is None else now
        if not self.publication(token, now=now):
            return None
        try:
            nonce, code = presented.split('.', 1)
        except (AttributeError, ValueError):
            return None
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                row = self.db.execute('SELECT * FROM native_report_challenges WHERE nonce=? AND token=?', (nonce, token)).fetchone()
                if not row or row['used'] or row['attempts'] >= 5 or row['created_at'] + 600 <= now:
                    self.db.execute('COMMIT')
                    return None
                if not hmac.compare_digest(row['code_hash'], digest((nonce + ':' + code).encode())):
                    self.db.execute('UPDATE native_report_challenges SET attempts=attempts+1 WHERE nonce=?', (nonce,))
                    self.db.execute('COMMIT')
                    return None
                self.db.execute('UPDATE native_report_challenges SET used=1 WHERE nonce=?', (nonce,))
                session = secrets.token_urlsafe(32)
                self.db.execute('INSERT INTO native_report_sessions VALUES(?,?,?,?)', (digest(session.encode()), token, now, now))
                self.db.execute('COMMIT')
                return session
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def authorize(self, token, session, *, now=None):
        now = time.time() if now is None else now
        if not session or not self.publication(token, now=now):
            return False
        with self.lock:
            key = digest(session.encode())
            row = self.db.execute('SELECT * FROM native_report_sessions WHERE session_hash=? AND token=?', (key, token)).fetchone()
            if not row or row['created_at'] + 1800 <= now or row['last_seen'] + 600 <= now:
                return False
            self.db.execute('UPDATE native_report_sessions SET last_seen=? WHERE session_hash=?', (now, key))
        return True

    def revoke(self, session):
        if session:
            with self.lock:
                self.db.execute('DELETE FROM native_report_sessions WHERE session_hash=?', (digest(session.encode()),))

    def artifact(self, token, kind):
        row = self.publication(token)
        if not row or kind not in {'pdf', 'html'}:
            return None
        item = json.loads(row['artifacts'])[kind]
        data = base64.b64decode(item['content'], validate=True)
        if digest(data) != item['sha256']:
            raise WorkflowError('published_artifact_mismatch')
        return data
