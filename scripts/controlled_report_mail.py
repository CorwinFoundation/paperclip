"""Operator-only Findings attachment delivery; never import into request handlers.

Trust boundary: service-owned config and reviewer acceptance manifest, not intake
input. This adapter consumes acceptance, it does not confer report approval.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import urllib.request
import urllib.error

RECIPIENT = 'info@backbond.ai'
SENDER = 'BackBond <reports@backbond.ai>'
REPLY_TO = 'info@backbond.ai'
DEFAULT_CONFIG = Path('/etc/backbond-scan-intake/controlled-report-mail.json')
DEFAULT_OUTBOX = Path('/var/lib/backbond-scan-intake/controlled-report-mail/mail.sqlite')


def installed_path(value, expected):
    if Path(value).resolve() != expected.resolve():
        raise ValueError('Only the installed service-owned path is allowed: ' + str(expected))
    return expected


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prepare(manifest_path, config_path=DEFAULT_CONFIG):
    config = json.loads(Path(config_path).read_text())
    if config.get('recipient', RECIPIENT) != RECIPIENT or config.get('allowed_recipients', [RECIPIENT]) != [RECIPIENT]:
        raise ValueError('Internal recipient configuration must match fixed allowlist')
    manifest_path = Path(manifest_path)
    m = json.loads(manifest_path.read_text())
    for field in ('run_id', 'report_id', 'revision', 'reviewer_acceptance_ref', 'qa_acceptance_ref'):
        if not isinstance(m.get(field), str) or not m[field].strip():
            raise ValueError('Missing acceptance identity: ' + field)
    if m.get('reviewer_decision') != 'SHIP' or m.get('qa_decision') != 'PASS':
        raise ValueError('Exact revision requires reviewer SHIP and QA PASS')
    if m.get('recipient', RECIPIENT) != RECIPIENT:
        raise ValueError('Recipient override forbidden')
    for field in ('run_id', 'report_id', 'revision'):
        if not config.get(field) or config[field] != m[field]:
            raise ValueError('Service configuration must bind the new controlled ' + field)
    attachments = []
    hashes = {}
    for kind in ('pdf', 'html'):
        item = m.get(kind)
        if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not isinstance(item.get('sha256'), str):
            raise ValueError('Missing accepted artifact: ' + kind)
        path = (manifest_path.parent / item['path']).resolve()
        data = path.read_bytes()
        if not data or digest(data) != item['sha256']:
            raise ValueError('Accepted ' + kind + ' bytes do not match')
        hashes[kind] = digest(data)
        attachments.append({'filename': 'findings.' + kind,
                            'content': base64.b64encode(data).decode('ascii')})
    binding = {k: m[k] for k in ('run_id', 'report_id', 'revision',
                'reviewer_acceptance_ref', 'qa_acceptance_ref')}
    binding.update(recipient=RECIPIENT, hashes=hashes)
    encoded = json.dumps(binding, sort_keys=True, separators=(',', ':'))
    return binding, encoded, {'from': SENDER, 'reply_to': REPLY_TO, 'to': [RECIPIENT],
        'subject': 'BackBond Findings Report - ' + digest(encoded.encode()),
        'text': 'Reviewed Findings Report PDF and HTML attached.', 'attachments': attachments}


class Outbox:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS report_mail (
            slot INTEGER PRIMARY KEY CHECK(slot=1), binding TEXT NOT NULL,
            idempotency_key TEXT NOT NULL, state TEXT NOT NULL,
            provider_id TEXT, provider_evidence TEXT, inbox_evidence TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')

    def receipt(self):
        row = self.db.execute('SELECT * FROM report_mail WHERE slot=1').fetchone()
        return dict(row) if row else None

    def send(self, manifest_path, transport, config_path=DEFAULT_CONFIG):
        binding, encoded, payload = prepare(manifest_path, config_path)
        key = 'controlled-findings-' + digest(encoded.encode())
        # Reserve durably BEFORE network. Any crash after this point is unknown;
        # pending is never automatically retried, even after process restart.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old = self.receipt()
            if old:
                if old['binding'] != encoded:
                    raise ValueError('Controlled outbox already bound to another revision/run')
                self.db.execute('COMMIT')
                return old
            self.db.execute("INSERT INTO report_mail(slot,binding,idempotency_key,state) VALUES(1,?,?, 'pending')", (encoded, key))
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise
        try:
            status, response = transport(payload, key)
            provider_id = response.get('id') if isinstance(response, dict) else None
            if status in (200, 201) and isinstance(provider_id, str) and provider_id:
                state = 'provider_accepted'
            elif status in (400, 401, 403, 404, 413, 422):
                state, provider_id = 'failed', None
            else:
                state, provider_id = 'unknown', None
        except Exception:
            state, provider_id, status = 'unknown', None, None
        # Never persist credentials, response bodies, or exception text.
        self.db.execute('UPDATE report_mail SET state=?,provider_id=?,provider_evidence=?,updated_at=CURRENT_TIMESTAMP WHERE slot=1',
                        (state, provider_id, json.dumps({'http_status': status})))
        return self.receipt()

    def reconcile(self, state, evidence, provider_id=None):
        if not evidence.strip():
            raise ValueError('Durable evidence reference required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old = self.receipt()
            if not old:
                raise ValueError('No reserved delivery')
            if state == 'inbox_received':
                if old['inbox_evidence'] is not None:
                    raise ValueError('Inbox receipt already recorded')
                if old['state'] != 'provider_accepted':
                    raise ValueError('Reconcile provider acceptance before inbox receipt')
                self.db.execute('UPDATE report_mail SET inbox_evidence=?,updated_at=CURRENT_TIMESTAMP WHERE slot=1', (evidence,))
            elif state in ('provider_accepted', 'failed') and old['state'] in ('pending', 'unknown'):
                if state == 'provider_accepted' and not provider_id:
                    raise ValueError('Provider message ID required')
                self.db.execute('UPDATE report_mail SET state=?,provider_id=?,provider_evidence=?,updated_at=CURRENT_TIMESTAMP WHERE slot=1', (state, provider_id, evidence))
            else:
                raise ValueError('Invalid reconciliation transition; no retry/reset path')
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        return self.receipt()


def resend_transport():
    path = os.environ.get('SCAN_MAIL_CREDENTIALS_PATH')
    if not path and os.environ.get('CREDENTIALS_DIRECTORY'):
        path = str(Path(os.environ['CREDENTIALS_DIRECTORY']) / 'scan-mail.json')
    if not path:
        raise ValueError('Existing service mail credential configuration required')
    value = json.loads(Path(path).read_text())
    if value.get('provider') != 'resend' or not value.get('api_key'):
        raise ValueError('Existing Resend configuration required')

    def send(payload, key):
        req = urllib.request.Request('https://api.resend.com/emails',
            data=json.dumps(payload).encode(), method='POST', headers={
                'Authorization': 'Bearer ' + value['api_key'],
                'Content-Type': 'application/json', 'Idempotency-Key': key})
        # No transport retry; redirects are forbidden to protect sender credentials.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, {}
    return send


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--outbox', default=str(DEFAULT_OUTBOX), help='Must equal the installed service-owned SQLite path')
    sub = p.add_subparsers(dest='action', required=True)
    s = sub.add_parser('send')
    s.add_argument('--acceptance', required=True, help='Service-owned exact revision acceptance manifest')
    s.add_argument('--config', default=str(DEFAULT_CONFIG))
    sub.add_parser('status')
    r = sub.add_parser('reconcile')
    r.add_argument('--state', required=True, choices=['provider_accepted', 'failed', 'inbox_received'])
    r.add_argument('--evidence', required=True)
    r.add_argument('--provider-id')
    args = p.parse_args(argv)
    outbox = installed_path(args.outbox, DEFAULT_OUTBOX)
    if args.action == 'send':
        installed_path(args.config, DEFAULT_CONFIG)
    box = Outbox(outbox)
    if args.action == 'send':
        result = box.send(args.acceptance, resend_transport(), args.config)
    elif args.action == 'reconcile':
        result = box.reconcile(args.state, args.evidence, args.provider_id)
    else:
        result = box.receipt()
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
