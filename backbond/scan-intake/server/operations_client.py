"""BEAAA-42596: hand a finished BAC-bound Starter scan to the Operations workflow.

Starter reports are authored, reviewed, approved and released in customer-operations
(``POST /v1/starter-scans`` -> ``OperationsService.record_starter_result``). Intake only
records the result there, through a durable outbox:

* The payload is built once, at enqueue time, exclusively from the private BAC case
  resolver (``BACIntake.evidence_digest`` + ``report_tool.freeze_starter_input``, i.e.
  ``resolve_evidence``): opaque BAC request/case/scan refs, the evidence sha256 and
  ordinal verdicts. Never customer email, recipients, prompts/replies, endpoints, or
  invented comparison/assessment/retest ids (Operations rejects those fields).
* The stored payload bytes are re-sent unchanged on retry. Operations is idempotent on
  an identical replay (same ``bac_scan_id`` + same content returns the same result_id)
  and refuses a conflicting replay with 409, so a same-payload retry after a timeout or
  5xx is safe, and a 409 is held for an operator instead of retried.
* Nothing happens unless ``QA_OPERATIONS_BASE_URL`` (https only) and
  ``QA_OPERATIONS_TOKEN`` (the Operations integration bearer token, as for ``/v1/events``)
  are both set. There are no defaults.
* Operations, not intake, writes and sends the final Starter report to info@backbond.ai.
  The public Starter page reads that workflow's state back through
  ``GET /v1/starter-scans/{bac_scan_id}/status`` (``starter_status_v1``): the worker polls one
  acknowledged row per tick, validates the body strictly and keeps the last good snapshot.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from bac_intake import BACUnconfirmed
from report_tool import freeze_starter_input

SCHEMA_VERSION = 'starter_scan_result_v1'
SOURCE = 'backbond-scan-intake'
ROUTE = '/v1/starter-scans'
TIMEOUT_SECONDS = 20
STATUS_TIMEOUT_SECONDS = 10
PAYLOAD_KEYS = frozenset({'schema_version', 'bac_request_id', 'bac_case_id', 'bac_scan_id', 'first_scan',
                          'evidence_sha256', 'evidence_refs', 'provenance', 'scope_complete', 'result'})
STATUS_SCHEMA_VERSION = 'starter_status_v1'
STATUS_KEYS = frozenset({'schema_version', 'bac_scan_id', 'result_id', 'report_status', 'delivery_status',
                         'updated_at'})
OPERATIONS_REPORT_STATUSES = ('awaiting_report', 'review', 'changes_requested', 'awaiting_lead_approval', 'approved')
OPERATIONS_DELIVERY_STATUSES = ('not_ready', 'queued', 'sending', 'provider_outcome_unknown', 'provider_rejected',
                                'provider_accepted', 'reconcile_required', 'abandoned')
# Operations vocabulary -> the page's starter_lifecycle@1 vocabulary (report_release.REPORT_STATUSES /
# DELIVERY_STATUSES, rendered by the Starter page). provider_accepted is the mail provider accepting
# the request, never inbox delivery.
PUBLIC_REPORT_STATUS = {'awaiting_report': 'awaiting_results', 'review': 'review',
                        'changes_requested': 'changes_requested',
                        'awaiting_lead_approval': 'awaiting_ceo_approval', 'approved': 'approved'}
PUBLIC_DELIVERY_STATUS = {'not_ready': 'not_ready', 'queued': 'pending', 'sending': 'sending',
                          'provider_outcome_unknown': 'pending', 'provider_accepted': 'provider_accepted',
                          'provider_rejected': 'failed', 'abandoned': 'failed', 'reconcile_required': 'unknown'}
# Operations may supersede an accepted revision. Keep every acknowledged scan
# in the bounded, durable least-recently-polled reconciliation rotation.
PRE_REPORT = {'report_status': 'awaiting_results', 'delivery_status': 'not_ready', 'updated_at': None}
_SCAN_REF = re.compile(r'[A-Za-z0-9_-]{1,128}')
# Statuses where re-sending the identical payload cannot succeed: hold for an operator.
HOLD_STATUSES = frozenset({400, 409, 413, 415, 422})
_SHA256 = re.compile(r'[0-9a-f]{64}')


class OperationsClient:
    """Client for ``{base}/v1/starter-scans`` (POST) and ``.../{bac_scan_id}/status`` (GET);
    https + bearer, no redirects."""

    def __init__(self, base_url: str, token: str, post, get=None):
        self.base_url = (base_url or '').rstrip('/')
        self.token = token or ''
        self.post = post
        self.get = get

    @classmethod
    def from_env(cls, post, get=None) -> 'OperationsClient':
        return cls(os.environ.get('QA_OPERATIONS_BASE_URL', ''), os.environ.get('QA_OPERATIONS_TOKEN', ''), post,
                   get)

    @property
    def configured(self) -> bool:
        parts = urlsplit(self.base_url)
        return bool(parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password
                    and not parts.query and not parts.fragment and self.token)

    def record_starter_result(self, payload: dict):
        if not self.configured:
            raise BACUnconfirmed('operations client not configured')
        return self.post(self.base_url + ROUTE, json=payload,
                         headers={'Authorization': 'Bearer ' + self.token},
                         timeout=TIMEOUT_SECONDS, follow_redirects=False)

    def read_starter_status(self, bac_scan_id: str):
        """``GET {base}/v1/starter-scans/{bac_scan_id}/status``; the caller validates the body."""
        if not self.configured or self.get is None:
            raise BACUnconfirmed('operations client not configured')
        if not isinstance(bac_scan_id, str) or not _SCAN_REF.fullmatch(bac_scan_id):
            raise BACUnconfirmed('invalid bac_scan_id')
        return self.get(self.base_url + ROUTE + '/' + bac_scan_id + '/status',
                        headers={'Authorization': 'Bearer ' + self.token},
                        timeout=STATUS_TIMEOUT_SECONDS, follow_redirects=False)


def _utc_iso(value):
    """A timezone-aware ISO-8601 timestamp normalized to UTC, else None."""
    if not isinstance(value, str) or not 0 < len(value) <= 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def parse_starter_status(body, *, bac_scan_id: str, result_id: str):
    """Strict ``starter_status_v1`` check against the acknowledged outbox row; None if anything is off."""
    if not isinstance(body, dict) or set(body) != STATUS_KEYS:
        return None
    if (body['schema_version'] != STATUS_SCHEMA_VERSION or body['bac_scan_id'] != bac_scan_id
            or body['result_id'] != result_id
            or not isinstance(body['report_status'], str) or body['report_status'] not in OPERATIONS_REPORT_STATUSES
            or not isinstance(body['delivery_status'], str)
            or body['delivery_status'] not in OPERATIONS_DELIVERY_STATUSES):
        return None
    updated_at = _utc_iso(body['updated_at'])
    if updated_at is None:
        return None
    return {'report_status': body['report_status'], 'delivery_status': body['delivery_status'],
            'updated_at': updated_at}


def build_starter_payload(bac, store, *, request_id: str, case_id: str, scan_id: str) -> dict:
    """The exact ``starter_scan_result_v1`` body, from the BAC case resolver only.

    Raises BACUnconfirmed unless the scan is bound to an acknowledged case and finished."""
    digest = bac.evidence_digest(store, request_id=request_id, case_id=case_id, scan_id=scan_id)
    frozen = freeze_starter_input(bac, store, request_id=request_id, case_id=case_id, scan_id=scan_id,
                                  evidence_sha256=digest)
    value = frozen['input']
    if not str(value['pack_version'] or '').startswith('starter-'):
        raise BACUnconfirmed('not a Starter scan')
    return {
        'schema_version': SCHEMA_VERSION,
        'bac_request_id': value['request_id'],
        'bac_case_id': value['case_id'],
        'bac_scan_id': value['scan_id'],
        'first_scan': True,  # a Starter scan is a single first scan: no comparison, no retest
        'evidence_sha256': value['evidence_sha256'],
        'evidence_refs': ['intake:' + value['scan_id']],  # same opaque ref as the BAC evidence event
        'provenance': {'source': SOURCE, 'input_schema': value['schema'], 'input_sha256': frozen['input_sha256'],
                       'pack_version': value['pack_version']},
        # Never inferred from terminal status or pack name; an absent outcome is never 'complete'.
        'scope_complete': value['coverage']['complete'] is True,
        'result': {'outcome': value['outcome'] or 'unconfirmed', 'verdict_counts': value['verdict_counts'],
                   'results': value['results'], 'coverage': value['coverage']},
    }


class OperationsOutbox:
    """Durable outbox: pending -> acknowledged | uncertain (retry) | held (operator)."""

    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.lock = threading.RLock()
        self.db.execute('CREATE TABLE IF NOT EXISTS operations_starter_outbox ('
                        'scan_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, case_id TEXT NOT NULL, '
                        'evidence_sha256 TEXT NOT NULL, payload TEXT NOT NULL, '
                        "state TEXT NOT NULL CHECK (state IN ('pending','acknowledged','uncertain','held')), "
                        'attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0, '
                        'operations_starter_id TEXT, last_status TEXT, '
                        "CHECK ((state='acknowledged') = (operations_starter_id IS NOT NULL)))")
        # Last good Operations status snapshot per acknowledged scan (starter_status_v1, validated).
        self.db.execute('CREATE TABLE IF NOT EXISTS operations_starter_status ('
                        'scan_id TEXT PRIMARY KEY, result_id TEXT NOT NULL, '
                        'report_status TEXT NOT NULL CHECK (report_status IN (%s)), '
                        'delivery_status TEXT NOT NULL CHECK (delivery_status IN (%s)), '
                        'updated_at TEXT NOT NULL, recorded_at REAL NOT NULL)'
                        % (','.join("'%s'" % v for v in OPERATIONS_REPORT_STATUSES),
                           ','.join("'%s'" % v for v in OPERATIONS_DELIVERY_STATUSES)))
        have = {r[1] for r in self.db.execute('PRAGMA table_info(operations_starter_outbox)')}
        if 'status_polled_at' not in have:  # migration-safe for outboxes created before status polling
            self.db.execute('ALTER TABLE operations_starter_outbox ADD COLUMN status_polled_at REAL')

    def row(self, scan_id):
        with self.lock:
            cursor = self.db.execute('SELECT * FROM operations_starter_outbox WHERE scan_id=?', (scan_id,))
            names = [d[0] for d in cursor.description]
            found = cursor.fetchone()
        return dict(zip(names, found)) if found else None

    def enqueue(self, bac, store, *, request_id: str, scan_id: str, client: OperationsClient) -> bool:
        """Record one finished, BAC-bound Starter scan. Idempotent; no-op when unconfigured."""
        if not client.configured:
            return False
        bound = bac.bound_case(scan_id)
        if not bound or bound[0] != request_id:
            return False
        if self.row(scan_id):
            return True
        try:
            payload = build_starter_payload(bac, store, request_id=request_id, case_id=bound[1], scan_id=scan_id)
        except BACUnconfirmed:
            return False
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO operations_starter_outbox "
                            "(scan_id,request_id,case_id,evidence_sha256,payload,state) VALUES (?,?,?,?,?,'pending')",
                            (scan_id, request_id, bound[1], payload['evidence_sha256'],
                             json.dumps(payload, sort_keys=True, separators=(',', ':'))))
        return True

    def flush(self, client: OperationsClient, *, now=None, limit=1):
        """Send at most ``limit`` due rows, one at a time. Claim (attempts+1, backoff) before I/O."""
        if not client.configured:
            return []
        now = time.time() if now is None else now
        done = []
        for _ in range(max(0, min(limit, 100))):
            with self.lock:
                self.db.execute('BEGIN IMMEDIATE')
                try:
                    row = self.db.execute(
                        "SELECT scan_id,request_id,case_id,evidence_sha256,payload,attempts "
                        "FROM operations_starter_outbox WHERE state IN ('pending','uncertain') AND next_at<=? "
                        "ORDER BY next_at,scan_id LIMIT 1", (now,)).fetchone()
                    if row:
                        self.db.execute("UPDATE operations_starter_outbox SET state='uncertain',attempts=attempts+1,"
                                        "next_at=?,last_status='in_flight' WHERE scan_id=?",
                                        (now + min(3600, 10 * 2 ** min(row[5], 9)), row[0]))
                    self.db.execute('COMMIT')
                except Exception:
                    if self.db.in_transaction:
                        self.db.execute('ROLLBACK')
                    raise
            if not row:
                break
            scan_id, request_id, case_id, evidence_sha256, payload, _ = row
            state, starter_id, status = 'uncertain', None, 'transport_error'
            try:
                response = client.record_starter_result(json.loads(payload))
                status = str(response.status_code)
                if 200 <= response.status_code < 300:
                    value = response.json()
                    expected = dict(bac_request_id=request_id, bac_case_id=case_id, bac_scan_id=scan_id,
                                    evidence_sha256=evidence_sha256)
                    if (isinstance(value, dict) and isinstance(value.get('result_id'), str)
                            and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value['result_id'])):
                        if all(value.get(k) == v for k, v in expected.items()):
                            state, starter_id = 'acknowledged', value['result_id']
                        else:
                            state, status = 'held', status + ':binding_mismatch'
                    else:
                        status += ':malformed'
                elif response.status_code in HOLD_STATUSES:
                    state = 'held'
            except BACUnconfirmed:
                status = 'unconfigured'
            except Exception:
                status = 'transport_error'
            with self.lock:
                self.db.execute('UPDATE operations_starter_outbox SET state=?,operations_starter_id=?,last_status=? '
                                'WHERE scan_id=?', (state, starter_id, status, scan_id))
            done.append((scan_id, state))
        return done

    def poll_status(self, client: OperationsClient, *, now=None):
        """Worker step: read the Operations status of at most one acknowledged row, least recently
        polled first, and keep it only if it is a valid ``starter_status_v1`` body for exactly that
        row. 404 (unknown), errors, redirects and invalid bodies keep the last good snapshot."""
        if not client.configured or client.get is None:
            return None
        now = time.time() if now is None else now
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                row = self.db.execute(
                    'SELECT o.scan_id,o.payload,o.operations_starter_id FROM operations_starter_outbox o '
                    "WHERE o.state='acknowledged' AND o.operations_starter_id IS NOT NULL "
                    'ORDER BY COALESCE(o.status_polled_at,0),o.scan_id LIMIT 1').fetchone()
                if row:
                    self.db.execute('UPDATE operations_starter_outbox SET status_polled_at=? WHERE scan_id=?',
                                    (now, row[0]))
                self.db.execute('COMMIT')
            except Exception:
                if self.db.in_transaction:
                    self.db.execute('ROLLBACK')
                raise
        if not row:
            return None
        scan_id, payload, result_id = row
        try:
            bac_scan_id = json.loads(payload)['bac_scan_id']
            response = client.read_starter_status(bac_scan_id)
            if response.status_code != 200:
                return (scan_id, 'unknown' if response.status_code == 404 else 'ignored')
            value = parse_starter_status(response.json(), bac_scan_id=bac_scan_id, result_id=result_id)
        except Exception:
            return (scan_id, 'unreachable')
        if value is None:
            return (scan_id, 'invalid')
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO operations_starter_status '
                            '(scan_id,result_id,report_status,delivery_status,updated_at,recorded_at) '
                            'VALUES (?,?,?,?,?,?)', (scan_id, result_id, value['report_status'],
                                                     value['delivery_status'], value['updated_at'], now))
        return (scan_id, 'recorded')

    def public_lifecycle(self, scan_id: str):
        """starter_lifecycle@1 report/delivery fields for a scan the Operations workflow owns.

        None unless the outbox row is ``acknowledged`` (the caller then keeps its own source).
        Acknowledged without a valid snapshot yet -> the pre-report state, never an invented one."""
        with self.lock:
            row = self.db.execute(
                'SELECT o.operations_starter_id,s.result_id,s.report_status,s.delivery_status,s.updated_at '
                'FROM operations_starter_outbox o LEFT JOIN operations_starter_status s ON s.scan_id=o.scan_id '
                "WHERE o.scan_id=? AND o.state='acknowledged'", (scan_id,)).fetchone()
        if not row:
            return None
        starter_id, result_id, report, delivery, updated_at = row
        if result_id is None or result_id != starter_id:
            return dict(PRE_REPORT)
        return {'report_status': PUBLIC_REPORT_STATUS[report], 'delivery_status': PUBLIC_DELIVERY_STATUS[delivery],
                'updated_at': updated_at}
