"""Persistent one-use primitives. Not enabled until intake/paid-call wiring is reviewed.

Amounts are integer micro-USD upper bounds, including all tokens/retries. Charges
are reserved before I/O and never refunded after an unknown result.
"""
import sqlite3


class AdmissionClosed(RuntimeError):
    pass


class ControlledAdmission:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS controlled_admission (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            actor TEXT NOT NULL, request_id TEXT NOT NULL, target TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0, consumed INTEGER NOT NULL DEFAULT 0,
            tests INTEGER NOT NULL DEFAULT 0, reserved_microusd INTEGER NOT NULL DEFAULT 0,
            dispatch_state TEXT NOT NULL DEFAULT 'unclaimed')''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS controlled_paid_calls (
            call_id TEXT PRIMARY KEY, upper_microusd INTEGER NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('call_unknown','returned')))''')

        self.db.execute("""CREATE TABLE IF NOT EXISTS controlled_preflight (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            state TEXT NOT NULL CHECK(state IN ('check_unknown','returned')))""")

    def preflight_call(self, *, actor, request_id, target, upper_microusd, operation):
        """One pre-scan check; shared spend, no test ID, no retry after unknown.

        The owning loader validates expiry before calling. operation performs
        exactly one bounded network attempt with provider retries disabled.
        """
        binding = self._binding(actor, request_id, target)
        if (type(upper_microusd) is not int or not 0 < upper_microusd <= 10_000_000
                or not callable(operation)):
            raise AdmissionClosed('Known positive integer price bound required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT 1 FROM controlled_preflight').fetchone():
                raise AdmissionClosed('Preflight already attempted; do not retry')
            result = self.db.execute("""UPDATE controlled_admission
                SET reserved_microusd=reserved_microusd+?
                WHERE singleton=1 AND enabled=1 AND consumed=0
                AND dispatch_state='unclaimed' AND actor=? AND request_id=? AND target=?
                AND reserved_microusd<=?""",
                (upper_microusd, *binding, 10_000_000-upper_microusd))
            if result.rowcount != 1:
                raise AdmissionClosed('Preflight budget or binding unavailable')
            self.db.execute("INSERT INTO controlled_preflight VALUES(1,'check_unknown')")
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        result = operation()
        self.db.execute("UPDATE controlled_preflight SET state='returned' WHERE singleton=1")
        return result

    @staticmethod
    def _binding(actor, request_id, target):
        if any(not isinstance(x, str) or not x or x != x.strip()
               for x in (actor, request_id, target)):
            raise AdmissionClosed('Exact binding required')
        return actor, request_id, target

    def provision(self, *, actor, request_id, target):
        """Local administrative setup only; duplicate provisioning never resets state.

        Call only after Root confirms the exact target. No public HTTP exposure.
        """
        binding = self._binding(actor, request_id, target)
        self.db.execute('INSERT INTO controlled_admission(singleton,actor,request_id,target) '
                        'VALUES(1,?,?,?)', binding)

    def enable(self):
        # Re-enabling consumed state cannot create a second admission.
        self.db.execute('UPDATE controlled_admission SET enabled=1 WHERE consumed=0')

    def disable(self):
        self.db.execute('UPDATE controlled_admission SET enabled=0')

    def claim(self, *, actor, request_id, target):
        binding = self._binding(actor, request_id, target)
        result = self.db.execute('''UPDATE controlled_admission
            SET consumed=1, dispatch_state='claimed'
            WHERE singleton=1 AND enabled=1 AND consumed=0
            AND NOT EXISTS (SELECT 1 FROM controlled_preflight WHERE state='check_unknown')
            AND actor=? AND request_id=? AND target=?''', binding)
        if result.rowcount != 1:
            raise AdmissionClosed('Controlled admission unavailable')

    def begin_dispatch(self, *, actor, request_id, target):
        """Persist unknown BEFORE sending; no restart or retry can send again."""
        binding = self._binding(actor, request_id, target)
        result = self.db.execute('''UPDATE controlled_admission
            SET dispatch_state='dispatch_unknown'
            WHERE singleton=1 AND enabled=1 AND consumed=1 AND dispatch_state='claimed'
            AND actor=? AND request_id=? AND target=?''', binding)
        if result.rowcount != 1:
            raise AdmissionClosed('Dispatch unavailable; do not retry')

    def reserve_call(self, *, actor, request_id, target, upper_microusd, tests=0):
        """Authorize one paid operation only after its full worst-case price is known.

        The worker must invoke this immediately before EVERY paid call, including
        retries and judges. tests=1 starts a case; further calls in that case use 0.
        """
        binding = self._binding(actor, request_id, target)
        if (type(upper_microusd) is not int or upper_microusd <= 0
                or type(tests) is not int or tests not in (0, 1)):
            raise AdmissionClosed('Known positive integer price bound required')
        if upper_microusd > 10_000_000:
            raise AdmissionClosed('Budget exhausted')
        result = self.db.execute('''UPDATE controlled_admission
            SET reserved_microusd=reserved_microusd+?, tests=tests+?
            WHERE singleton=1 AND enabled=1 AND consumed=1
            AND dispatch_state='dispatch_unknown' AND actor=? AND request_id=? AND target=?
            AND reserved_microusd<=? AND tests<=?''',
            (upper_microusd, tests, *binding, 10_000_000-upper_microusd, 100-tests))
        if result.rowcount != 1:
            raise AdmissionClosed('Budget, test limit or binding unavailable')

    def paid_call(self, *, call_id, actor, request_id, target, upper_microusd,
                  operation, tests=0):
        """Commit spend and a unique attempt BEFORE invoking an operation once.

        call_id is a stable, service-generated case/phase/attempt identity. It
        must survive worker restarts; it must never be regenerated to retry an
        unknown call. operation must perform exactly one network attempt (no
        internal retries). Its complete worst-case price includes token limits,
        tools and any provider-side charges; unknown bounds fail closed.

        No credentials, prompts, target URLs or responses enter the call ledger.
        An exception or crash retains both spend and call_unknown. A successful
        response also cannot authorize a repeat of the same call identity.
        """
        if (not isinstance(call_id, str) or not call_id or call_id != call_id.strip()
                or len(call_id) > 256 or not callable(operation)):
            raise AdmissionClosed('Stable paid-call identity and operation required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT 1 FROM controlled_paid_calls WHERE call_id=?',
                               (call_id,)).fetchone():
                raise AdmissionClosed('Paid call already attempted; do not retry')
            self.reserve_call(actor=actor, request_id=request_id, target=target,
                              upper_microusd=upper_microusd, tests=tests)
            self.db.execute('INSERT INTO controlled_paid_calls VALUES(?,?,?)',
                            (call_id, upper_microusd, 'call_unknown'))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        result = operation()
        self.db.execute("UPDATE controlled_paid_calls SET state='returned' WHERE call_id=?",
                        (call_id,))
        return result

    def finish(self, *, actor, request_id, target):
        """Close paid admission permanently after any confirmed terminal result.

        Intake/worker result persistence is responsible for distinguishing a
        complete run from a budget stop or failed run; this never asserts success.
        Unknown dispatch must not be converted to a confirmed terminal result by
        timeout alone. Disabling or finishing never refunds or resets consumption.
        """
        binding = self._binding(actor, request_id, target)
        result = self.db.execute('''UPDATE controlled_admission SET enabled=0,
            dispatch_state='terminal' WHERE singleton=1 AND consumed=1
            AND actor=? AND request_id=? AND target=?''', binding)
        if result.rowcount != 1:
            raise AdmissionClosed('Terminal binding unavailable')


class OperatorAdmission:
    """Service-owned offline wiring; no environment activation or HTTP provisioning.

    The owning service must supply a validated Scanner permit before production
    construction is implemented. Fixtures may inject this adapter with inert values.
    Target fingerprints include credentials but only their digest is persisted.
    """
    def __init__(self, ledger, *, actor, request_id, target, token_sha256):
        import re
        self.binding = dict(zip(('actor', 'request_id', 'target'),
                                ledger._binding(actor, request_id, target)))
        if not isinstance(token_sha256, str) or not re.fullmatch('[0-9a-f]{64}', token_sha256):
            raise AdmissionClosed('Operator token digest required')
        self.ledger, self.token_sha256 = ledger, token_sha256
        ledger.db.execute('CREATE TABLE IF NOT EXISTS controlled_scan_binding '
                          '(singleton INTEGER PRIMARY KEY CHECK(singleton=1), scan_id TEXT UNIQUE NOT NULL)')

    @staticmethod
    def fingerprint(payload):
        import hashlib
        import json
        if not isinstance(payload.get('agent_endpoint'), dict) or not payload.get('credential_secret'):
            raise AdmissionClosed('Exact endpoint and credential binding required')
        # Bind the complete execution request, including pack, model, auth and callbacks.
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                         allow_nan=False).encode()).hexdigest()

    def authenticate_http(self, headers):
        import hashlib
        import hmac
        authorization = headers.get('authorization', '')
        if not authorization.startswith('Bearer ') or not authorization[7:]:
            raise AdmissionClosed('Operator authentication required')
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        if (not hmac.compare_digest(digest, self.token_sha256)
                or headers.get('x-controlled-request-id') != self.binding['request_id']):
            raise AdmissionClosed('Operator or target mismatch')

    def verify_http(self, headers, payload):
        self.authenticate_http(headers)
        if self.fingerprint(payload) != self.binding['target']:
            raise AdmissionClosed('Operator or target mismatch')

    def claim_http(self, headers, payload):
        self.verify_http(headers, payload)
        self.ledger.claim(**self.binding)

    def bind_scan(self, scan_id):
        # A crash between claim/reserve/bind permanently consumes admission.
        # Neither a failed reservation nor a lost HTTP response authorizes replay.
        self.ledger.db.execute('INSERT INTO controlled_scan_binding VALUES(1,?)', (scan_id,))

    def before_dispatch(self, scan_id, payload):
        row = self.ledger.db.execute('SELECT scan_id FROM controlled_scan_binding WHERE singleton=1').fetchone()
        if (not row or row[0] != scan_id
                or self.fingerprint(payload) != self.binding['target']):
            raise AdmissionClosed('Dispatch payload or reservation mismatch')
        self.ledger.begin_dispatch(**self.binding)


class ScannerPermit:
    """Offline adapter for one bound scan and its combined paid-call ledger.

    This is deliberately not a production loader. The Scanner must supply stable
    test/phase identities and bounded single-attempt operations at every paid I/O.
    Construct a new adapter after restart; never reset or copy the ledger.
    """
    def __init__(self, admission, *, scan_id, payload):
        row = admission.ledger.db.execute(
            'SELECT scan_id FROM controlled_scan_binding WHERE singleton=1').fetchone()
        if (not row or row[0] != scan_id
                or admission.fingerprint(payload) != admission.binding['target']):
            raise AdmissionClosed('Scanner permit binding mismatch')
        self.admission, self.scan_id = admission, scan_id
        admission.ledger.db.execute('''CREATE TABLE IF NOT EXISTS controlled_tests (
            test_id TEXT PRIMARY KEY)''')

    def _accepted_form(self, tenant_id, agent_id, pack_id):
        import hashlib
        import json
        from scanners.starter_forms import starter_form_store, cases_from_form
        from scanners.variable_test_forms import FormError
        if pack_id != 'starter-100@4':
            raise AdmissionClosed('Controlled accepted @4 form required')
        try:
            # Existing authority only: never draw a form at the paid boundary.
            form = starter_form_store().get(
                self.scan_id, scope=(tenant_id, agent_id), product='starter')
            ids = tuple(case.case_id for case in cases_from_form(form))
            if (len(ids) != 100 or len(set(ids)) != 100
                    or form['form_id'] != self.scan_id or form['product'] != 'starter'
                    or not form['policy']['bank_version'].startswith('starter-bank@4.')):
                raise AdmissionClosed('Invalid accepted Starter form')
            digest = hashlib.sha256(json.dumps(form, sort_keys=True, separators=(',', ':'),
                ensure_ascii=False, allow_nan=False).encode()).hexdigest()
            return digest, frozenset(ids)
        except (FormError, KeyError, TypeError, ValueError) as exc:
            raise AdmissionClosed('Accepted Starter form unavailable') from exc

    def bind_accepted_form(self, *, tenant_id, agent_id, pack_id):
        """Service scope after reservation; no caller allowlist or replacement."""
        ledger = self.admission.ledger
        metadata = ledger.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='controlled_permit_context'"
        ).fetchone()
        if metadata and ledger.db.execute(
                'SELECT tenant_id FROM controlled_permit_context WHERE singleton=1'
                ).fetchone() != (tenant_id,):
            raise AdmissionClosed('Accepted form tenant mismatch')
        digest, _ = self._accepted_form(tenant_id, agent_id, pack_id)
        ledger.db.execute('''CREATE TABLE IF NOT EXISTS controlled_accepted_form (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), scan_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL, agent_id TEXT NOT NULL, pack_id TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL)''')
        binding = (self.scan_id, tenant_id, agent_id, pack_id, digest)
        ledger.db.execute('INSERT OR IGNORE INTO controlled_accepted_form VALUES(1,?,?,?,?,?)', binding)
        if ledger.db.execute('SELECT scan_id,tenant_id,agent_id,pack_id,manifest_sha256 '
                             'FROM controlled_accepted_form WHERE singleton=1').fetchone() != binding:
            raise AdmissionClosed('Accepted form cannot be replaced')

    def _selected_ids(self):
        try:
            row = self.admission.ledger.db.execute(
                'SELECT scan_id,tenant_id,agent_id,pack_id,manifest_sha256 '
                'FROM controlled_accepted_form WHERE singleton=1').fetchone()
        except sqlite3.Error as exc:
            raise AdmissionClosed('Accepted form not bound') from exc
        if not row or row[0] != self.scan_id:
            raise AdmissionClosed('Accepted form not bound')
        digest, ids = self._accepted_form(*row[1:4])
        if digest != row[4]:
            raise AdmissionClosed('Accepted form changed')
        return ids

    def call(self, *, test_id, phase, upper_microusd, operation):
        """One target/evaluator attempt, with a shared USD10 and 100-test cap.

        Tests are counted once before their first paid operation. A crash after
        counting can consume a test without a call; it cannot restore allowance.
        Phase must identify a logical operation, not a newly generated retry ID.
        """
        import json
        if any(not isinstance(x, str) or not x or x != x.strip() or len(x) > 100
               for x in (test_id, phase)):
            raise AdmissionClosed('Stable test and phase identities required')
        if test_id not in self._selected_ids():
            raise AdmissionClosed('Selected accepted Starter test identity required')
        ledger = self.admission.ledger
        ledger.db.execute('BEGIN IMMEDIATE')
        try:
            if not ledger.db.execute('SELECT 1 FROM controlled_tests WHERE test_id=?',
                                     (test_id,)).fetchone():
                result = ledger.db.execute('''UPDATE controlled_admission SET tests=tests+1
                    WHERE singleton=1 AND enabled=1 AND consumed=1
                    AND dispatch_state='dispatch_unknown' AND tests<100
                    AND actor=? AND request_id=? AND target=?''',
                    tuple(self.admission.binding[k] for k in ('actor', 'request_id', 'target')))
                if result.rowcount != 1:
                    raise AdmissionClosed('Test limit or admission unavailable')
                ledger.db.execute('INSERT INTO controlled_tests VALUES(?)', (test_id,))
            ledger.db.execute('COMMIT')
        except BaseException:
            ledger.db.execute('ROLLBACK')
            raise
        return ledger.paid_call(
            call_id=json.dumps([self.scan_id, test_id, phase], separators=(',', ':')),
            **self.admission.binding, upper_microusd=upper_microusd, operation=operation)
