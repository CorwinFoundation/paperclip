"""Local Findings recovery. No BAC client, report release, or delivery authority.

Evidence archives and Writer consumption receipts remain governed by BEAAA-42945;
local terminal rows alone do not assert archive completeness or a Writer handoff.
"""
import json
from datetime import datetime, timezone
import sqlite3
import threading
import time
from bac_intake import safe_progress
from qa_service import translate_callback, report_failure


class FindingsIntake:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS findings_progress '
                        '(scan_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, next_at REAL NOT NULL)')

        self.db.execute('CREATE TABLE IF NOT EXISTS findings_confirmed '
                        '(scan_id TEXT PRIMARY KEY, processed INTEGER NOT NULL, observed_at TEXT NOT NULL)')

        self.db.execute('CREATE TABLE IF NOT EXISTS findings_scope '
                        '(scan_id TEXT PRIMARY KEY, pack_version TEXT NOT NULL)')

    def accept(self, scan_id, pack_version):
        with self.lock:
            self.db.execute('INSERT OR IGNORE INTO findings_scope VALUES (?,?)', (scan_id, pack_version))

    def accepted_scope(self, scan_id):
        with self.lock:
            row = self.db.execute('SELECT pack_version FROM findings_scope WHERE scan_id=?', (scan_id,)).fetchone()
        return row[0] if row else None

    def confirmed_progress_at(self, scan_id):
        with self.lock:
            row = self.db.execute('SELECT observed_at FROM findings_confirmed WHERE scan_id=?', (scan_id,)).fetchone()
        return row[0] if row else None

    def latest_progress(self, scan_id):
        with self.lock:
            row = self.db.execute('SELECT snapshot FROM findings_progress WHERE scan_id=?',
                                  (scan_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def capture(self, store, queue, fetch_terminal, fetch_progress, *, now=None, limit=1):
        now = time.time() if now is None else now
        # Only active correlated scans. Rotation prevents a stalled scan starving others.
        with store._lock:
            candidates = list(store.db.execute("SELECT * FROM scans WHERE status IN "
                "('running','dispatch_unknown') AND scanner_id IS NOT NULL OR "
                "(status='finished' AND scanner_id IS NOT NULL AND incomplete_reason LIKE 'report_preparation_error:%')"))
        # Store fixtures and migrations can use a separate database connection.
        # Only explicitly accepted Findings requests belong to this worker.
        with self.lock:
            due = dict(self.db.execute('SELECT scan_id,next_at FROM findings_progress'))
        rows = sorted((row for row in candidates if self.accepted_scope(row['id'])
                       and due.get(row['id'], 0) <= now),
                      key=lambda row: (due.get(row['id'], 0), row['id']))[:max(0, min(limit, 100))]
        for row in rows:
            scan_id = row['id']
            progress = self.latest_progress(scan_id)
            try:
                observed = safe_progress(fetch_progress(scan_id))
                if observed:
                    progress = {**progress, **observed}
            except Exception:
                pass  # Keep the last observed denominator; never infer from pack name.
            with self.lock:
                processed = progress.get('processed_count')
                if type(processed) is int and processed > 0:
                    self.db.execute('INSERT INTO findings_confirmed VALUES (?,?,?) ON CONFLICT(scan_id) '
                                    'DO UPDATE SET processed=excluded.processed, observed_at=excluded.observed_at '
                                    'WHERE excluded.processed > findings_confirmed.processed',
                                    (scan_id, processed, datetime.fromtimestamp(now, timezone.utc).isoformat()))
                self.db.execute('INSERT OR REPLACE INTO findings_progress VALUES (?,?,?)',
                                (scan_id, json.dumps(progress), now + 30))
            try:
                value = fetch_terminal(dict(row))
                if (isinstance(value, dict) and value.get('scan_id') == row['scanner_id']
                        and value.get('status') in {'complete', 'completed', 'failed', 'incomplete', 'partial'}
                        and isinstance(value.get('result_rows', []), list)
                        and len(value.get('result_rows', [])) <= 100
                        and all(isinstance(r, dict) for r in value.get('result_rows', []))
                        and (value.get('result_rows') or (value['status'] == 'failed'
                             and value.get('incomplete') is True and value.get('incomplete_reason')))):
                    value = dict(value)
                    if value.get('partial_completion'):
                        value['incomplete'] = True
                    if report_failure(row):
                        store.refresh_report_failure(scan_id, row['scanner_id'], value)
                    else:
                        queue.callback(row['scanner_id'], translate_callback(value))
            except Exception:
                pass  # No redispatch on unknown, foreign, or malformed terminal data.
