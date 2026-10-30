#!/usr/bin/env python3
"""Read-only, bounded intake delivery diagnostics. Never emit recipients or links."""
import argparse
import json
import re
import sqlite3
from pathlib import Path


def collect(path):
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result = {}
        for table, fields in {
            'scans': ['id', 'status', 'outcome', 'scanner_id'],
            'mail_outbox': ['scan_id', 'status'],
            'findings_report_outbox': ['scan_id', 'state', 'error'],
            'findings_capture_errors': ['scan_id', 'error'],
            'native_report_notifications': ['state', 'error'],
        }.items():
            if table not in tables:
                result[table] = {'missing': True}
                continue
            columns = {r[1] for r in db.execute('PRAGMA table_info(' + table + ')')}
            selected = [f for f in fields if f in columns]
            rows = db.execute('SELECT ' + ','.join(selected) + ' FROM ' + table + ' ORDER BY rowid DESC LIMIT 10')
            result[table] = [{k: (v if v is None or
                (k in {'id', 'scan_id', 'scanner_id'} and re.fullmatch(r'[a-f0-9-]{36}', str(v))) or
                (k != 'error' and k not in {'id', 'scan_id', 'scanner_id'} and str(v) in {
                    'pending','working','held','failed','unknown','accepted','running','finished',
                    'complete','queued','reserved','dispatch_unknown','sent','sending','retry'})
                else '<redacted>') for k, v in dict(row).items()} for row in rows]
        if {'scans', 'mail_outbox'} <= tables:
            # Include every completion during the expired-grant interval, plus
            # any older pending retries that could have attempted in it.
            rows = db.execute("""SELECT s.id, s.status, m.status AS mail_status,
                m.attempts FROM scans s LEFT JOIN mail_outbox m ON m.scan_id=s.id
                WHERE (julianday(s.finished_at) BETWEEN julianday(?) AND julianday(?))
                   OR (m.status='pending' AND julianday(s.created_at)<=julianday(?))
                ORDER BY s.created_at LIMIT 1001""",
                ('2026-10-29T20:22:00Z', '2026-10-30T16:12:00Z',
                 '2026-10-30T16:12:00Z')).fetchall()
            result['outage_window'] = {
                'truncated': len(rows) > 1000,
                'runs': [{'id': r['id'] if re.fullmatch(r'[a-f0-9-]{36}', str(r['id'])) else '<redacted>',
                          'status': r['status'] if r['status'] in {'finished','running','failed'} else '<redacted>',
                          'mail_status': r['mail_status'] if r['mail_status'] in {'sent','pending',None} else '<redacted>',
                          'attempts': r['attempts'] if isinstance(r['attempts'], int) else None}
                         for r in rows[:1000]]}
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path)
    parser.add_argument('--env', type=Path, default=Path('/etc/backbond/scan-intake.env'))
    args = parser.parse_args()
    path = args.db
    if path is None:
        settings = dict(line.split('=', 1) for line in args.env.read_text().splitlines()
                        if line.startswith('QA_DB_PATH='))
        path = Path(settings.get('QA_DB_PATH', '/var/lib/backbond-qa/qa.sqlite').strip('\"\''))
    print(json.dumps(collect(path), indent=2))
