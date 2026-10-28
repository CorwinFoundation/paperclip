"""BAC persistence boundary. No scanner secrets belong in the issue payload.

Uses Paperclip's company issue-create contract. A lost create response is
uncertain, never permission to create another issue or dispatch another scan.
"""
import json
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote


def iso_time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def safe_progress(value):
    """Closed schema: arbitrary upstream strings and nested payloads stay private."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ("state", "phase"):
        allowed = {"running", "queued", "paused", "stalled", "unconfirmed", "finished",
                   "incomplete", "error", "execution", "report_preparation", "unknown"}
        if isinstance(value.get(key), str) and value[key] in allowed:
            result[key] = value[key]
    if value.get("failed_stage") == "report_preparation":
        result["failed_stage"] = "report_preparation"
        for key in ("execution_completed", "report_retry_allowed", "retry_allowed"):
            if type(value.get(key)) is bool:
                result[key] = value[key]
        if value.get("report_status") == "failed":
            result["report_status"] = "failed"
    for key in ("processed_count", "accepted_pack_total", "usable_count", "unavailable_count"):
        item = value.get(key)
        if type(item) is int and 0 <= item <= 100000:
            result[key] = item
    total = result.get("accepted_pack_total")
    if total is not None and any(result.get(k, 0) > total for k in
            ("processed_count", "usable_count", "unavailable_count")):
        return {}
    for key in ("heartbeat_at", "updated_at", "last_activity_at"):
        timestamp = iso_time(value.get(key))
        if timestamp:
            result[key] = timestamp
    return result


class BACUnconfirmed(RuntimeError):
    pass


class BACIntake:
    def __init__(self, path, *, base_url, company_id, owner_id, token, post, get=None):
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.lock = threading.RLock()
        self.base_url = base_url.rstrip('/')
        self.company_id, self.owner_id, self.token = company_id, owner_id, token
        self.post = post
        self.get = get
        self.db.execute('CREATE TABLE IF NOT EXISTS bac_intake '
                        '(request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, '
                        'state TEXT NOT NULL, issue_id TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS bac_reconcile_retry '
                        '(request_id TEXT PRIMARY KEY, attempts INTEGER NOT NULL, '
                        'next_at REAL NOT NULL)')

        self.db.execute('CREATE TABLE IF NOT EXISTS bac_events ' 
                        '(event_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, payload TEXT NOT NULL, ' 
                        'state TEXT NOT NULL, attempts INTEGER NOT NULL, next_at REAL NOT NULL)')

        self.db.execute('CREATE TABLE IF NOT EXISTS bac_scan_bindings ' 
                        '(scan_id TEXT PRIMARY KEY, request_id TEXT NOT NULL)')

        self.db.execute('CREATE TABLE IF NOT EXISTS bac_observations '
                        '(scan_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, observed_at TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS bac_confirmed_progress '
                        '(scan_id TEXT PRIMARY KEY, processed_count INTEGER NOT NULL, confirmed_at TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS bac_intake_observed '
                        '(request_id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT NOT NULL)')

        self.db.execute('CREATE TABLE IF NOT EXISTS bac_scan_retry ' 
                        '(scan_id TEXT PRIMARY KEY, next_at REAL NOT NULL)')

    def retry_identity(self, scan_id, fields):
        """Reuse a case only for an identical, previously cancelled attempt.

        A database-local HMAC key keeps private intake fields out of stored
        fingerprints and BAC. Successful scans always get a fresh identity.
        """
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.db.execute('CREATE TABLE IF NOT EXISTS bac_identity_key '
                                '(id INTEGER PRIMARY KEY CHECK(id=1), secret TEXT NOT NULL)')
                self.db.execute('INSERT OR IGNORE INTO bac_identity_key VALUES (1,?)',
                                (secrets.token_hex(32),))
                self.db.execute('CREATE TABLE IF NOT EXISTS bac_retry_identity '
                                '(fingerprint TEXT PRIMARY KEY, request_id TEXT NOT NULL, scan_id TEXT NOT NULL)')
                key = self.db.execute('SELECT secret FROM bac_identity_key WHERE id=1').fetchone()[0]
                canonical = dict(fields)
                canonical['email'] = str(canonical['email']).strip().lower()
                # Credentials and short-lived connection proofs are deliberately
                # excluded; target, scope, model and submitted prompt stay bound.
                for name in ('auth', 'connection_proof'):
                    canonical.pop(name, None)
                fingerprint = hmac.new(key.encode(), json.dumps(canonical, sort_keys=True,
                                       default=str).encode(), hashlib.sha256).hexdigest()
                row = self.db.execute('SELECT request_id,scan_id FROM bac_retry_identity '
                                      'WHERE fingerprint=?', (fingerprint,)).fetchone()
                previous = self.db.execute('SELECT status FROM scans WHERE id=?',
                                           (row[1],)).fetchone() if row else None
                request_id = row[0] if previous and previous[0] == 'cancelled' else scan_id
                self.db.execute('INSERT OR REPLACE INTO bac_retry_identity VALUES (?,?,?)',
                                (fingerprint, request_id, scan_id))
                self.db.execute('INSERT OR IGNORE INTO bac_scan_bindings VALUES (?,?)', (scan_id, request_id))
                self.db.execute('COMMIT')
                return request_id
            except Exception:
                if self.db.in_transaction:
                    self.db.execute('ROLLBACK')
                raise

    def acknowledge(self, request_id, *, customer_ref, agent_ref, scope_ref, code_ref):
        if not all((request_id, customer_ref, agent_ref, scope_ref, code_ref)):
            raise BACUnconfirmed('missing intake reference')
        # References only: callers retain endpoints, auth and prompts in their
        # existing private/ephemeral stores. Never pass a raw partner code here.
        payload = {'title': 'Starter request ' + request_id,
                   'description': json.dumps(dict(request_id=request_id,
                       customer_ref=customer_ref, agent_ref=agent_ref,
                       scope_ref=scope_ref, code_ref=code_ref), sort_keys=True),
                   'status': 'todo', 'priority': 'medium',
                   'assigneeAgentId': self.owner_id}
        encoded = json.dumps(payload, sort_keys=True)
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                row = self.db.execute('SELECT payload,state,issue_id FROM bac_intake '
                                      'WHERE request_id=?', (request_id,)).fetchone()
                if row:
                    if row[0] != encoded:
                        raise BACUnconfirmed('request identity conflict')
                    if row[1] == 'acknowledged':
                        self.db.execute('COMMIT')
                        return row[2]
                    self.db.execute('ROLLBACK')
                    return self.reconcile(request_id)
                if not all((self.base_url.startswith('https://'), self.company_id,
                            self.owner_id, self.token)):
                    raise BACUnconfirmed('BAC service configuration unavailable')
                # Commit intent before network I/O. A crash at any later point
                # cannot lead to a second create or an unacknowledged dispatch.
                self.db.execute('INSERT INTO bac_intake VALUES (?,?,?,NULL)',
                                (request_id, encoded, 'unconfirmed'))
                self.db.execute('COMMIT')
            except Exception:
                if self.db.in_transaction:
                    self.db.execute('ROLLBACK')
                raise
        try:
            response = self.post(self.base_url + '/api/companies/' + self.company_id + '/issues',
                                 json=payload, headers={'Authorization': 'Bearer ' + self.token},
                                 timeout=20, follow_redirects=False)
            if response.status_code != 201:
                raise BACUnconfirmed('BAC acceptance unconfirmed')
            value = response.json()
            issue_id = value.get('id')
            if (not isinstance(issue_id, str) or not issue_id
                    or value.get('companyId') != self.company_id
                    or value.get('assigneeAgentId') != self.owner_id):
                raise BACUnconfirmed('BAC acknowledgment mismatch')
        except Exception:
            raise BACUnconfirmed('BAC acceptance unconfirmed; reconcile same request') from None
        with self.lock:
            self.db.execute("UPDATE bac_intake SET state='acknowledged',issue_id=? WHERE request_id=?",
                            (issue_id, request_id))
        return issue_id

    def reconcile(self, request_id):
        """Authenticated read only recovery. Never creates a second case."""
        with self.lock:
            row = self.db.execute('SELECT payload,state,issue_id FROM bac_intake WHERE request_id=?',
                                  (request_id,)).fetchone()
            if not row:
                raise BACUnconfirmed('unknown intake request')
            if row[1] == 'acknowledged':
                return row[2]
            if not self.get or not self.token:
                raise BACUnconfirmed('authenticated reconciliation unavailable')
            expected = json.loads(row[0])
        try:
            response = self.get(self.base_url + '/api/companies/' + self.company_id +
                                '/issues?q=' + quote(request_id, safe=''),
                                headers={'Authorization': 'Bearer ' + self.token},
                                timeout=20, follow_redirects=False)
            if response.status_code != 200:
                raise ValueError()
            matches = [v for v in response.json() if
                       v.get('companyId') == self.company_id and
                       v.get('assigneeAgentId') == self.owner_id and
                       v.get('title') == expected['title'] and
                       json.loads(v.get('description', '{}')) == json.loads(expected['description'])]
            if len(matches) != 1 or not isinstance(matches[0].get('id'), str) or not matches[0]['id']:
                raise ValueError()
            issue_id = matches[0]['id']
        except Exception:
            raise BACUnconfirmed('BAC reconciliation unconfirmed') from None
        with self.lock:
            self.db.execute("UPDATE bac_intake SET state='acknowledged',issue_id=? WHERE request_id=?",
                            (issue_id, request_id))
        return issue_id

    def reconcile_pending(self, now=None, limit=10):
        """Bounded authenticated reads of orphaned intents, with durable backoff.

        Never creates another case or dispatches a scan. Reserve the next retry
        before network I/O so concurrent workers and restarts cannot hot-loop.
        """
        now = time.time() if now is None else now
        acknowledged = []
        for _ in range(max(0, min(limit, 100))):
            with self.lock:
                self.db.execute('BEGIN IMMEDIATE')
                try:
                    row = self.db.execute(
                        "SELECT b.request_id, COALESCE(r.attempts,0) FROM bac_intake b "
                        "LEFT JOIN bac_reconcile_retry r ON r.request_id=b.request_id "
                        "WHERE b.state='unconfirmed' AND COALESCE(r.next_at,0)<=? "
                        "ORDER BY COALESCE(r.next_at,0), b.request_id LIMIT 1", (now,)).fetchone()
                    if row:
                        request_id, attempts = row
                        delay = min(3600, 10 * 2 ** min(attempts, 9))
                        self.db.execute('INSERT OR REPLACE INTO bac_reconcile_retry VALUES (?,?,?)',
                                        (request_id, attempts + 1, now + delay))
                    self.db.execute('COMMIT')
                except Exception:
                    if self.db.in_transaction:
                        self.db.execute('ROLLBACK')
                    raise
            if not row:
                break
            try:
                issue_id = self.reconcile(request_id)
            except BACUnconfirmed:
                continue
            acknowledged.append((request_id, issue_id))
        return acknowledged

    def enqueue_event(self, request_id, *, scan_id, event_type, revision_sha256, reference, snapshot=None):
        """Immutable opaque references only; identity excludes retry time."""
        import re
        if event_type not in {'lifecycle', 'evidence', 'report_revision', 'send_receipt'}:
            raise BACUnconfirmed('invalid event type')
        if not re.fullmatch(r'[a-f0-9]{64}', revision_sha256):
            raise BACUnconfirmed('invalid revision')
        if any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_:@.-]{1,200}', v)
               for v in (request_id, scan_id, reference)):
            raise BACUnconfirmed('invalid opaque reference')
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                row = self.db.execute("SELECT issue_id FROM bac_intake WHERE request_id=? AND state='acknowledged'",
                                      (request_id,)).fetchone()
                if not row:
                    raise BACUnconfirmed('case unconfirmed')
                event = dict(request_id=request_id, case_id=row[0], scan_id=scan_id,
                             type=event_type, revision_sha256=revision_sha256, reference=reference)
                if snapshot is not None:
                    event['snapshot'] = snapshot
                event_id = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
                event.update(event_id=event_id, occurred_at=time.time())
                self.db.execute('INSERT OR IGNORE INTO bac_events VALUES (?,?,?, ?,0,0)',
                                (event_id, row[0], json.dumps(event, sort_keys=True), 'pending'))
                self.db.execute('COMMIT')
                return event_id
            except Exception:
                if self.db.in_transaction:
                    self.db.execute('ROLLBACK')
                raise

    def flush_events(self, now=None, limit=1):
        """Claim once before POST. Uncertain appends are read-only forever.

        A plain Paperclip comment POST has no atomic idempotency key. An empty
        read after a timeout is therefore NOT permission to repeat that POST.
        """
        now = time.time() if now is None else now
        for _ in range(max(0, min(limit, 100))):
            with self.lock:
                self.db.execute('BEGIN IMMEDIATE')
                row = self.db.execute("SELECT event_id,case_id,payload,state,attempts FROM bac_events "
                                      "WHERE state!='acknowledged' AND next_at<=? ORDER BY next_at,event_id LIMIT 1",
                                      (now,)).fetchone()
                if row:
                    self.db.execute("UPDATE bac_events SET state='uncertain',attempts=attempts+1,next_at=? WHERE event_id=?",
                                    (now + min(3600, 10 * 2 ** min(row[4], 9)), row[0]))
                self.db.execute('COMMIT')
            if not row:
                break
            event_id, case_id, payload, state, _ = row
            route = self.base_url + '/api/issues/' + quote(case_id, safe='') + '/comments'
            options = dict(headers={'Authorization': 'Bearer ' + self.token}, timeout=20, follow_redirects=False)
            if not self.token or not self.base_url.startswith('https://'):
                continue
            try:
                if state == 'pending':
                    response = self.post(route, json={'body': payload}, **options)
                    records = [response.json()] if response.status_code == 201 else []
                else:
                    if not self.get:
                        continue
                    response = self.get(route, **options)
                    records = response.json() if response.status_code == 200 else []
                matches = [r for r in records if json.loads(r.get('body', '{}')) == json.loads(payload)
                           and r.get('issueId') == case_id and isinstance(r.get('id'), str) and r['id']]
                if len(matches) != 1:
                    continue
            except Exception:
                continue
            with self.lock:
                self.db.execute("UPDATE bac_events SET state='acknowledged' WHERE event_id=?", (event_id,))

    def capture_scans(self, store, queue, fetch_terminal=None, *, now=None, limit=1, fetch_progress=None,
                      on_finished=None):
        """Recover and project bounded correlated local state without private text.

        ``on_finished(request_id, scan_id)`` runs for a finished scan bound to an acknowledged
        case (every revisit; the hook must be idempotent). Its failures never block capture."""
        from qa_service import translate_callback, report_failure
        now = time.time() if now is None else now
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            bindings = self.db.execute('SELECT b.scan_id,b.request_id FROM bac_scan_bindings b '
                'LEFT JOIN bac_scan_retry r ON r.scan_id=b.scan_id WHERE COALESCE(r.next_at,0)<=? '
                'ORDER BY COALESCE(r.next_at,0),b.scan_id LIMIT ?', (now, max(0, min(limit, 100)))).fetchall()
            for scan_id, _ in bindings:
                self.db.execute('INSERT OR REPLACE INTO bac_scan_retry VALUES (?,?)', (scan_id, now + 30))
            self.db.execute('COMMIT')
        for scan_id, request_id in bindings:
            row = store.scan(scan_id)
            if not row:
                continue
            if (row['status'] in {'running', 'dispatch_unknown'} or report_failure(row)) and row['scanner_id'] and fetch_terminal:
                try:
                    value = fetch_terminal(dict(row))
                    if (isinstance(value, dict) and value.get('scan_id') == row['scanner_id']
                            and value.get('status') in {'complete', 'completed', 'failed', 'incomplete', 'partial'}
                            and isinstance(value.get('result_rows', []), list)
                            and len(value.get('result_rows', [])) <= 100
                            and all(isinstance(r, dict) for r in value.get('result_rows', []))
                            and (value.get('result_rows') or (value['status'] == 'failed'
                                 and value.get('incomplete') is True
                                 and bool(value.get('incomplete_reason'))))):
                        value = dict(value)
                        if value.get('partial_completion'):
                            value['incomplete'] = True
                        if report_failure(row):
                            store.refresh_report_failure(scan_id, row['scanner_id'], value)
                        else:
                            queue.callback(row['scanner_id'], translate_callback(value))
                        row = store.scan(scan_id)
                except Exception:
                    pass  # Unknown/foreign/malformed state never completes or redispatches.
            progress = {}
            if fetch_progress:
                try:
                    progress = safe_progress(fetch_progress(scan_id))
                except Exception:
                    pass
            # Heartbeats and successful reads do not establish advancement.
            processed = progress.get('processed_count')
            if type(processed) is int and processed > 0:
                with self.lock:
                    previous = self.db.execute('SELECT processed_count FROM bac_confirmed_progress WHERE scan_id=?',
                                               (scan_id,)).fetchone()
                    if not previous or processed > previous[0]:
                        self.db.execute('INSERT OR REPLACE INTO bac_confirmed_progress VALUES (?,?,?)',
                                        (scan_id, processed, datetime.fromtimestamp(now, timezone.utc).isoformat()))
            projection = dict(status=row['status'], outcome=row['outcome'], progress=progress)
            reason = ('scanner_failed' if row['outcome'] == 'failed' else
                      'partial_results' if row['outcome'] == 'incomplete' else
                      'dispatch_unconfirmed' if row['status'] == 'dispatch_unknown' else
                      'progress_unavailable' if not progress and row['status'] == 'running' else None)
            heartbeat = progress.get('heartbeat_at') or progress.get('updated_at')
            if heartbeat and row['status'] in {'running', 'dispatch_unknown'}:
                if now - datetime.fromisoformat(heartbeat).timestamp() > 120:
                    reason = 'stale_progress'
            if row['incomplete_reason'] in {
                    'scanner_restart_resume_exhausted', 'customer_endpoint_unreachable_transport',
                    'full_scan_registry_incomplete', 'full_suite_manifest_incomplete'}:
                reason = row['incomplete_reason']
            projection['reason_code'] = reason
            digest = hashlib.sha256(json.dumps(projection, sort_keys=True).encode()).hexdigest()
            observed_at = datetime.fromtimestamp(now, timezone.utc).isoformat()
            with self.lock:
                previous = self.db.execute('SELECT snapshot,observed_at FROM bac_observations WHERE scan_id=?',
                                           (scan_id,)).fetchone()
                encoded = json.dumps(projection, sort_keys=True)
                if previous and previous[0] == encoded:
                    observed_at = previous[1]
                self.db.execute('INSERT OR REPLACE INTO bac_observations VALUES (?,?,?)',
                                (scan_id, encoded, observed_at))
            snapshot = dict(projection, observed_at=observed_at)
            try:
                self.enqueue_event(request_id, scan_id=scan_id, event_type='lifecycle',
                                   revision_sha256=digest, reference='state:' + row['status'] + ':' + str(row['outcome'] or 'pending'),
                                   snapshot=snapshot)
                if row['status'] == 'finished':
                    revision = hashlib.sha256((row['tests_json'] or '[]').encode()).hexdigest()
                    self.enqueue_event(request_id, scan_id=scan_id, event_type='evidence',
                                       revision_sha256=revision, reference='intake:' + scan_id)
            except BACUnconfirmed:
                continue
            if row['status'] == 'finished' and on_finished:
                try:
                    on_finished(request_id, scan_id)
                except Exception:
                    pass  # Durable hook state is retried on the next revisit.

    def public_lifecycle(self, scan_id, *, now=None):
        """Intake acknowledgement only; report/delivery require their own durable authority."""
        now = time.time() if now is None else now
        with self.lock:
            row = self.db.execute('SELECT b.request_id,i.state FROM bac_scan_bindings b '
                                  'LEFT JOIN bac_intake i ON i.request_id=b.request_id '
                                  'WHERE b.scan_id=?', (scan_id,)).fetchone()
            if not row:
                return dict(version='starter_lifecycle@1', intake_status='unavailable',
                            report_status='unavailable', delivery_status='unavailable', updated_at=None)
            state = {'acknowledged': 'recorded', 'unconfirmed': 'unknown'}.get(row[1], 'pending')
            previous = self.db.execute('SELECT state,updated_at FROM bac_intake_observed WHERE request_id=?',
                                       (row[0],)).fetchone()
            timestamp = previous[1] if previous and previous[0] == state else datetime.fromtimestamp(now, timezone.utc).isoformat()
            self.db.execute('INSERT OR REPLACE INTO bac_intake_observed VALUES (?,?,?)',
                            (row[0], state, timestamp))
            return dict(version='starter_lifecycle@1', intake_status=state,
                        report_status='unavailable', delivery_status='unavailable', updated_at=timestamp)

    def is_bound(self, scan_id):
        """True when intake bound this scan to a BAC request (every BAC-before-dispatch scan).
        Such a scan's report is reachable only through the Lead-approved release path."""
        with self.lock:
            return self.db.execute('SELECT 1 FROM bac_scan_bindings WHERE scan_id=?',
                                   (scan_id,)).fetchone() is not None

    def bound_case(self, scan_id):
        """(request_id, case_id) for a scan bound to an acknowledged BAC case, else None."""
        with self.lock:
            row = self.db.execute("SELECT b.request_id,i.issue_id FROM bac_scan_bindings b JOIN bac_intake i "
                                  "ON i.request_id=b.request_id WHERE b.scan_id=? AND i.state='acknowledged'",
                                  (scan_id,)).fetchone()
        return (row[0], row[1]) if row else None

    def report_decision(self, scan_id, revision_sha256):
        """Read-only native Lead decision for one exact report revision, through the BAC gateway
        (BAC_GATEWAY.md: GET /api/issues/<case>/starter-report-approval). Returns None when no
        decision exists, or the single decision whose every binding (request, case, scan, digest)
        matches. Non-200 (the gateway 403s duplicate/conflicting decisions), more than one record,
        an extra or missing field, or any mismatched binding -> BACUnconfirmed. Intake never
        writes approvals; this only reads."""
        import re
        bound = self.bound_case(scan_id)
        if not bound or not isinstance(revision_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', revision_sha256):
            raise BACUnconfirmed('case unconfirmed')
        if not self.get or not self.token or not self.base_url.startswith('https://'):
            raise BACUnconfirmed('authenticated decision read unavailable')
        request_id, case_id = bound
        route = (self.base_url + '/api/issues/' + quote(case_id, safe='') + '/starter-report-approval?scan_id='
                 + quote(scan_id, safe='') + '&revision_sha256=' + revision_sha256)
        expected = dict(type='report_approval', request_id=request_id, case_id=case_id, scan_id=scan_id,
                        revision_sha256=revision_sha256)
        try:
            response = self.get(route, headers={'Authorization': 'Bearer ' + self.token},
                                timeout=20, follow_redirects=False)
            if response.status_code != 200:
                raise ValueError()
            records = response.json()
            if not isinstance(records, list) or len(records) > 1:
                raise ValueError()
            if not records:
                return None
            value = records[0]
            if (not isinstance(value, dict) or set(value) != set(expected) | {'decision', 'id', 'authorAgentId'}
                    or any(value[k] != v for k, v in expected.items())
                    or value['decision'] not in ('approve', 'reject')
                    or not isinstance(value['id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value['id'])
                    or not isinstance(value['authorAgentId'], str) or not value['authorAgentId']):
                raise ValueError()
        except Exception:
            raise BACUnconfirmed('decision unconfirmed') from None
        return dict(value)

    def confirmed_progress_at(self, scan_id):
        with self.lock:
            row = self.db.execute('SELECT confirmed_at FROM bac_confirmed_progress WHERE scan_id=?',
                                  (scan_id,)).fetchone()
            return row[0] if row else None

    def latest_progress(self, scan_id):
        with self.lock:
            row = self.db.execute('SELECT snapshot FROM bac_observations WHERE scan_id=?', (scan_id,)).fetchone()
            return json.loads(row[0]).get('progress', {}) if row else {}

    def _bound_finished_scan(self, store, request_id, case_id, scan_id):
        with self.lock:
            binding = self.db.execute(
                "SELECT 1 FROM bac_scan_bindings b JOIN bac_intake i ON i.request_id=b.request_id "
                "WHERE b.scan_id=? AND b.request_id=? AND i.issue_id=? AND i.state='acknowledged'",
                (scan_id, request_id, case_id)).fetchone()
        row = store.scan(scan_id) if binding else None
        if not row or row['status'] != 'finished':
            raise BACUnconfirmed('evidence unavailable')
        return row

    def evidence_digest(self, store, *, request_id, case_id, scan_id):
        """Current evidence digest of a bound, acknowledged, finished case; same checks as
        resolve_evidence, so the Analyst can name the exact evidence it will report on."""
        row = self._bound_finished_scan(store, request_id, case_id, scan_id)
        return hashlib.sha256((row['tests_json'] or '[]').encode()).hexdigest()

    def resolve_evidence(self, store, *, request_id, case_id, scan_id, revision_sha256):
        """Private gateway primitive; caller must authorize the requested case first.

        No public HTTP route or general-purpose SQL interface is exposed. Raw
        target inputs never leave this boundary, even for an authorized case.
        """
        row = self._bound_finished_scan(store, request_id, case_id, scan_id)
        actual = hashlib.sha256((row['tests_json'] or '[]').encode()).hexdigest()
        if not hmac.compare_digest(actual, revision_sha256):
            raise BACUnconfirmed('evidence revision mismatch')
        # Tests carry private prompt/reply/reason text. Return fixed verdicts
        # by ordinal only; full private review needs a separately approved tool.
        tests = json.loads(row['tests_json'] or '[]')
        return dict(request_id=request_id, case_id=case_id, scan_id=scan_id,
                    revision_sha256=actual, outcome=row['outcome'],
                    results=[dict(ordinal=n, verdict=t['verdict'] if t.get('verdict') in
                                  {'pass', 'fail', 'could_not_run'} else 'could_not_run')
                             for n, t in enumerate(tests, 1)])
