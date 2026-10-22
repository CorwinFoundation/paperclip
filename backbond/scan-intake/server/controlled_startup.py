"""Protected intake startup binding. Loading never provisions or enables state."""
import json
import os
from pathlib import Path
import sqlite3
import stat
from contextlib import contextmanager

from controlled_admission import AdmissionClosed, ControlledAdmission, OperatorAdmission


def _private_file(value, *, bounded=True):
    path = Path(value)
    if not path.is_absolute():
        raise AdmissionClosed('Absolute controlled startup path required')
    parent = path.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode)
            or parent.st_uid != os.geteuid() or parent.st_mode & 0o077):
        raise AdmissionClosed('Private service-owned directory required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or (bounded and info.st_size > 4096)):
        os.close(fd)
        raise AdmissionClosed('Private bounded startup file required')
    return fd


class PersistentOperator(OperatorAdmission):
    """Own each SQLite connection in the thread performing the operation."""
    @contextmanager
    def _operation(self):
        fd = _private_file(str(self.path), bounded=False)
        os.close(fd)
        db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True,
                             isolation_level=None, timeout=10)
        try:
            db.execute('PRAGMA synchronous=FULL')
            ledger = ControlledAdmission.__new__(ControlledAdmission)
            ledger.db = db
            adapter = OperatorAdmission.__new__(OperatorAdmission)
            adapter.ledger, adapter.binding = ledger, self.binding
            adapter.token_sha256 = self.token_sha256
            yield adapter
        finally:
            db.close()

    def claim_http(self, headers, payload):
        with self._operation() as adapter:
            adapter.claim_http(headers, payload)

    def bind_scan(self, scan_id):
        with self._operation() as adapter:
            adapter.bind_scan(scan_id)

    def before_dispatch(self, scan_id, payload):
        with self._operation() as adapter:
            adapter.before_dispatch(scan_id, payload)


def load_operator():
    value = os.environ.get('QA_CONTROLLED_OPERATOR_CONFIG')
    if not value:
        return None
    if os.environ.get('QA_TERMS_APPROVED', '0') != '0' or os.environ.get('QA_READY', '0') != '0':
        raise AdmissionClosed('Controlled startup requires both general holds')
    db = None
    try:
        fd = _private_file(value)
        try:
            raw = os.read(fd, 4097)
        finally:
            os.close(fd)
        def unique(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise AdmissionClosed('Duplicate startup field')
                result[key] = item
            return result
        config = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(config, dict) or set(config) != {'ledger_path', 'actor', 'request_id', 'target', 'token_sha256', 'scanner_permit_id'}:
            raise AdmissionClosed('Exact controlled startup binding required')
        permit_id = config.pop('scanner_permit_id')
        import re
        if not isinstance(permit_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', permit_id):
            raise AdmissionClosed('Service-owned Scanner permit required')
        path = Path(config.pop('ledger_path'))
        # Validate storage independently, without opening/initializing a new ledger.
        fd = _private_file(str(path), bounded=False)
        os.close(fd)
        db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, isolation_level=None, timeout=10)
        db.execute('PRAGMA synchronous=FULL')
        required = {'controlled_admission', 'controlled_scan_binding', 'controlled_paid_calls', 'controlled_preflight'}
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required <= tables:
            raise AdmissionClosed('Incomplete controlled intake ledger')
        row = db.execute('SELECT actor,request_id,target FROM controlled_admission WHERE singleton=1').fetchone()
        if row != tuple(config[k] for k in ('actor', 'request_id', 'target')):
            raise AdmissionClosed('Controlled startup binding mismatch')
        ledger = ControlledAdmission.__new__(ControlledAdmission)
        ledger.db = db
        operator = OperatorAdmission(ledger, **config)
        persistent = PersistentOperator.__new__(PersistentOperator)
        persistent.scanner_permit_id = permit_id
        persistent.path = path
        persistent.binding = operator.binding
        persistent.token_sha256 = operator.token_sha256
        return persistent
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise AdmissionClosed('Controlled intake startup unavailable') from exc
    finally:
        if db is not None:
            db.close()
