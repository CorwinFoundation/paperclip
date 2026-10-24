"""Completed-scan outbox and exact, idempotent delivery of native SHIP artifacts."""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from findings_workflow import canonical, checked_request, digest, WorkflowError
from native_reports import NativeReports, RECIPIENT


class FindingsReports:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=20, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.native = NativeReports(path)
        self.db.execute('''CREATE TABLE IF NOT EXISTS findings_report_outbox (
            scan_id TEXT PRIMARY KEY, payload TEXT NOT NULL, request_sha TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending', remote_status TEXT, error TEXT,
            next_at REAL NOT NULL DEFAULT 0, send_started REAL, lease_until REAL NOT NULL DEFAULT 0,
            message_id TEXT, updated_at REAL NOT NULL)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS findings_capture_errors (
            scan_id TEXT PRIMARY KEY, error TEXT NOT NULL, next_at REAL NOT NULL, updated_at REAL NOT NULL)''')

    def enqueue(self, value):
        checked_request(value)
        data = canonical(value)
        with self.lock:
            if 'adjudication' in value:
                from adjudication_review import verify_stored_source
                source = self.db.execute('SELECT payload,request_sha FROM findings_report_outbox WHERE scan_id=?',
                                         (value['adjudication']['source_workflow_id'],)).fetchone()
                try:
                    verify_stored_source(value, source['payload'] if source else None,
                                         source['request_sha'] if source else None)
                except ValueError as exc:
                    raise WorkflowError(str(exc)) from exc
            old = self.db.execute('SELECT request_sha FROM findings_report_outbox WHERE scan_id=?', (value['workflow_id'],)).fetchone()
            if old and old['request_sha'] != digest(data.encode()):
                raise WorkflowError('immutable_request_conflict')
            self.db.execute('INSERT OR IGNORE INTO findings_report_outbox(scan_id,payload,request_sha,updated_at) VALUES (?,?,?,?)',
                            (value['workflow_id'], data, digest(data.encode()), time.time()))

    def update(self, ident, **fields):
        fields['updated_at'] = time.time()
        with self.lock:
            self.db.execute('UPDATE findings_report_outbox SET ' + ','.join(k + '=?' for k in fields) + ' WHERE scan_id=?',
                            (*fields.values(), ident))

    def expedite(self):
        # A notification only removes the scheduling delay, never a lease or review gate.
        with self.lock:
            self.db.execute("UPDATE findings_report_outbox SET next_at=0 WHERE state NOT IN ('sent','failed')")

    def capture(self, store, accepted_total, *, since, replay=(), limit=2, selection=None):
        # Never backfill historical customers as a side effect of deployment.
        if not since:
            return
        with store._lock:
            rows = list(store.db.execute("SELECT * FROM scans WHERE status='finished' AND outcome='complete' ORDER BY finished_at,id"))
        made = 0
        for row in rows:
            if row['finished_at'] < since and row['id'] not in replay:
                continue
            with self.lock:
                exists = self.db.execute('SELECT 1 FROM findings_report_outbox WHERE scan_id=?', (row['id'],)).fetchone()
                deferred = self.db.execute('SELECT 1 FROM findings_capture_errors WHERE scan_id=? AND next_at>?', (row['id'], time.time())).fetchone()
            if exists or deferred:
                continue
            try:
                value = {'workflow_id': row['id'], 'scanner_id': row['scanner_id'], 'recipient': RECIPIENT,
                         'agent_label': row['agent_label'], 'transport': row['transport'], 'pack_version': row['pack_version'],
                         'started_at': row['created_at'], 'finished_at': row['finished_at'],
                         'accepted_total': accepted_total(dict(row)), 'tests': json.loads(row['tests_json'] or '[]'),
                         'provenance': json.loads(row['provenance_json'] or '{}')}
                if selection:
                    value['selection'] = selection(dict(row))
                self.enqueue(value)
                self.db.execute('DELETE FROM findings_capture_errors WHERE scan_id=?', (row['id'],))
            except Exception:
                # Keep the blocker visible and retry saved evidence, never the target.
                with self.lock:
                    now = time.time()
                    self.db.execute('INSERT OR REPLACE INTO findings_capture_errors VALUES(?,?,?,?)',
                        (row['id'], 'evidence_capture_unconfirmed', now + 60, now))
            made += 1
            if made >= limit:
                break

    def lifecycle(self, ident):
        with self.lock:
            row = self.db.execute('SELECT state,remote_status,error,message_id,updated_at FROM findings_report_outbox WHERE scan_id=?', (ident,)).fetchone()
            capture_error = self.db.execute('SELECT updated_at FROM findings_capture_errors WHERE scan_id=?', (ident,)).fetchone()
        state = row['state'] if row else 'pending'
        remote = json.loads(row['remote_status']) if row and row['remote_status'] else {}
        report = {'pending': 'drafting', 'writing': 'drafting', 'reviewing': 'review',
                  'approved': 'approved', 'sent': 'approved', 'needs_attention': 'changes_requested'}.get(remote.get('state'), 'awaiting_results')
        if capture_error and not row:
            report = 'changes_requested'
        delivery = {'pending': 'not_ready', 'working': 'not_ready', 'sending': 'sending', 'unknown': 'unknown',
                    'accepted': 'provider_accepted', 'sent': 'provider_accepted', 'failed': 'failed', 'held': 'unknown'}.get(state, 'not_ready')
        return {'version': 'starter_lifecycle@1', 'intake_status': 'recorded', 'report_status': report,
                'delivery_status': delivery, 'updated_at': datetime.fromtimestamp(row['updated_at'], timezone.utc).isoformat() if row else None}

    @staticmethod
    def _response(response):
        if response.status_code != 200:
            raise WorkflowError('workflow_transport_unavailable')
        value = response.json()
        if (not isinstance(value, dict) or value.get('schema_version') != 'findings_workflow_status@1'
                or value.get('state') not in {'pending', 'writing', 'reviewing', 'approved', 'sent', 'needs_attention'}):
            raise WorkflowError('invalid_workflow_status')
        return value

    def tick(self, *, base_url, token, http, mail_credentials, now=None, limit=2, recover_selection=None):
        url = urlsplit(base_url)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or not token:
            return
        now = time.time() if now is None else now
        self.native.flush(http, mail_credentials, now=now)
        route = base_url.rstrip('/') + '/findings/v1/workflows'
        headers = {'Authorization': 'Bearer ' + token}
        with self.lock:
            rows = self.db.execute("SELECT * FROM findings_report_outbox WHERE state NOT IN ('sent','failed') AND next_at<=? AND lease_until<=? ORDER BY next_at,scan_id LIMIT ?", (now, now, limit)).fetchall()
            for row in rows:
                claimed = self.db.execute('UPDATE findings_report_outbox SET lease_until=?,next_at=? WHERE scan_id=? AND lease_until<=?',
                                          (now + 180, now + 15, row['scan_id'], now)).rowcount
                if not claimed:
                    continue
                ident = row['scan_id']
                try:
                    if row['state'] == 'pending':
                        remote = self._response(http.post(route, headers=headers, json=json.loads(row['payload']), timeout=30, follow_redirects=False))
                    else:
                        remote = self._response(http.get(route + '/' + ident, headers=headers, timeout=30, follow_redirects=False))
                    if remote.get('workflow_id') != ident or remote.get('request_sha256') != row['request_sha']:
                        raise WorkflowError('workflow_identity_conflict')
                    if (remote['state'] == 'needs_attention' and remote.get('error') == 'review_escalate'
                            and remote.get('round') == 1 and recover_selection
                            and 'selection' not in json.loads(row['payload'])):
                        selected = recover_selection(ident)
                        remote = self._response(http.post(route + '/' + ident + '/evidence', headers=headers, json=selected, timeout=30, follow_redirects=False))
                        if remote.get('workflow_id') != ident or remote.get('request_sha256') != row['request_sha']:
                            raise WorkflowError('workflow_identity_conflict')
                    self.update(ident, remote_status=canonical(remote), state='accepted' if row['message_id'] else 'working', error=None)
                    if row['message_id']:
                        self._receipt(route, ident, row['message_id'], remote, headers, http)
                        self.update(ident, state='sent')
                    elif remote['state'] == 'approved':
                        self._deliver(row, remote, http, mail_credentials, now)
                    elif remote['state'] == 'needs_attention':
                        self.update(ident, state='held', error=remote.get('error') or 'report_needs_attention', next_at=now + 60)
                except WorkflowError as exc:
                    self.update(ident, error=str(exc), next_at=now + 60,
                                **({'state': 'held'} if str(exc) not in {'workflow_transport_unavailable'} else {}))
                except Exception:
                    self.update(ident, error='workflow_transport_unconfirmed', next_at=now + 60)
                finally:
                    self.update(ident, lease_until=0)

    def _deliver(self, row, remote, http, mail_credentials, now):
        ident = row['scan_id']
        value = json.loads(row['payload'])
        artifacts = remote.get('artifacts')
        if not isinstance(artifacts, dict) or set(artifacts) != {'pdf', 'html'}:
            raise WorkflowError('missing_approved_artifacts')
        attachments = []
        for kind in ('pdf', 'html'):
            item = artifacts[kind]
            data = base64.b64decode(item['content'], validate=True)
            if (not 1 <= len(data) <= 6_000_000 or digest(data) != item['sha256']
                    or (kind == 'pdf' and not data.startswith(b'%PDF-'))):
                raise WorkflowError('approved_artifact_mismatch')
            attachments.append({'filename': 'BackBond-Findings-Report.' + kind, 'content': item['content'],
                                'content_type': 'application/pdf' if kind == 'pdf' else 'text/html'})
        first = row['send_started']
        # An unknown send may be retried only inside the provider's 24h idempotency
        # window with the identical recipient, body, attachments and key.
        if first and now - first >= 23 * 3600:
            self.update(ident, state='held', error='provider_idempotency_window_expired')
            return
        key = 'findings-report:' + digest((ident + '|' + artifacts['pdf']['sha256'] + '|' + artifacts['html']['sha256']).encode())
        body = {'from': 'BackBond <info@backbond.ai>', 'to': [value['recipient']],
                'subject': 'Your BackBond Findings Report',
                'text': 'Your Findings Report is attached as PDF and HTML. It was prepared from your completed scan and independently reviewed.\n\nQuestions: info@backbond.ai',
                'attachments': attachments}
        # Freeze the new notification before sending. An older uncertain send
        # keeps its original provider key and exact attachment payload.
        if not first or self.native.token_for(ident):
            report_token = self.native.publish(ident, remote)
            self.update(ident, state='sending', send_started=first or now)
            message = self.native.notify(report_token, http, mail_credentials, now=now)
            if message:
                self.update(ident, state='accepted', message_id=message, error=None)
            else:
                notification = self.native.db.execute('SELECT state,error FROM native_report_notifications WHERE token=?', (report_token,)).fetchone()
                self.update(ident, state=notification['state'] if notification['state'] in {'held','failed'} else 'unknown',
                            error=notification['error'])
            return
        credentials = mail_credentials()
        if credentials.get('provider') != 'resend' or not credentials.get('api_key'):
            raise WorkflowError('mail_not_configured')
        # Persist sending intent before network I/O. A restart uses the same key.
        self.update(ident, state='sending', send_started=first or now)
        try:
            response = http.post('https://api.resend.com/emails', headers={
                'Authorization': 'Bearer ' + credentials['api_key'], 'Idempotency-Key': key,
                'User-Agent': 'BackBond-Findings-Delivery/1.0'}, json=body, timeout=30, follow_redirects=False)
        except Exception:
            self.update(ident, state='unknown', error='provider_outcome_unknown')
            return
        finally:
            credentials.clear()
        if response.status_code in {200, 201}:
            result = response.json()
            message = result.get('id')
            if not isinstance(message, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', message):
                self.update(ident, state='unknown', error='provider_outcome_unknown')
                return
            self.update(ident, state='accepted', message_id=message, error=None)
        elif response.status_code in {400, 401, 403, 404, 405, 413, 415, 422}:
            self.update(ident, state='failed', error='provider_rejected')
        else:
            self.update(ident, state='unknown', error='provider_outcome_unknown')

    def _receipt(self, route, ident, message, remote, headers, http):
        artifacts = remote['artifacts']
        receipt = {'message_id': message, **{kind + '_sha256': artifacts[kind]['sha256'] for kind in ('pdf', 'html')}}
        self._response(http.post(route + '/' + ident + '/delivery', headers=headers, json=receipt, timeout=30, follow_redirects=False))
