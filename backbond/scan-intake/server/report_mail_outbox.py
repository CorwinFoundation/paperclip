"""Durable BAC delivery claims over the existing intake mail transport.

Provider acceptance is not inbox delivery. Unknown sends use the same immutable
payload and provider key for less than 23 hours, then require reconciliation.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import date
import hashlib
import json
import re
import sqlite3
import time

from report_mail_transport import send_mail


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def validate(payload, founders):
    """Validate independently of the CLI; recipient policy is server owned."""
    fields = {'schema_version', 'mode', 'target_name', 'run_date', 'note',
              'recipients', 'approval_ref', 'pdf_sha256', 'html_sha256',
              'pdf_base64', 'html_base64'}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError('invalid_report_payload')
    if payload['schema_version'] != 'report_mail@1':
        raise ValueError('invalid_report_schema')
    for name, limit in [('target_name', 200), ('note', 2000), ('run_date', 10)]:
        value = payload[name]
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError('invalid_report_metadata')
    if any(c in payload['target_name'] for c in '\r\n'):
        raise ValueError('invalid_report_target')
    date.fromisoformat(payload['run_date'])
    recipients = payload['recipients']
    if not isinstance(recipients, list) or not isinstance(founders, list) or not founders:
        raise ValueError('invalid_report_recipients')
    addresses = recipients + founders
    if len(addresses) > 50 or any(not isinstance(a, str) or len(a) > 254 or
            not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', a) for a in addresses):
        raise ValueError('invalid_report_recipients')
    if payload['mode'] == 'founders':
        if recipients or payload['approval_ref'] is not None:
            raise ValueError('founders_recipients_are_configured')
        to, cc = sorted(set(founders)), []
    elif payload['mode'] == 'customer':
        if not recipients or not isinstance(payload['approval_ref'], str) or not re.fullmatch(
                r'BAC-[1-9][0-9]*', payload['approval_ref']):
            raise ValueError('customer_approval_required')
        to = sorted(set(recipients))
        cc = sorted(set(founders) - set(to))
    else:
        raise ValueError('invalid_report_mode')
    decoded = {}
    for kind, limit in [('pdf', 20 * 1024 * 1024), ('html', 5 * 1024 * 1024)]:
        encoded = payload[kind + '_base64']
        if not isinstance(encoded, str) or len(encoded) > 4 * ((limit + 2) // 3):
            raise ValueError('report_size_limit')
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > limit or hashlib.sha256(raw).hexdigest() != payload[kind + '_sha256']:
            raise ValueError('report_hash_mismatch')
        decoded[kind] = raw
    if not decoded['pdf'].startswith(b'%PDF-'):
        raise ValueError('invalid_report_pdf')
    decoded['html'].decode('utf-8')
    # HTML is retained as opaque bytes. It must never enter an unsandboxed viewer.
    return to, cc, decoded


class ReportMailOutbox:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS bac_report_mail(
                request_id TEXT PRIMARY KEY, binding TEXT NOT NULL,
                actor TEXT NOT NULL, created_at REAL NOT NULL, first_send REAL,
                lease_until REAL NOT NULL DEFAULT 0, state TEXT NOT NULL,
                message_id TEXT, attempts INTEGER NOT NULL DEFAULT 0)''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(self, payload, *, actor, founders, links, now=None):
        to, cc, _ = validate(payload, founders)
        if not isinstance(actor, str) or not actor:
            raise ValueError('missing_report_actor')
        if set(links) != set(to + cc) or any(not isinstance(v, str) or not v.startswith(
                'https://') or any(c in v for c in '\r\n') for v in links.values()):
            raise ValueError('recipient_links_required')
        # The request identity comes from caller content and actor. The first
        # accepted server config/links stay frozen across later config changes.
        binding = canonical(dict(payload=payload, actor=actor, to=to, cc=cc, links=links))
        ident = hashlib.sha256(canonical(dict(payload=payload, actor=actor)).encode()).hexdigest()
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO bac_report_mail(request_id,binding,actor,created_at,state) '
                       "VALUES(?,?,?,?,'pending')", (ident, binding, actor, time.time() if now is None else now))
        return ident

    def deliver(self, ident, config, *, now=None, transport=send_mail):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM bac_report_mail WHERE request_id=?', (ident,)).fetchone()
            if row is None:
                raise ValueError('unknown_report_mail')
            if row['state'] in {'accepted', 'held'} or row['lease_until'] > now:
                return self.receipt(row)
            if row['first_send'] is not None and now - row['first_send'] >= 23 * 3600:
                db.execute("UPDATE bac_report_mail SET state='held' WHERE request_id=?", (ident,))
                return dict(request_id=ident, state='held')
            db.execute("UPDATE bac_report_mail SET state='sending',first_send=COALESCE(first_send,?),"
                       'lease_until=?,attempts=attempts+1 WHERE request_id=?', (now, now + 120, ident))
        value = json.loads(row['binding'])
        payload = value['payload']
        # All values affecting the provider body are frozen in the durable binding.
        text = payload['note'] + '\n\n' + '\n'.join(
            sorted(set(value['links'].values())))
        try:
            message = transport(config, recipients=value['to'], cc=value['cc'],
                subject='BackBond report: ' + payload['target_name'] + ' — ' + payload['run_date'],
                text=text, pdf=base64.b64decode(payload['pdf_base64'], validate=True),
                idempotency_key=ident)
        except Exception:
            # No raw provider error or credential enters the audit record.
            with self.connect() as db:
                db.execute("UPDATE bac_report_mail SET state='unknown',lease_until=0 WHERE request_id=?", (ident,))
            return dict(request_id=ident, state='unknown')
        with self.connect() as db:
            db.execute("UPDATE bac_report_mail SET state='accepted',message_id=?,lease_until=0 WHERE request_id=?",
                       (message, ident))
        return dict(request_id=ident, state='accepted', message_id=message)

    @staticmethod
    def receipt(row):
        result = dict(request_id=row['request_id'], state=row['state'])
        if row['message_id']:
            result['message_id'] = row['message_id']
        return result
