"""Disabled-by-default Scanner-local permit transport. No administrative HTTP API."""
import hmac
import importlib.util
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time

_SOURCE = Path(__file__).resolve().parents[1] / 'backbond/scan-intake/server/controlled_admission.py'
if 'controlled_admission' in sys.modules:
    _primitives = sys.modules['controlled_admission']
else:
    _spec = importlib.util.spec_from_file_location('controlled_admission', _SOURCE)
    _primitives = importlib.util.module_from_spec(_spec)
    sys.modules['controlled_admission'] = _primitives
    _spec.loader.exec_module(_primitives)
AdmissionClosed = _primitives.AdmissionClosed

METADATA_SQL = """CREATE TABLE controlled_permit_context (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 permit_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
 expires_at INTEGER NOT NULL, token_sha256 TEXT NOT NULL)"""


def fingerprint(payload):
    return _primitives.OperatorAdmission.fingerprint(payload)


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', value):
        raise AdmissionClosed('Invalid permit identifier')
    return value


def state_path():
    value = os.environ.get('ARE_CONTROLLED_PERMIT_DB')
    if not value or not Path(value).is_absolute():
        raise AdmissionClosed('Controlled permit storage unconfigured')
    return Path(value)


def _existing(path):
    # The parent directory must be private to the service; never initialize from HTTP.
    path = Path(path)
    info, parent = path.lstat(), path.parent.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode)
            or info.st_mode & 0o077 or parent.st_mode & 0o077
            or info.st_uid != os.geteuid() or parent.st_uid != os.geteuid()):
        raise AdmissionClosed('Unsafe permit storage')
    return sqlite3.connect(path.as_uri() + '?mode=rw', uri=True,
                           isolation_level=None, timeout=10)


class _ExistingLedger(_primitives.ControlledAdmission):
    def __init__(self, path):
        self.db = _existing(path)
        self.db.execute('PRAGMA synchronous=FULL')
        required = {'controlled_admission', 'controlled_scan_binding',
                    'controlled_paid_calls', 'controlled_tests', 'controlled_permit_context',
                    'controlled_preflight'}
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required <= tables:
            self.db.close()
            raise AdmissionClosed('Incomplete permit schema')

    def assert_open(self):
        row = self.db.execute('SELECT expires_at FROM controlled_permit_context WHERE singleton=1').fetchone()
        if not row or type(row[0]) is not int or time.time() >= row[0]:
            raise AdmissionClosed('Controlled permit expired')

    def preflight_call(self, **kwargs):
        self.assert_open()
        return super().preflight_call(**kwargs)

    def reserve_call(self, **kwargs):
        self.assert_open()
        return super().reserve_call(**kwargs)


def _load(opaque_id, tenant_id):
    try:
        _identifier(opaque_id)
        if not isinstance(tenant_id, str) or not tenant_id:
            raise AdmissionClosed('Authenticated tenant required')
        ledger = _ExistingLedger(state_path())
        try:
            row = ledger.db.execute(
                'SELECT permit_id,tenant_id,token_sha256 FROM controlled_permit_context WHERE singleton=1'
            ).fetchone()
            binding = ledger.db.execute(
                'SELECT actor,request_id,target,enabled FROM controlled_admission WHERE singleton=1'
            ).fetchone()
            ledger.assert_open()
            if (not row or not binding or binding[3] != 1
                    or not hmac.compare_digest(row[0], opaque_id)
                    or row[1] != tenant_id):
                raise AdmissionClosed('Controlled permit unavailable')
            admission = _primitives.OperatorAdmission(
                ledger, actor=binding[0], request_id=binding[1], target=binding[2],
                token_sha256=row[2])
            return admission
        except BaseException:
            ledger.db.close()
            raise
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        raise AdmissionClosed('Controlled permit unavailable') from exc


def validate_permit_context(opaque_id, *, tenant_id, payload):
    admission = _load(opaque_id, tenant_id)
    try:
        if not hmac.compare_digest(fingerprint(payload), admission.binding['target']):
            raise AdmissionClosed('Controlled execution mismatch')
        return {'permit_id': opaque_id, 'tenant_id': tenant_id,
                'target_sha256': admission.binding['target']}
    finally:
        admission.ledger.db.close()


def execute_preflight(opaque_id, *, tenant_id, payload, upper_microusd, operation):
    """Server-owned bounded operation; never pass caller prices or runner callbacks.

    Validate the complete execution payload before reserving the sole preflight.
    Closing the connection after any outcome preserves unknown/consumed state.
    The HTTP route remains closed until its single-attempt transport is wired.
    """
    admission = _load(opaque_id, tenant_id)
    try:
        if not hmac.compare_digest(fingerprint(payload), admission.binding['target']):
            raise AdmissionClosed('Controlled execution mismatch')
        return admission.ledger.preflight_call(
            **admission.binding, upper_microusd=upper_microusd, operation=operation)
    finally:
        admission.ledger.db.close()


def bind_scanner_permit(context, *, scan_id):
    """Commit sole Scanner admission before kickoff; no retry after any ambiguity."""
    admission = _load(context['permit_id'], context['tenant_id'])
    try:
        if context.get('target_sha256') != admission.binding['target']:
            raise AdmissionClosed('Controlled execution mismatch')
        admission.ledger.claim(**admission.binding)
        admission.bind_scan(scan_id)
        admission.ledger.begin_dispatch(**admission.binding)
    finally:
        admission.ledger.db.close()


def load_scanner_permit(opaque_id, *, tenant_id, scan_id, payload):
    context = validate_permit_context(opaque_id, tenant_id=tenant_id, payload=payload)
    return load_bound_scanner_permit(context, scan_id=scan_id)


def load_bound_scanner_permit(context, *, scan_id):
    """Only consume a descriptor produced by API validation, never raw endpoint config."""
    admission = _load(context['permit_id'], context['tenant_id'])
    try:
        row = admission.ledger.db.execute(
            'SELECT scan_id FROM controlled_scan_binding WHERE singleton=1').fetchone()
        if (context.get('target_sha256') != admission.binding['target']
                or not row or row[0] != scan_id):
            raise AdmissionClosed('Controlled scan mismatch')
        return BoundScannerPermit(admission, scan_id)
    except BaseException:
        admission.ledger.db.close()
        raise



class BoundScannerPermit(_primitives.ScannerPermit):
    """Adapter for a server-validated descriptor and already-bound scan."""
    def __init__(self, admission, scan_id):
        self.admission, self.scan_id = admission, scan_id

    def call(self, **kwargs):
        self.admission.ledger.assert_open()
        return super().call(**kwargs)

    def close(self):
        self.admission.ledger.db.close()


def provision_disabled(path, record):
    """Local CLI only. Idempotent same binding; never enable or replace an existing record."""
    expected = {'permit_id', 'tenant_id', 'actor', 'request_id', 'target_sha256',
                'token_sha256', 'expires_at'}
    if set(record) != expected:
        raise AdmissionClosed('Exact server-owned record required')
    _identifier(record['permit_id'])
    for key in ('target_sha256', 'token_sha256'):
        if not isinstance(record[key], str) or not re.fullmatch('[0-9a-f]{64}', record[key]):
            raise AdmissionClosed('Digest required')
    if (not isinstance(record['tenant_id'], str) or not record['tenant_id']
            or type(record['expires_at']) is not int or record['expires_at'] <= time.time()):
        raise AdmissionClosed('Future expiry and tenant required')
    _primitives.ControlledAdmission._binding(record['actor'], record['request_id'], record['target_sha256'])
    path = Path(path)
    if not path.is_absolute():
        raise AdmissionClosed('Absolute storage path required')
    path.parent.mkdir(mode=0o700, parents=False, exist_ok=True)
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
        raise AdmissionClosed('Private service-owned directory required')
    if path.exists() or path.is_symlink():
        db = _existing(path)
        try:
            actual = db.execute('SELECT permit_id,tenant_id,expires_at,token_sha256 FROM controlled_permit_context').fetchall()
            binding = db.execute('SELECT actor,request_id,target FROM controlled_admission').fetchall()
            if (actual != [(record['permit_id'], record['tenant_id'], record['expires_at'], record['token_sha256'])]
                    or binding != [(record['actor'], record['request_id'], record['target_sha256'])]):
                raise AdmissionClosed('Existing reservation cannot be replaced')
            return
        finally:
            db.close()
    # Create exclusively: a concurrent provisioner must not overwrite any state.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    ledger = _primitives.ControlledAdmission(path)
    try:
        ledger.db.execute(METADATA_SQL)
        ledger.db.execute('INSERT INTO controlled_permit_context VALUES(1,?,?,?,?)',
                          (record['permit_id'], record['tenant_id'], record['expires_at'], record['token_sha256']))
        ledger.provision(actor=record['actor'], request_id=record['request_id'], target=record['target_sha256'])
        admission = _primitives.OperatorAdmission(ledger, actor=record['actor'],
            request_id=record['request_id'], target=record['target_sha256'], token_sha256=record['token_sha256'])
        ledger.db.execute('CREATE TABLE controlled_tests (test_id TEXT PRIMARY KEY)')
    finally:
        ledger.db.close()
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def disable(path):
    db = _existing(Path(path))
    try:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('UPDATE controlled_admission SET enabled=0')
    finally:
        db.close()


def arm_once(path, record):
    """Local service-owner operation; exact original record, never reset/rearm.

    Commit an irreversible arming marker with enablement. Disabling is permanent
    for this reservation, including when no network operation was attempted.
    """
    expected = {'permit_id', 'tenant_id', 'actor', 'request_id', 'target_sha256',
                'token_sha256', 'expires_at'}
    if not isinstance(record, dict) or set(record) != expected:
        raise AdmissionClosed('Exact original reservation record required')
    ledger = _ExistingLedger(Path(path))
    db = ledger.db
    try:
        db.execute('BEGIN IMMEDIATE')
        ledger.assert_open()
        metadata = db.execute('SELECT permit_id,tenant_id,expires_at,token_sha256 '
                              'FROM controlled_permit_context WHERE singleton=1').fetchone()
        binding = db.execute('SELECT actor,request_id,target,enabled,consumed,tests,'
                             'reserved_microusd,dispatch_state FROM controlled_admission '
                             'WHERE singleton=1').fetchone()
        if (metadata != (record['permit_id'], record['tenant_id'], record['expires_at'], record['token_sha256'])
                or binding != (record['actor'], record['request_id'], record['target_sha256'],
                               0, 0, 0, 0, 'unclaimed')):
            raise AdmissionClosed('Reservation changed or already attempted')
        for table in ('controlled_scan_binding', 'controlled_preflight',
                      'controlled_paid_calls', 'controlled_tests'):
            if db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone():
                raise AdmissionClosed('Reservation already attempted')
        db.execute('CREATE TABLE IF NOT EXISTS controlled_arming '
                   '(singleton INTEGER PRIMARY KEY CHECK(singleton=1))')
        if db.execute('SELECT 1 FROM controlled_arming').fetchone():
            raise AdmissionClosed('Reservation cannot be rearmed')
        db.execute('INSERT INTO controlled_arming VALUES(1)')
        db.execute('UPDATE controlled_admission SET enabled=1 WHERE singleton=1')
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise
    finally:
        db.close()
