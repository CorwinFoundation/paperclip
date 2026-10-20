"""BEAAA-42596: finished BAC-bound Starter scans are recorded in the Operations workflow
(customer-operations ``POST /v1/starter-scans`` -> ``record_starter_result``) through a
durable intake outbox. Fixtures only; the Operations route is a fake that mirrors its
contract (idempotent identical replay, 409 on a conflicting replay, 422 on forbidden fields)."""
import hashlib
import importlib
import itertools
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient  # noqa: F401  (import before httpx is patched)

sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))
from bac_intake import BACIntake  # noqa: E402
from operations_client import (PAYLOAD_KEYS, OperationsClient, OperationsOutbox,  # noqa: E402
                               build_starter_payload)
from qa_runtime import ScanQueue  # noqa: E402
from qa_service import Code, IntakeStore  # noqa: E402
from report_tool import freeze_starter_input  # noqa: E402

BASE = 'https://ops.invalid'
TOKEN = 'integration-bearer'
EMAIL = 'customer@example.test'
PRIVATE_TESTS = [{'id': 't1', 'verdict': 'pass', 'prompt': 'PRIVATE-PROMPT-1', 'reply': 'PRIVATE-REPLY-1'},
                 {'id': 't2', 'verdict': 'fail', 'prompt': 'PRIVATE-PROMPT-2', 'reply': 'PRIVATE-REPLY-2',
                  'reason': 'PRIVATE-REASON'},
                 {'id': 't3', 'verdict': 'weird', 'prompt': 'PRIVATE-PROMPT-3'}]
# customer-operations starter_reports.py: the stored keys and the refused keys.
OPERATIONS_STORED_KEYS = {'schema_version', 'bac_request_id', 'bac_case_id', 'bac_scan_id', 'evidence_sha256',
                          'evidence_refs', 'provenance', 'scope_complete', 'first_scan', 'result'}
OPERATIONS_FORBIDDEN = {'comparison_id', 'baseline_assessment_id', 'predecessor_assessment_id',
                        'retest_completed', 'assessment_id', 'recipient', 'recipient_id'}


class Response:
    def __init__(self, status_code, body=None):
        self.status_code, self.body = status_code, body

    def json(self):
        if self.body is None:
            raise ValueError('no body')
        return self.body


class FakeOperations:
    """Mirrors record_starter_result: validate, identical replay -> same row, else 409."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []
        self.rows = {}

    def post(self, url, **kw):
        self.calls.append(dict(kw, url=url))
        step = self.script.pop(0) if self.script else 'ok'
        if step == 'timeout':
            raise TimeoutError('operations timed out')
        if isinstance(step, int):
            return Response(step, {'detail': 'scripted'})
        payload = kw['json']
        if payload.get('schema_version') != 'starter_scan_result_v1' or OPERATIONS_FORBIDDEN & payload.keys():
            return Response(422, {'detail': 'invalid'})
        stored = {k: payload[k] for k in OPERATIONS_STORED_KEYS}
        digest = hashlib.sha256(json.dumps(stored, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        existing = self.rows.get(payload['bac_scan_id'])
        if existing and existing['request_hash'] != digest:
            return Response(409, {'detail': 'Starter scan already recorded with different content'})
        row = existing or {'result_id': 'res-%d' % (len(self.rows) + 1), 'request_hash': digest,
                           **{k: payload[k] for k in ('bac_request_id', 'bac_case_id', 'bac_scan_id',
                                                      'evidence_sha256')}}
        self.rows[payload['bac_scan_id']] = row
        if step == 'lost':  # Operations recorded it; the response never arrived.
            raise TimeoutError('response lost')
        return Response(202, dict(row, replayed=existing is not None))


def no_network(*a, **k):
    pytest.fail('network call')


@pytest.fixture
def env(tmp_path):
    path = str(tmp_path / 'qa.sqlite')
    store = IntakeStore(path)
    store.put_code(Code('TEST', 'test', 'Test', 'standard', 5, 5, '2099-01-01'))
    queue = ScanQueue(store, lambda payload, n=itertools.count(1): {'scan_id': 'remote-%d' % next(n)})
    bac = BACIntake(path, base_url='', company_id='', owner_id='', token='', post=no_network, get=no_network)
    return store, queue, bac, OperationsOutbox(path)


def reserve(store):
    return store.reserve(code='TEST', email=EMAIL, agent_label='Agent', endpoint_url='https://agent.example.test',
                         transport='rest')['scan_id']


def finish(store, queue, sid):
    queue.submit(sid, {})
    queue.callback(store.scan(sid)['scanner_id'], {'tests': PRIVATE_TESTS, 'meta': {}})
    assert store.scan(sid)['status'] == 'finished'


def bind(bac, sid, case_id='case-1', state='acknowledged'):
    bac.db.execute('INSERT OR IGNORE INTO bac_intake VALUES (?,?,?,?)', (sid, '{}', state, case_id))
    bac.db.execute('INSERT OR IGNORE INTO bac_scan_bindings VALUES (?,?)', (sid, sid))


def bound_finished(store, queue, bac):
    sid = reserve(store)
    bind(bac, sid)
    finish(store, queue, sid)
    return sid


def capture(bac, store, queue, outbox, client, now=100):
    bac.capture_scans(store, queue, None, now=now, limit=10,
                      on_finished=lambda request_id, scan_id: outbox.enqueue(
                          bac, store, request_id=request_id, scan_id=scan_id, client=client))


def test_payload_shape_is_exactly_the_operations_starter_contract_from_the_evidence_path(env):
    store, queue, bac, outbox = env
    sid = bound_finished(store, queue, bac)
    ops = FakeOperations()
    client = OperationsClient(BASE, TOKEN, ops.post)
    capture(bac, store, queue, outbox, client)
    assert outbox.flush(client, now=200) == [(sid, 'acknowledged')]
    sent = ops.calls[0]['json']
    assert set(sent) == PAYLOAD_KEYS == OPERATIONS_STORED_KEYS
    assert not OPERATIONS_FORBIDDEN & set(sent)
    # Every value is derived from the private BAC case resolver, nothing else.
    digest = bac.evidence_digest(store, request_id=sid, case_id='case-1', scan_id=sid)
    assert digest == hashlib.sha256(store.scan(sid)['tests_json'].encode()).hexdigest()
    frozen = freeze_starter_input(bac, store, request_id=sid, case_id='case-1', scan_id=sid,
                                  evidence_sha256=digest)
    assert sent == {
        'schema_version': 'starter_scan_result_v1',
        'bac_request_id': sid, 'bac_case_id': 'case-1', 'bac_scan_id': sid,
        'first_scan': True, 'evidence_sha256': digest, 'evidence_refs': ['intake:' + sid],
        'provenance': {'source': 'backbond-scan-intake', 'input_schema': 'starter_report_input@1',
                       'input_sha256': frozen['input_sha256'], 'pack_version': 'starter-100@4'},
        # One could_not_run: coverage is NOT complete, whatever the terminal outcome says.
        'scope_complete': False,
        'result': {'outcome': 'complete', 'verdict_counts': {'pass': 1, 'fail': 1, 'could_not_run': 1},
                   'results': [{'ordinal': 1, 'verdict': 'pass'}, {'ordinal': 2, 'verdict': 'fail'},
                               {'ordinal': 3, 'verdict': 'could_not_run'}],
                   'coverage': frozen['input']['coverage']}}
    assert frozen['input']['coverage']['complete'] is False
    assert sent == build_starter_payload(bac, store, request_id=sid, case_id='case-1', scan_id=sid)
    row = outbox.row(sid)
    assert (row['request_id'], row['case_id'], row['evidence_sha256'], row['state'],
            row['attempts'], row['operations_starter_id']) == (sid, 'case-1', digest, 'acknowledged', 1, 'res-1')


def test_unbound_unacknowledged_or_unfinished_scans_are_never_enqueued(env):
    store, queue, bac, outbox = env
    ops = FakeOperations()
    client = OperationsClient(BASE, TOKEN, ops.post)
    unbound = reserve(store)
    finish(store, queue, unbound)
    unacknowledged = reserve(store)
    bind(bac, unacknowledged, state='unconfirmed')
    finish(store, queue, unacknowledged)
    running = reserve(store)
    bind(bac, running)
    queue.submit(running, {})
    capture(bac, store, queue, outbox, client)
    for sid in (unbound, unacknowledged, running):
        assert outbox.enqueue(bac, store, request_id=sid, scan_id=sid, client=client) is False
    # A mismatched request binding is refused too.
    assert outbox.enqueue(bac, store, request_id='other', scan_id=running, client=client) is False
    assert outbox.db.execute('SELECT count(*) FROM operations_starter_outbox').fetchone()[0] == 0
    assert outbox.flush(client, now=200) == [] and ops.calls == []


def test_no_customer_email_recipient_or_private_text_is_ever_sent(env):
    store, queue, bac, outbox = env
    sid = bound_finished(store, queue, bac)
    ops = FakeOperations()
    client = OperationsClient(BASE, TOKEN, ops.post)
    capture(bac, store, queue, outbox, client)
    outbox.flush(client, now=200)
    wire = json.dumps(ops.calls[0]['json'])
    stored = outbox.row(sid)['payload']
    for secret in (EMAIL, 'agent.example.test', 'PRIVATE', 'recipient', 'email', 'info@backbond.ai',
                   'comparison', 'assessment', 'retest'):
        assert secret not in wire and secret not in stored


def test_auth_header_https_and_no_redirects_are_enforced(env):
    store, queue, bac, outbox = env
    sid = bound_finished(store, queue, bac)
    for base in ('http://ops.invalid', 'ops.invalid', 'https://user:pw@ops.invalid', 'https://ops.invalid?x=1', ''):
        client = OperationsClient(base, TOKEN, no_network)
        assert client.configured is False
        assert outbox.enqueue(bac, store, request_id=sid, scan_id=sid, client=client) is False
        assert outbox.flush(client, now=200) == []
    assert OperationsClient(BASE, '', no_network).configured is False
    ops = FakeOperations()
    client = OperationsClient(BASE + '/', TOKEN, ops.post)
    assert outbox.enqueue(bac, store, request_id=sid, scan_id=sid, client=client) is True
    outbox.flush(client, now=200)
    call = ops.calls[0]
    assert call['url'] == 'https://ops.invalid/v1/starter-scans'
    assert call['headers'] == {'Authorization': 'Bearer ' + TOKEN}
    assert call['follow_redirects'] is False and call['timeout'] == 20
    # A redirect is not an acknowledgment.
    sid2 = bound_finished(store, queue, bac)
    redirect = OperationsClient(BASE, TOKEN, FakeOperations(302).post)
    outbox.enqueue(bac, store, request_id=sid2, scan_id=sid2, client=redirect)
    assert outbox.flush(redirect, now=300) == [(sid2, 'uncertain')]


def test_idempotent_replay_leaves_one_acknowledged_row(env):
    store, queue, bac, outbox = env
    sid = bound_finished(store, queue, bac)
    ops = FakeOperations('lost')  # recorded by Operations, response lost
    client = OperationsClient(BASE, TOKEN, ops.post)
    for now in (100, 130, 160):  # capture revisits finished scans; enqueue is idempotent
        capture(bac, store, queue, outbox, client, now=now)
    assert outbox.flush(client, now=200) == [(sid, 'uncertain')]
    assert outbox.flush(client, now=205) == []  # backoff holds the retry
    assert outbox.flush(client, now=1000) == [(sid, 'acknowledged')]
    assert outbox.flush(client, now=99999) == []
    capture(bac, store, queue, outbox, client, now=100000)
    assert outbox.flush(client, now=100001) == []
    rows = outbox.db.execute('SELECT scan_id,state,operations_starter_id FROM operations_starter_outbox').fetchall()
    assert rows == [(sid, 'acknowledged', 'res-1')]
    assert len(ops.rows) == 1 and len(ops.calls) == 2
    assert ops.calls[0]['json'] == ops.calls[1]['json']


@pytest.mark.parametrize('failure', ['timeout', 500, 503])
def test_timeout_or_5xx_is_uncertain_then_retried_once_with_the_same_payload(env, failure):
    store, queue, bac, outbox = env
    sid = bound_finished(store, queue, bac)
    ops = FakeOperations(failure)
    client = OperationsClient(BASE, TOKEN, ops.post)
    capture(bac, store, queue, outbox, client)
    assert outbox.flush(client, now=200) == [(sid, 'uncertain')]
    row = outbox.row(sid)
    assert (row['state'], row['attempts'], row['operations_starter_id']) == ('uncertain', 1, None)
    assert row['next_at'] == 210
    assert outbox.flush(client, now=209) == []
    assert outbox.flush(client, now=210) == [(sid, 'acknowledged')]
    assert len(ops.calls) == 2 and ops.calls[0]['json'] == ops.calls[1]['json']
    assert outbox.row(sid)['attempts'] == 2


@pytest.mark.parametrize('status', [409, 422])
def test_conflict_or_rejection_is_held_for_an_operator_and_not_retried(env, status):
    store, queue, bac, outbox = env
    sid = bound_finished(store, queue, bac)
    ops = FakeOperations(status)
    client = OperationsClient(BASE, TOKEN, ops.post)
    capture(bac, store, queue, outbox, client)
    assert outbox.flush(client, now=200) == [(sid, 'held')]
    for now in (300, 10_000, 1_000_000):
        capture(bac, store, queue, outbox, client, now=now)
        assert outbox.flush(client, now=now) == []
    row = outbox.row(sid)
    assert (row['state'], row['last_status'], row['operations_starter_id']) == ('held', str(status), None)
    assert len(ops.calls) == 1


def test_real_conflicting_replay_from_operations_is_held(env):
    """Operations already holds a different result for this bac_scan_id -> 409 -> held."""
    store, queue, bac, outbox = env
    sid = bound_finished(store, queue, bac)
    ops = FakeOperations()
    ops.rows[sid] = {'result_id': 'res-old', 'request_hash': '0' * 64}
    client = OperationsClient(BASE, TOKEN, ops.post)
    capture(bac, store, queue, outbox, client)
    assert outbox.flush(client, now=200) == [(sid, 'held')]
    assert outbox.flush(client, now=10_000) == [] and len(ops.calls) == 1


def test_acknowledgment_with_foreign_binding_is_held(env):
    store, queue, bac, outbox = env
    sid = bound_finished(store, queue, bac)
    client = OperationsClient(BASE, TOKEN, lambda url, **kw: Response(202, dict(
        result_id='res-x', bac_request_id=sid, bac_case_id='case-other', bac_scan_id=sid,
        evidence_sha256=kw['json']['evidence_sha256'])))
    capture(bac, store, queue, outbox, client)
    assert outbox.flush(client, now=200) == [(sid, 'held')]
    assert outbox.row(sid)['operations_starter_id'] is None


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    os.environ.setdefault('QA_DB_PATH', str(Path(tempfile.mkdtemp()) / 'import.sqlite'))
    app = importlib.import_module('app')
    import httpx
    for name in ('post', 'get', 'Client'):
        monkeypatch.setattr(httpx, name, no_network)
    monkeypatch.delenv('QA_OPERATIONS_BASE_URL', raising=False)
    monkeypatch.delenv('QA_OPERATIONS_TOKEN', raising=False)
    path = str(tmp_path / 'qa.sqlite')
    store = IntakeStore(path)
    store.put_code(Code('TEST', 'test', 'Test', 'standard', 5, 5, '2099-01-01'))
    queue = ScanQueue(store, lambda payload, n=itertools.count(1): {'scan_id': 'remote-%d' % next(n)})
    bac = BACIntake(path, base_url='', company_id='', owner_id='', token='', post=no_network, get=no_network)
    outbox = OperationsOutbox(path)
    from report_release import ReportRelease
    for name, value in (('store', store), ('queue', queue), ('bac', bac), ('operations', outbox),
                        ('release', ReportRelease(path, lead_actor_id=lambda: ''))):
        monkeypatch.setattr(app, name, value)
    monkeypatch.setattr(app, '_engine_progress', lambda _: {})
    return app, store, queue, bac, outbox


def test_env_unset_worker_makes_no_operations_call_and_enqueues_nothing(app_env, monkeypatch):
    app, store, queue, bac, outbox = app_env
    bound_finished(store, queue, bac)
    for _ in range(3):
        app.worker_tick()
    assert outbox.db.execute('SELECT count(*) FROM operations_starter_outbox').fetchone()[0] == 0
    monkeypatch.setenv('QA_OPERATIONS_BASE_URL', BASE)  # token still unset
    app.worker_tick()
    assert outbox.db.execute('SELECT count(*) FROM operations_starter_outbox').fetchone()[0] == 0


def test_worker_tick_enqueues_and_flushes_one_finished_bound_scan(app_env, monkeypatch):
    app, store, queue, bac, outbox = app_env
    import httpx
    sid = bound_finished(store, queue, bac)
    ops = FakeOperations()
    reads = []
    monkeypatch.setattr(httpx, 'post', ops.post)
    monkeypatch.setattr(httpx, 'get', lambda url, **kw: reads.append(dict(kw, url=url)) or Response(404, {}))
    monkeypatch.setenv('QA_OPERATIONS_BASE_URL', BASE)
    monkeypatch.setenv('QA_OPERATIONS_TOKEN', TOKEN)
    app.worker_tick()
    assert outbox.row(sid)['state'] == 'acknowledged' and outbox.row(sid)['operations_starter_id'] == 'res-1'
    assert [c['url'] for c in ops.calls] == ['https://ops.invalid/v1/starter-scans']
    # Then the Operations status read-back (BEAAA-42596 page status): GET only, same bearer, no redirects.
    assert [(r['url'], r['headers'], r['follow_redirects']) for r in reads] == [
        (f'https://ops.invalid/v1/starter-scans/{sid}/status', {'Authorization': 'Bearer ' + TOKEN}, False)]
    assert EMAIL not in json.dumps(ops.calls[0]['json'])


# --- BEAAA-42596 coverage boundary (board review 2026-10-20 07:58Z) -----------------------------

def _payload_for(env, monkeypatch, *, outcome, verdicts, accepted):
    store, queue, bac, outbox = env
    sid = bound_finished(store, queue, bac)
    real = bac.resolve_evidence

    def resolve(*a, **kw):
        value = real(*a, **kw)
        return dict(value, outcome=outcome,
                    results=[{'ordinal': n, 'verdict': v} for n, v in enumerate(verdicts, 1)])
    monkeypatch.setattr(bac, 'resolve_evidence', resolve)
    monkeypatch.setattr(bac, 'latest_progress', lambda scan_id: {} if accepted is None
                        else {'accepted_pack_total': accepted})
    return build_starter_payload(bac, store, request_id=sid, case_id='case-1', scan_id=sid)


def test_one_could_not_run_is_never_complete_scope(env, monkeypatch):
    p = _payload_for(env, monkeypatch, outcome='complete', verdicts=['pass'] * 99 + ['could_not_run'],
                     accepted=100)
    assert p['scope_complete'] is False
    assert p['result']['coverage'] == {'accepted_total': 100, 'accepted_scope_confirmed': True,
                                       'reported': 100, 'usable': 99, 'unavailable': 1, 'complete': False}


def test_null_outcome_is_never_defaulted_to_complete(env, monkeypatch):
    p = _payload_for(env, monkeypatch, outcome=None, verdicts=['pass'] * 100, accepted=100)
    assert p['scope_complete'] is False
    assert p['result']['outcome'] == 'unconfirmed'
    assert p['result']['coverage']['complete'] is False


def test_absent_accepted_scope_is_unconfirmed_not_inferred_from_pack(env, monkeypatch):
    p = _payload_for(env, monkeypatch, outcome='complete', verdicts=['pass'] * 100, accepted=None)
    assert p['scope_complete'] is False
    assert p['result']['coverage']['accepted_scope_confirmed'] is False
    assert p['result']['coverage']['accepted_total'] is None


def test_partial_accepted_scope_is_incomplete_and_keeps_reported_failures(env, monkeypatch):
    p = _payload_for(env, monkeypatch, outcome='complete', verdicts=['pass'] * 35 + ['fail'], accepted=37)
    assert p['scope_complete'] is False
    assert p['result']['coverage']['reported'] == 36
    assert p['result']['verdict_counts'] == {'pass': 35, 'fail': 1, 'could_not_run': 0}


@pytest.mark.parametrize('outcome', ['incomplete', 'failed'])
def test_incomplete_or_failed_outcome_is_reported_not_dropped(env, monkeypatch, outcome):
    p = _payload_for(env, monkeypatch, outcome=outcome, verdicts=['pass'] * 37, accepted=37)
    assert p['scope_complete'] is False and p['result']['outcome'] == outcome


def test_recorded_37_scope_fully_usable_is_complete(env, monkeypatch):
    p = _payload_for(env, monkeypatch, outcome='complete', verdicts=['pass'] * 30 + ['fail'] * 7, accepted=37)
    assert p['scope_complete'] is True
    assert p['result']['coverage'] == {'accepted_total': 37, 'accepted_scope_confirmed': True,
                                       'reported': 37, 'usable': 37, 'unavailable': 0, 'complete': True}


def test_bool_or_non_positive_accepted_total_is_not_a_confirmed_scope(env, monkeypatch):
    for bad in (True, 0, -1, '37', 37.0):
        p = _payload_for(env, monkeypatch, outcome='complete', verdicts=['pass'], accepted=bad)
        assert p['scope_complete'] is False, bad
