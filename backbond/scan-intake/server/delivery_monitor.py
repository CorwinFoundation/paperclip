"""Delivery observations and durable alert retries; never advance review or send mail.

Alert adapters must honor the supplied stable idempotency key, including after an
ambiguous transport failure. No raw provider errors or report content is stored.
"""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
import re
import sqlite3

RECIPIENT = 'info@backbond.ai'
DEADLINE = 4 * 3600


def epoch(value):
    instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if instant.utcoffset() is None:
        raise ValueError('timezone_required')
    return instant.timestamp()


def delivery_observation(scan, outbox=None, *, credential_error=False):
    """Use actual scan completion even if capture never created an outbox row.

    The caller supplies confirmed credential diagnostics; a generic transport
    failure cannot establish that a credential is invalid.
    """
    if scan['status'] != 'finished' or scan['outcome'] != 'complete':
        return None
    row = dict(outbox or {})
    remote = json.loads(row.get('remote_status') or '{}')
    message = row.get('message_id')
    accepted = (isinstance(message, str) and
                re.fullmatch(r'[A-Za-z0-9_-]{1,128}', message) is not None and
                row.get('state') in {'accepted', 'sent'})
    if accepted:
        stage = 'workflow_write_back' if row['state'] != 'sent' else 'sent'
    elif credential_error or row.get('error') == 'mail_not_configured':
        stage = 'credential'
    elif not row:
        stage = 'workflow_write_back'
    elif remote.get('state') in {'pending', 'writing', 'reviewing', 'needs_attention'}:
        stage = 'review'
    elif remote.get('state') in {'approved', 'sent'} or row.get('send_started') is not None:
        stage = 'provider'
    else:
        stage = 'workflow_write_back'
    return dict(scan_id=scan['id'], completed_at=epoch(scan['finished_at']),
                stage=stage, delivered=accepted and row['state'] == 'sent',
                provider_accepted=accepted, review_hold=remote.get('state') == 'needs_attention')


def delivery_alert(observation, now):
    if observation is None or observation['delivered']:
        return None
    if now - observation['completed_at'] < DEADLINE:
        return None
    return dict(key='delivery:' + observation['scan_id'], kind='delivery_breach',
                priority='critical', scan_id=observation['scan_id'],
                stage=observation['stage'], recipient=RECIPIENT,
                completed_at=observation['completed_at'])


def placement_alert(observation, now, mailbox=None, *, message_id=None):
    """Provider acceptance alone leaves recipient placement unverified.

    Mailbox evidence must correlate to this provider message and scan, carry an
    explicit INBOX label, and be observed after scan completion. No raw headers
    or message content enter the alert payload. Observer integration is separate.
    """
    if not observation or not observation['provider_accepted']:
        return None
    if now - observation['completed_at'] < DEADLINE:
        return None
    evidence = mailbox or {}
    labels = evidence.get('labels')
    observed = evidence.get('observed_at')
    verified = (bool(message_id) and evidence.get('message_id') == message_id
                and evidence.get('scan_id') == observation['scan_id']
                and evidence.get('recipient') == RECIPIENT
                and isinstance(labels, list) and 'INBOX' in labels
                and 'SPAM' not in labels and 'SENT' not in labels
                and isinstance(observed, (int, float)) and not isinstance(observed, bool)
                and observation['completed_at'] <= observed <= now)
    if verified:
        return None
    return dict(key='placement:' + observation['scan_id'], kind='placement_failure',
                priority='critical', scan_id=observation['scan_id'],
                stage='mailbox', recipient=RECIPIENT,
                completed_at=observation['completed_at'])


def review_alert(observation):
    if not observation or not observation['review_hold'] or observation['delivered']:
        return None
    return dict(key='review:' + observation['scan_id'], kind='review_decision',
                priority='high', scan_id=observation['scan_id'], stage='review',
                options=['Request report corrections', 'Provide missing evidence',
                         'Keep delivery on hold'])


def credential_alert(key_id, expires_at, renewal_command, now):
    """Metadata only. Renewal command must be verified by the integration owner."""
    if not math.isfinite(expires_at) or not renewal_command.strip():
        raise ValueError('verified_credential_metadata_required')
    remaining = expires_at - now
    threshold = 'T-1' if remaining <= 86400 else 'T-7' if remaining <= 7 * 86400 else None
    if threshold is None:
        return None
    return dict(key=f'credential:{key_id}:{expires_at}:{threshold}',
                kind='credential_expiry', priority='high', key_id=key_id,
                expires_at=expires_at, threshold=threshold, renewal_command=renewal_command)


class AlertOutbox:
    """One durable incident per key, with independent ticket and push receipts.

    SQLite serializes concurrent workers. Adapters have bounded timeouts and
    must deduplicate by key: a crash can occur after remote acceptance but before
    the local receipt commits. Keep this database separate from scan storage.
    """
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS delivery_alerts (
                key TEXT PRIMARY KEY, payload TEXT NOT NULL,
                ticket TEXT, push TEXT, next_at REAL NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0)''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, alert):
        if alert is None:
            return
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO delivery_alerts(key,payload) VALUES (?,?)',
                       (alert['key'], json.dumps(alert, sort_keys=True, allow_nan=False)))

    def flush(self, ticket, push, *, now, limit=10):
        # Commit each output separately so failed push never recreates a ticket.
        with self.connect() as db:
            keys = [r[0] for r in db.execute('SELECT key FROM delivery_alerts '
                    'WHERE (ticket IS NULL OR push IS NULL) AND next_at<=? '
                    'ORDER BY next_at,key LIMIT ?', (now, limit))]
        for key in keys:
            for channel, adapter in [('ticket', ticket), ('push', push)]:
                with self.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row = db.execute('SELECT * FROM delivery_alerts WHERE key=?', (key,)).fetchone()
                    if row[channel] is not None or row['next_at'] > now:
                        continue
                    stable_key = hashlib.sha256((key + ':' + channel).encode()).hexdigest()
                    try:
                        receipt = adapter(json.loads(row['payload']), idempotency_key=stable_key)
                        if not isinstance(receipt, str) or not receipt.strip():
                            raise ValueError('acceptance_receipt_required')
                    except Exception:
                        db.execute('UPDATE delivery_alerts SET next_at=?,attempts=attempts+1 WHERE key=?',
                                   (now + 60, key))
                    else:
                        db.execute(f'UPDATE delivery_alerts SET {channel}=? WHERE key=?', (receipt, key))


def collect_alerts(store, reports, alerts, *, now, since):
    """Observe completed scans including capture gaps, without changing either DB.

    An explicit completion cutoff prevents accidental historical alert backfill.
    Runtime wiring owns scheduling and separately calls AlertOutbox.flush.
    """
    if since is None:
        raise ValueError('explicit_monitor_cutoff_required')
    cutoff = epoch(since)
    with store._lock:
        scans = [dict(row) for row in store.db.execute(
            "SELECT id,status,outcome,finished_at FROM scans "
            "WHERE status='finished' AND outcome='complete'")]
    count = 0
    for scan in scans:
        if epoch(scan['finished_at']) < cutoff:
            continue
        with reports.lock:
            row = reports.db.execute('SELECT state,remote_status,error,message_id,send_started '
                                     'FROM findings_report_outbox WHERE scan_id=?', (scan['id'],)).fetchone()
        value = delivery_observation(scan, row)
        alerts.enqueue(delivery_alert(value, now))
        # Until a mailbox observer is connected, accepted mail remains unverified.
        alerts.enqueue(placement_alert(value, now, message_id=dict(row or {}).get('message_id')))
        alerts.enqueue(review_alert(value))
        count += 1
    return count


def monitor_tick(store, reports, *, http, now):
    """Opt-in worker integration with explicit cutoff and separate durable DB."""
    import os
    from alert_adapters import FounderPushAdapter, PaperclipIncidentAdapter
    if os.environ.get('QA_DELIVERY_MONITOR') != '1':
        return 0
    since = os.environ.get('QA_DELIVERY_MONITOR_SINCE', '')
    path = os.environ.get('QA_DELIVERY_ALERT_DB_PATH', '')
    if not since or not path:
        raise ValueError('explicit_monitor_cutoff_and_database_required')
    epoch(since)
    alerts = AlertOutbox(path)
    count = collect_alerts(store, reports, alerts, now=now, since=since)
    alerts.flush(PaperclipIncidentAdapter.from_env(post=http.post, get=http.get),
                 FounderPushAdapter.from_env(post=http.post), now=now)
    return count
