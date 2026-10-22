import importlib.util
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import pytest

spec = importlib.util.spec_from_file_location('controlled_admission', Path(__file__).parents[1] / 'backbond/scan-intake/server/controlled_admission.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
B = dict(actor='selected-operator', request_id='selected-request', target='exact-target-awaiting-root')

@pytest.fixture
def path(tmp_path):
    p = str(tmp_path / 'admission.sqlite')
    s = m.ControlledAdmission(p)
    s.provision(**B)
    s.enable()
    s.db.close()
    return p

def test_concurrent_claim_and_restart(path):
    def claim(_):
        s = m.ControlledAdmission(path)
        try:
            s.claim(**B)
            return True
        except m.AdmissionClosed:
            return False
        finally:
            s.db.close()
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(claim, range(20))) == 1
    s = m.ControlledAdmission(path)
    s.begin_dispatch(**B)
    s.db.close()  # lost acceptance response / process restart
    s = m.ControlledAdmission(path)
    s.disable()
    s.enable()
    with pytest.raises(m.AdmissionClosed): s.claim(**B)
    with pytest.raises(m.AdmissionClosed): s.begin_dispatch(**B)
    with pytest.raises(sqlite3.IntegrityError): s.provision(**B)
    assert s.db.execute('SELECT dispatch_state FROM controlled_admission').fetchone()[0] == 'dispatch_unknown'

@pytest.mark.parametrize('key', list(B))
def test_wrong_binding_and_public_closed(path, key):
    s = m.ControlledAdmission(path)
    wrong = dict(B, **{key: 'ordinary-caller'})
    with pytest.raises(m.AdmissionClosed): s.claim(**wrong)
    s.claim(**B)
    with pytest.raises(m.AdmissionClosed): s.begin_dispatch(**wrong)
    s.begin_dispatch(**B)
    with pytest.raises(m.AdmissionClosed): s.reserve_call(**wrong, upper_microusd=1)

@pytest.mark.parametrize('price', [None, 0, -1, 1.0, True, '100', float('nan'), 10_000_001])
def test_unknown_or_invalid_price_stops_before_call(path, price):
    s = m.ControlledAdmission(path)
    s.claim(**B); s.begin_dispatch(**B)
    calls = []
    with pytest.raises(m.AdmissionClosed):
        s.reserve_call(**B, upper_microusd=price)
        calls.append('paid')
    assert calls == []

def test_budget_and_test_caps_persist(path):
    s = m.ControlledAdmission(path)
    s.claim(**B); s.begin_dispatch(**B)
    for _ in range(100): s.reserve_call(**B, upper_microusd=1, tests=1)
    with pytest.raises(m.AdmissionClosed): s.reserve_call(**B, upper_microusd=1, tests=1)
    s.reserve_call(**B, upper_microusd=9_999_900)
    s.db.close()
    s = m.ControlledAdmission(path)
    with pytest.raises(m.AdmissionClosed): s.reserve_call(**B, upper_microusd=1)
    assert s.db.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (100, 10_000_000)

def test_disabled_stops_paid_calls_without_reset(path):
    s = m.ControlledAdmission(path)
    s.claim(**B); s.begin_dispatch(**B)
    s.disable()
    with pytest.raises(m.AdmissionClosed): s.reserve_call(**B, upper_microusd=1)
    assert s.db.execute('SELECT consumed FROM controlled_admission').fetchone()[0] == 1


def test_paid_call_commits_before_io_and_rejects_replay(path):
    s = m.ControlledAdmission(path)
    s.claim(**B)
    s.begin_dispatch(**B)

    def operation():
        observer = sqlite3.connect(path)
        try:
            assert observer.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (1, 123)
            assert observer.execute('SELECT state FROM controlled_paid_calls').fetchone() == ('call_unknown',)
        finally:
            observer.close()
        return 'reply'

    assert s.paid_call(**B, call_id='case-1/target/attempt-1', upper_microusd=123,
                       tests=1, operation=operation) == 'reply'
    s.db.close()
    restarted = m.ControlledAdmission(path)
    with pytest.raises(m.AdmissionClosed):
        restarted.paid_call(**B, call_id='case-1/target/attempt-1', upper_microusd=123,
                            tests=1, operation=lambda: pytest.fail('replayed paid call'))
    assert restarted.db.execute('SELECT state FROM controlled_paid_calls').fetchone() == ('returned',)


@pytest.mark.parametrize('failure', [TimeoutError, SystemExit])
def test_paid_call_unknown_survives_failure_restart_and_disable(path, failure):
    s = m.ControlledAdmission(path)
    s.claim(**B)
    s.begin_dispatch(**B)

    def failed():
        raise failure()

    with pytest.raises(failure):
        s.paid_call(**B, call_id='case-1/judge/attempt-1', upper_microusd=10_000_000,
                    tests=1, operation=failed)
    s.db.close()
    restarted = m.ControlledAdmission(path)
    restarted.disable()
    restarted.enable()
    with pytest.raises(m.AdmissionClosed):
        restarted.paid_call(**B, call_id='case-1/judge/attempt-1', upper_microusd=1,
                            operation=lambda: pytest.fail('unknown call retried'))
    with pytest.raises(m.AdmissionClosed):
        restarted.paid_call(**B, call_id='case-1/judge/attempt-2', upper_microusd=1,
                            operation=lambda: pytest.fail('exhausted budget called'))
    assert restarted.db.execute('SELECT reserved_microusd FROM controlled_admission').fetchone() == (10_000_000,)
    assert restarted.db.execute('SELECT state FROM controlled_paid_calls').fetchone() == ('call_unknown',)


def test_concurrent_paid_attempt_runs_once(path):
    s = m.ControlledAdmission(path)
    s.claim(**B)
    s.begin_dispatch(**B)
    s.db.close()

    def attempt(_):
        ledger = m.ControlledAdmission(path)
        try:
            return ledger.paid_call(**B, call_id='stable-attempt', upper_microusd=7,
                                    tests=1, operation=lambda: 1)
        except m.AdmissionClosed:
            return 0
        finally:
            ledger.db.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(20))) == 1
    observer = sqlite3.connect(path)
    assert observer.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (1, 7)
    observer.close()


@pytest.mark.parametrize('price', [None, 0, -1, True, 10_000_001])
def test_paid_call_invalid_price_leaves_no_attempt_or_spend(path, price):
    s = m.ControlledAdmission(path)
    s.claim(**B)
    s.begin_dispatch(**B)
    with pytest.raises(m.AdmissionClosed):
        s.paid_call(**B, call_id='denied', upper_microusd=price, tests=1,
                    operation=lambda: pytest.fail('invalid price called'))
    assert s.db.execute('SELECT COUNT(*) FROM controlled_paid_calls').fetchone() == (0,)
    assert s.db.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (0, 0)


def test_finish_never_refunds_or_reopens(path):
    s = m.ControlledAdmission(path)
    s.claim(**B)
    s.begin_dispatch(**B)
    s.paid_call(**B, call_id='first', upper_microusd=1, tests=1, operation=lambda: None)
    with pytest.raises(m.AdmissionClosed):
        s.finish(**dict(B, actor='wrong'))
    s.finish(**B)
    s.finish(**B)  # idempotent terminal persistence
    s.enable()
    with pytest.raises(m.AdmissionClosed):
        s.claim(**B)
    with pytest.raises(m.AdmissionClosed):
        s.paid_call(**B, call_id='after-terminal', upper_microusd=1,
                    operation=lambda: pytest.fail('terminal run called'))
    assert s.db.execute('SELECT enabled,consumed,tests,reserved_microusd,dispatch_state FROM controlled_admission').fetchone() == (0, 1, 1, 1, 'terminal')


def test_process_death_after_paid_admission_cannot_repeat(tmp_path, path):
    import subprocess
    import sys

    s = m.ControlledAdmission(path)
    s.claim(**B)
    s.begin_dispatch(**B)
    s.db.close()
    # The child dies inside the operation, after both persistent writes commit.
    # No exception cleanup or normal SQLite connection close runs in that child.
    program = '''
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location('admission', sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
s = m.ControlledAdmission(sys.argv[2])
s.paid_call(actor='selected-operator', request_id='selected-request',
            target='exact-target-awaiting-root', call_id='crashed-attempt',
            upper_microusd=321, tests=1, operation=lambda: os._exit(23))
'''
    result = subprocess.run([sys.executable, '-c', program, str(Path(m.__file__)), path],
                            check=False, capture_output=True, timeout=15)
    assert result.returncode == 23
    restarted = m.ControlledAdmission(path)
    with pytest.raises(m.AdmissionClosed):
        restarted.paid_call(**B, call_id='crashed-attempt', upper_microusd=321,
                            tests=1, operation=lambda: pytest.fail('process crash retried'))
    assert restarted.db.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (1, 321)
    assert restarted.db.execute('SELECT state FROM controlled_paid_calls').fetchone() == ('call_unknown',)


def test_concurrent_distinct_calls_cannot_overspend(path):
    s = m.ControlledAdmission(path)
    s.claim(**B)
    s.begin_dispatch(**B)
    s.db.close()

    def attempt(number):
        ledger = m.ControlledAdmission(path)
        try:
            return ledger.paid_call(**B, call_id=f'case-{number}/target/attempt-1',
                                    upper_microusd=6_000_000, tests=1, operation=lambda: 1)
        except m.AdmissionClosed:
            return 0
        finally:
            ledger.db.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(20))) == 1
    observer = sqlite3.connect(path)
    assert observer.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (1, 6_000_000)
    assert observer.execute('SELECT COUNT(*) FROM controlled_paid_calls').fetchone() == (1,)
    observer.close()


def test_queue_dispatch_boundary_persists_before_start_and_blocks_repeat(path):
    import sys
    from unittest.mock import Mock
    server = str(Path(__file__).parents[1] / 'backbond/scan-intake/server')
    sys.path.insert(0, server)
    try:
        from qa_runtime import ScanQueue, DispatchUnknown
    finally:
        sys.path.remove(server)
    store = Mock()
    store.db.execute.return_value = []
    admission = m.ControlledAdmission(path)
    admission.claim(**B)
    observed = []

    def boundary(scan_id, payload):
        assert scan_id == B['request_id']
        admission.begin_dispatch(**B)

    def start(payload):
        observer = sqlite3.connect(path)
        try:
            observed.append(observer.execute(
                'SELECT dispatch_state FROM controlled_admission').fetchone()[0])
        finally:
            observer.close()
        raise TimeoutError('acceptance response lost')

    queue = ScanQueue(store, start, before_dispatch=boundary)
    payload = {'secret': 'memory-only'}
    with pytest.raises(DispatchUnknown):
        queue.submit(B['request_id'], payload)
    assert payload == {}
    assert observed == ['dispatch_unknown']
    assert B['request_id'] in queue.active
    # The queued-launch path uses the same boundary. Even an accidental replay
    # cannot invoke the scanner after an ambiguous first response.
    store.scan.return_value = {'status': 'dispatch_unknown'}
    replay = {'secret': 'memory-only'}
    queue._launch_queued(B['request_id'], replay)
    assert replay == {}
    assert observed == ['dispatch_unknown']
    assert store.dispatch_unknown.call_count == 2
    admission.db.close()


def operator_fixture(tmp_path):
    import hashlib
    payload = {'agent_endpoint': {'url': 'https://inert.invalid', 'model': 'fixture',
                                'auth': {'type': 'bearer'}},
               'credential_secret': 'inert-secret', 'pack_id': 'starter-100@4'}
    binding = dict(B, target=m.OperatorAdmission.fingerprint(payload))
    ledger = m.ControlledAdmission(str(tmp_path / 'http.sqlite'))
    ledger.provision(**binding)
    ledger.enable()
    adapter = m.OperatorAdmission(ledger, **binding,
        token_sha256=hashlib.sha256(b'inert-operator').hexdigest())
    headers = {'authorization': 'Bearer inert-operator', 'x-controlled-request-id': B['request_id']}
    return ledger, adapter, headers, payload


@pytest.mark.parametrize('change', ['anonymous', 'token', 'request', 'model', 'credential'])
def test_http_operator_rejects_before_consumption(tmp_path, change):
    ledger, adapter, headers, payload = operator_fixture(tmp_path)
    if change == 'anonymous': headers.clear()
    if change == 'token': headers['authorization'] = 'Bearer ordinary'
    if change == 'request': headers['x-controlled-request-id'] = 'replay'
    if change == 'model': payload['agent_endpoint']['model'] = 'substitute'
    if change == 'credential': payload['credential_secret'] = 'substitute'
    with pytest.raises(m.AdmissionClosed): adapter.claim_http(headers, payload)
    assert ledger.db.execute('SELECT consumed FROM controlled_admission').fetchone() == (0,)


def test_http_claim_bind_dispatch_checks_exact_payload_and_replay(tmp_path):
    ledger, adapter, headers, payload = operator_fixture(tmp_path)
    adapter.claim_http(headers, payload)
    with pytest.raises(m.AdmissionClosed): adapter.claim_http(headers, payload)
    adapter.bind_scan('local-scan')
    with pytest.raises(m.AdmissionClosed): adapter.before_dispatch('other-scan', payload)
    changed = dict(payload, credential_secret='changed')
    with pytest.raises(m.AdmissionClosed): adapter.before_dispatch('local-scan', changed)
    adapter.before_dispatch('local-scan', payload)
    with pytest.raises(m.AdmissionClosed): adapter.before_dispatch('local-scan', payload)
    assert ledger.db.execute('SELECT dispatch_state FROM controlled_admission').fetchone() == ('dispatch_unknown',)


def test_http_claim_crash_before_reservation_remains_consumed(tmp_path):
    ledger, adapter, headers, payload = operator_fixture(tmp_path)
    adapter.claim_http(headers, payload)
    path = ledger.db.execute('PRAGMA database_list').fetchone()[2]
    ledger.db.close()
    restarted = m.ControlledAdmission(path)
    with pytest.raises(m.AdmissionClosed): restarted.claim(**adapter.binding)
    assert restarted.db.execute('SELECT COUNT(*) FROM controlled_scan_binding').fetchone() == (0,)


def test_preflight_then_100_cases_share_one_budget(tmp_path, monkeypatch):
    ledger, adapter, headers, payload = operator_fixture(tmp_path)
    calls = []
    def check():
        assert ledger.db.execute('SELECT consumed,tests,reserved_microusd,dispatch_state FROM controlled_admission').fetchone() == (0, 0, 100, 'unclaimed')
        assert ledger.db.execute('SELECT state FROM controlled_preflight').fetchone() == ('check_unknown',)
        assert ledger.db.execute('SELECT COUNT(*) FROM controlled_scan_binding').fetchone() == (0,)
        calls.append('check')
    ledger.preflight_call(**adapter.binding, upper_microusd=100, operation=check)
    adapter.claim_http(headers, payload)
    adapter.bind_scan('0123456789abcdef0123456789abcdef')
    adapter.before_dispatch('0123456789abcdef0123456789abcdef', payload)
    permit = m.ScannerPermit(adapter, scan_id='0123456789abcdef0123456789abcdef', payload=payload)
    from tests.test_controlled_permit_loader import bind_cases
    cases = bind_cases(permit, tmp_path, monkeypatch)
    for case in cases:
        for phase in ('target', 'evaluator'):
            permit.call(test_id=case.case_id, phase=phase,
                        upper_microusd=49_999,
                        operation=lambda: calls.append('case'))
    assert ledger.db.execute('SELECT COUNT(*) FROM controlled_tests').fetchone() == (100,)
    assert ledger.db.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (100, 9_999_900)
    # Consume the last 100 micro-USD on an existing case, then deny all further I/O.
    permit.call(test_id=cases[-1].case_id, phase='final', upper_microusd=100, operation=lambda: None)
    with pytest.raises(m.AdmissionClosed):
        permit.call(test_id=cases[-1].case_id, phase='extra', upper_microusd=1, operation=lambda: pytest.fail('cap exceeded'))
    with pytest.raises(m.AdmissionClosed):
        permit.call(test_id='case-101', phase='target', upper_microusd=1, operation=lambda: pytest.fail('101st test'))
    with pytest.raises(m.AdmissionClosed):
        ledger.preflight_call(**adapter.binding, upper_microusd=1, operation=lambda: pytest.fail('second check'))
    assert len(calls) == 201


def test_unknown_preflight_blocks_restart_retry_and_scan(tmp_path):
    ledger, adapter, headers, payload = operator_fixture(tmp_path)
    path = ledger.db.execute('PRAGMA database_list').fetchone()[2]
    def unknown():
        raise TimeoutError('lost check response')
    with pytest.raises(TimeoutError):
        ledger.preflight_call(**adapter.binding, upper_microusd=123, operation=unknown)
    ledger.db.close()
    restarted = m.ControlledAdmission(path)
    restarted.disable(); restarted.enable()
    with pytest.raises(m.AdmissionClosed):
        restarted.preflight_call(**adapter.binding, upper_microusd=1, operation=lambda: pytest.fail('retry'))
    with pytest.raises(m.AdmissionClosed): restarted.claim(**adapter.binding)
    assert restarted.db.execute('SELECT tests,reserved_microusd,dispatch_state FROM controlled_admission').fetchone() == (0, 123, 'unclaimed')


@pytest.mark.parametrize('price', [None, 0, -1, True, 10_000_001])
def test_invalid_preflight_price_never_calls_or_consumes(path, price):
    ledger = m.ControlledAdmission(path)
    with pytest.raises(m.AdmissionClosed):
        ledger.preflight_call(**B, upper_microusd=price, operation=lambda: pytest.fail('invalid cost'))
    assert ledger.db.execute('SELECT COUNT(*) FROM controlled_preflight').fetchone() == (0,)
    assert ledger.db.execute('SELECT reserved_microusd FROM controlled_admission').fetchone() == (0,)


def test_concurrent_preflight_has_one_durable_attempt(path):
    def attempt(_):
        ledger = m.ControlledAdmission(path)
        try:
            return ledger.preflight_call(**B, upper_microusd=7, operation=lambda: 1)
        except m.AdmissionClosed:
            return 0
        finally:
            ledger.db.close()
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(20))) == 1
    ledger = m.ControlledAdmission(path)
    assert ledger.db.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (0, 7)
    assert ledger.db.execute('SELECT state FROM controlled_preflight').fetchone() == ('returned',)


@pytest.mark.parametrize('test_id', ['case', 'canonical-0', 'PI-00', 'PI-999', 'pi-01', ' PI-01', 'PI-01 ', '', None])
def test_scanner_permit_rejects_noncanonical_identity_before_accounting(tmp_path, monkeypatch, test_id):
    ledger, adapter, headers, payload = operator_fixture(tmp_path)
    adapter.claim_http(headers, payload)
    scan_id = '0123456789abcdef0123456789abcdef'
    adapter.bind_scan(scan_id)
    adapter.before_dispatch(scan_id, payload)
    permit = m.ScannerPermit(adapter, scan_id=scan_id, payload=payload)
    from tests.test_controlled_permit_loader import bind_cases
    bind_cases(permit, tmp_path, monkeypatch)
    before = ledger.db.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone()
    with pytest.raises(m.AdmissionClosed):
        permit.call(test_id=test_id, phase='target', upper_microusd=1,
                    operation=lambda: pytest.fail('noncanonical I/O'))
    assert ledger.db.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == before
    assert ledger.db.execute('SELECT COUNT(*) FROM controlled_tests').fetchone() == (0,)
    assert ledger.db.execute('SELECT COUNT(*) FROM controlled_paid_calls').fetchone() == (0,)
