"""BEAAA-42596: the Starter page's report/delivery status comes from the Operations workflow.

Operations (not intake) writes the final Starter report and sends it to info@backbond.ai. Intake
polls ``GET {QA_OPERATIONS_BASE_URL}/v1/starter-scans/{bac_scan_id}/status`` (``starter_status_v1``)
for acknowledged outbox rows, keeps the last strictly valid snapshot, and maps it into the page's
``starter_lifecycle@1`` vocabulary. Fixtures and fake HTTP only."""
import importlib
import itertools
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient  # noqa: F401  (import before httpx is patched)

sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))
sys.path.insert(0, str(Path(__file__).parent))
import report_release  # noqa: E402
from bac_intake import BACIntake  # noqa: E402
from operations_client import (OPERATIONS_DELIVERY_STATUSES, OPERATIONS_REPORT_STATUSES,  # noqa: E402
                               PUBLIC_DELIVERY_STATUS, PUBLIC_REPORT_STATUS, STATUS_KEYS, OperationsClient,
                               OperationsOutbox, parse_starter_status)
from qa_runtime import ScanQueue  # noqa: E402
from qa_service import Code, IntakeStore  # noqa: E402
from test_operations_binding import (BASE, EMAIL, TOKEN, FakeOperations, Response, bind, finish,  # noqa: E402
                                     no_network, reserve)

UPDATED = '2026-10-20T10:00:00+00:00'
REPORT_MAP = {'awaiting_report': 'awaiting_results', 'review': 'review', 'changes_requested': 'changes_requested',
              'awaiting_lead_approval': 'awaiting_ceo_approval', 'approved': 'approved'}
DELIVERY_MAP = {'not_ready': 'not_ready', 'queued': 'pending', 'sending': 'sending',
                'provider_outcome_unknown': 'pending', 'provider_accepted': 'provider_accepted',
                'provider_rejected': 'failed', 'reconcile_required': 'unknown', 'abandoned': 'failed'}
PAGE_TEXT = {'not_ready': 'Report delivery is not ready.', 'pending': 'Report delivery is pending.',
             'sending': 'Report delivery is in progress.',
             'provider_accepted': 'Report prepared for BackBond review.',
             'failed': 'Report delivery failed. Contact support@backbond.ai.',
             'unknown': 'Report delivery status is unconfirmed.'}
REPORT_TEXT = {'awaiting_results': 'Report preparation is awaiting scan results.',
               'review': 'Your report is under BackBond review.',
               'changes_requested': 'Your report is in preparation.',
               'awaiting_ceo_approval': 'Your report is under BackBond review.',
               'approved': 'Report review is complete.'}


class FakeStatus:
    """GET ``/v1/starter-scans/{id}/status``: scripted bodies/statuses/exceptions, else the current row."""

    def __init__(self, *script):
        self.script, self.calls, self.current = list(script), [], {}

    def get(self, url, **kw):
        self.calls.append(dict(kw, url=url))
        step = self.script.pop(0) if self.script else None
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, Response):
            return step
        if step is not None:
            return Response(200, step)
        scan_id = url.rsplit('/', 2)[-2]
        return Response(200, self.current[scan_id]) if scan_id in self.current else Response(404, {'detail': 'x'})


def body(sid, result_id='res-1', report='review', delivery='not_ready', updated_at=UPDATED, **extra):
    return dict({'schema_version': 'starter_status_v1', 'bac_scan_id': sid, 'result_id': result_id,
                 'report_status': report, 'delivery_status': delivery, 'updated_at': updated_at}, **extra)


@pytest.fixture
def env(tmp_path):
    path = str(tmp_path / 'qa.sqlite')
    store = IntakeStore(path)
    store.put_code(Code('TEST', 'test', 'Test', 'standard', 50, 50, '2099-01-01'))
    queue = ScanQueue(store, lambda payload, n=itertools.count(1): {'scan_id': 'remote-%d' % next(n)})
    bac = BACIntake(path, base_url='', company_id='', owner_id='', token='', post=no_network, get=no_network)
    return store, queue, bac, OperationsOutbox(path), path


def acknowledged(store, queue, bac, outbox, ops=None):
    """A finished BAC-bound Starter scan whose Operations outbox row is acknowledged."""
    ops = ops or FakeOperations()
    sid = reserve(store)
    bind(bac, sid)
    finish(store, queue, sid)
    client = OperationsClient(BASE, TOKEN, ops.post)
    assert outbox.enqueue(bac, store, request_id=sid, scan_id=sid, client=client)
    while outbox.row(sid)['state'] != 'acknowledged':
        outbox.flush(client, now=10**9)
    return sid, outbox.row(sid)['operations_starter_id']


def client_for(fake):
    return OperationsClient(BASE, TOKEN, no_network, fake.get)


def test_status_request_contract_is_exact(env):
    store, queue, bac, outbox, _ = env
    sid, result_id = acknowledged(store, queue, bac, outbox)
    fake = FakeStatus(body(sid, result_id))
    assert outbox.poll_status(client_for(fake), now=1) == (sid, 'recorded')
    (call,) = fake.calls
    assert call['url'] == f'https://ops.invalid/v1/starter-scans/{sid}/status'
    assert call['headers'] == {'Authorization': 'Bearer ' + TOKEN}
    assert call['follow_redirects'] is False and 0 < call['timeout'] <= 20
    assert set(body(sid)) == STATUS_KEYS
    assert set(OPERATIONS_REPORT_STATUSES) == set(REPORT_MAP)
    assert set(OPERATIONS_DELIVERY_STATUSES) == set(DELIVERY_MAP)


@pytest.mark.parametrize('report', list(REPORT_MAP))
def test_every_report_status_maps_into_the_page_vocabulary(env, report):
    store, queue, bac, outbox, _ = env
    sid, result_id = acknowledged(store, queue, bac, outbox)
    outbox.poll_status(client_for(FakeStatus(body(sid, result_id, report=report))), now=1)
    value = outbox.public_lifecycle(sid)
    assert value == {'report_status': REPORT_MAP[report], 'delivery_status': 'not_ready', 'updated_at': UPDATED}
    assert value['report_status'] in report_release.REPORT_STATUSES and PUBLIC_REPORT_STATUS == REPORT_MAP


@pytest.mark.parametrize('delivery', list(DELIVERY_MAP))
def test_every_delivery_status_maps_into_the_page_vocabulary(env, delivery):
    store, queue, bac, outbox, _ = env
    sid, result_id = acknowledged(store, queue, bac, outbox)
    outbox.poll_status(client_for(FakeStatus(body(sid, result_id, report='approved', delivery=delivery))), now=1)
    value = outbox.public_lifecycle(sid)
    assert value == {'report_status': 'approved', 'delivery_status': DELIVERY_MAP[delivery], 'updated_at': UPDATED}
    assert value['delivery_status'] in report_release.DELIVERY_STATUSES and PUBLIC_DELIVERY_STATUS == DELIVERY_MAP
    assert value['delivery_status'] in PAGE_TEXT  # the page already renders every mapped value
    assert 'deliver' not in value['delivery_status'] and 'inbox' not in value['delivery_status']


def test_updated_at_is_normalized_to_utc(env):
    store, queue, bac, outbox, _ = env
    sid, result_id = acknowledged(store, queue, bac, outbox)
    outbox.poll_status(client_for(FakeStatus(body(sid, result_id, updated_at='2026-10-20T12:00:00+02:00'))), now=1)
    assert outbox.public_lifecycle(sid)['updated_at'] == UPDATED


def _bad_bodies(sid, result_id):
    good = body(sid, result_id, report='approved', delivery='queued')
    without = dict(good); without.pop('updated_at')
    return {
        'extra_key': dict(good, recipient='info@backbond.ai'),
        'missing_key': without,
        'bad_schema': dict(good, schema_version='starter_status_v2'),
        'bad_report_enum': dict(good, report_status='delivered'),
        'bad_delivery_enum': dict(good, delivery_status='delivered'),
        'non_string_enum': dict(good, report_status=['approved']),
        'mismatched_bac_scan_id': dict(good, bac_scan_id='f' * 32),
        'mismatched_result_id': dict(good, result_id='res-other'),
        'naive_updated_at': dict(good, updated_at='2026-10-20T10:00:00'),
        'bad_updated_at': dict(good, updated_at='yesterday'),
        'not_an_object': [good],
        'null': None,
    }


@pytest.mark.parametrize('case', list(_bad_bodies('s', 'r')))
def test_strict_validation_rejects_and_keeps_last_good(env, case):
    store, queue, bac, outbox, _ = env
    sid, result_id = acknowledged(store, queue, bac, outbox)
    outbox.poll_status(client_for(FakeStatus(body(sid, result_id, report='review'))), now=1)
    last_good = outbox.public_lifecycle(sid)
    bad = _bad_bodies(sid, result_id)[case]
    assert parse_starter_status(bad, bac_scan_id=sid, result_id=result_id) is None
    fake = FakeStatus(Response(200, bad))
    # (the fake Response cannot carry a JSON null body; it reads as an unparseable body instead)
    assert outbox.poll_status(client_for(fake), now=2) == (sid, 'unreachable' if bad is None else 'invalid')
    assert len(fake.calls) == 1 and outbox.public_lifecycle(sid) == last_good
    assert last_good == {'report_status': 'review', 'delivery_status': 'not_ready', 'updated_at': UPDATED}


@pytest.mark.parametrize('failure', [TimeoutError('timed out'), ConnectionError('refused'), Response(404, {}),
                                     Response(500, {}), Response(302, {}), Response(200, None)])
def test_unreachable_or_unknown_keeps_last_good_and_never_invents(env, failure):
    store, queue, bac, outbox, _ = env
    sid, result_id = acknowledged(store, queue, bac, outbox)
    assert outbox.poll_status(client_for(FakeStatus(failure)), now=1)[1] in ('unreachable', 'unknown', 'ignored')
    assert outbox.public_lifecycle(sid) == {'report_status': 'awaiting_results', 'delivery_status': 'not_ready',
                                            'updated_at': None}  # no snapshot yet: pre-report state
    outbox.poll_status(client_for(FakeStatus(body(sid, result_id, report='approved', delivery='sending'))), now=2)
    last_good = outbox.public_lifecycle(sid)
    outbox.poll_status(client_for(FakeStatus(failure)), now=3)
    assert outbox.public_lifecycle(sid) == last_good == {'report_status': 'approved', 'delivery_status': 'sending',
                                                         'updated_at': UPDATED}


def test_snapshot_is_durable_across_restart(env):
    store, queue, bac, outbox, path = env
    sid, result_id = acknowledged(store, queue, bac, outbox)
    outbox.poll_status(client_for(FakeStatus(body(sid, result_id, report='approved', delivery='queued'))), now=1)
    reopened = OperationsOutbox(path)
    assert reopened.public_lifecycle(sid) == {'report_status': 'approved', 'delivery_status': 'pending',
                                              'updated_at': UPDATED}


def test_non_bac_unacknowledged_or_unconfigured_rows_are_never_polled(env):
    store, queue, bac, outbox, _ = env
    fake = FakeStatus()
    # Unbound (non-BAC) finished scan: never enqueued, so never polled.
    unbound = reserve(store); finish(store, queue, unbound)
    assert not outbox.enqueue(bac, store, request_id=unbound, scan_id=unbound,
                              client=OperationsClient(BASE, TOKEN, no_network))
    # Pending / uncertain / held rows: recorded but not acknowledged.
    rows = {}
    for state, ops in (('uncertain', FakeOperations(503)), ('held', FakeOperations(409)), ('pending', None)):
        sid = reserve(store); bind(bac, sid); finish(store, queue, sid)
        client = OperationsClient(BASE, TOKEN, (ops or FakeOperations()).post)
        outbox.enqueue(bac, store, request_id=sid, scan_id=sid, client=client)
        if ops:
            outbox.flush(client, now=0)
        rows[sid] = state
    assert {sid: outbox.row(sid)['state'] for sid in rows} == rows
    for now in range(5):
        assert outbox.poll_status(client_for(fake), now=now) is None
    assert fake.calls == []
    for sid in [unbound, *rows]:
        assert outbox.public_lifecycle(sid) is None  # the page keeps its intake-side source
    # An acknowledged row with an unconfigured / non-https / GET-less client is not polled either.
    sid, _ = acknowledged(store, queue, bac, outbox)
    for client in (OperationsClient('', TOKEN, no_network, fake.get), OperationsClient(BASE, '', no_network, fake.get),
                   OperationsClient('http://ops.invalid', TOKEN, no_network, fake.get),
                   OperationsClient(BASE, TOKEN, no_network)):
        assert outbox.poll_status(client, now=10) is None
    assert fake.calls == []


def test_one_row_per_tick_least_recently_polled_first_including_accepted_rows(env):
    store, queue, bac, outbox, _ = env
    ops = FakeOperations()
    a, ra = acknowledged(store, queue, bac, outbox, ops)
    b, rb = acknowledged(store, queue, bac, outbox, ops)
    c, rc = acknowledged(store, queue, bac, outbox, ops)
    fake = FakeStatus()
    fake.current = {a: body(a, ra), b: body(b, rb), c: body(c, rc)}
    polled = []
    for now in range(1, 7):
        before = len(fake.calls)
        polled.append(outbox.poll_status(client_for(fake), now=now)[0])
        assert len(fake.calls) == before + 1  # at most one GET per tick
    assert polled == sorted([a, b, c]) * 2  # round robin: nobody starves
    fake.current[b] = body(b, rb, report='approved', delivery='provider_accepted')
    while outbox.poll_status(client_for(fake), now=10)[0] != b:
        pass
    seen = {outbox.poll_status(client_for(fake), now=now)[0] for now in range(11, 17)}
    assert seen == {a, b, c}  # acceptance must not starve revision reconciliation
    assert outbox.public_lifecycle(b)['delivery_status'] == 'provider_accepted'


# --- app wiring -------------------------------------------------------------------

@pytest.fixture
def app_env(tmp_path, monkeypatch):
    os.environ.setdefault('QA_DB_PATH', str(Path(tempfile.mkdtemp()) / 'import.sqlite'))
    app = importlib.import_module('app')
    # These legacy lifecycle fixtures exercise the configured BAC branch.
    monkeypatch.setattr(app, '_bac_mode', lambda: 'configured')
    import httpx
    for name in ('post', 'get', 'Client'):
        monkeypatch.setattr(httpx, name, no_network)
    path = str(tmp_path / 'qa.sqlite')
    store = IntakeStore(path)
    store.put_code(Code('TEST', 'test', 'Test', 'standard', 50, 50, '2099-01-01'))
    queue = ScanQueue(store, lambda payload, n=itertools.count(1): {'scan_id': 'remote-%d' % next(n)})
    bac = BACIntake(path, base_url='', company_id='', owner_id='', token='', post=no_network, get=no_network)
    outbox = OperationsOutbox(path)
    from report_release import ReportRelease
    for name, value in (('store', store), ('queue', queue), ('bac', bac), ('operations', outbox),
                        ('release', ReportRelease(path, lead_actor_id=lambda: ''))):
        monkeypatch.setattr(app, name, value)
    ops, status = FakeOperations(), FakeStatus()
    monkeypatch.setattr(httpx, 'post', ops.post)
    monkeypatch.setattr(httpx, 'get', status.get)
    monkeypatch.setenv('QA_OPERATIONS_BASE_URL', BASE)
    monkeypatch.setenv('QA_OPERATIONS_TOKEN', TOKEN)
    return app, store, queue, bac, outbox, ops, status


def bound_scan(store, queue, bac):
    res = store.reserve(code='TEST', email=EMAIL, agent_label='Agent', endpoint_url='https://agent.example.test',
                        transport='rest')
    bind(bac, res['scan_id'])
    finish(store, queue, res['scan_id'])
    return res['scan_id'], res['status_token']


def page_status(app, token):
    response = TestClient(app.app).post('/scan/v1/scan-status', json={'status_token': token})
    assert response.status_code == 200
    return response


def test_worker_polls_status_and_page_shows_mapped_values_without_internal_fields(app_env):
    app, store, queue, bac, outbox, ops, status = app_env
    sid, token = bound_scan(store, queue, bac)
    app.worker_tick()  # enqueue + record in Operations, then the first status poll (404: unknown)
    row = outbox.row(sid)
    assert row['state'] == 'acknowledged' and [c['url'] for c in status.calls] == [
        f'https://ops.invalid/v1/starter-scans/{sid}/status']
    lifecycle = page_status(app, token).json()['lifecycle']
    assert (lifecycle['report_status'], lifecycle['delivery_status']) == ('awaiting_results', 'not_ready')
    result_id = row['operations_starter_id']
    status.current[sid] = body(sid, result_id, report='awaiting_lead_approval', delivery='not_ready')
    app.worker_tick()
    lifecycle = page_status(app, token).json()['lifecycle']
    assert (lifecycle['report_status'], lifecycle['delivery_status']) == ('awaiting_ceo_approval', 'not_ready')
    status.current[sid] = body(sid, result_id, report='approved', delivery='provider_outcome_unknown',
                               updated_at='2026-10-20T11:00:00+00:00')
    app.worker_tick()
    response = page_status(app, token)
    lifecycle = response.json()['lifecycle']
    assert (lifecycle['report_status'], lifecycle['delivery_status']) == ('approved', 'pending')
    assert lifecycle['updated_at'] >= '2026-10-20T11:00:00+00:00'
    assert set(lifecycle) == {'version', 'intake_status', 'report_status', 'delivery_status', 'updated_at'}
    raw = response.text
    for secret in (sid, result_id, TOKEN, EMAIL, 'info@backbond.ai', 'starter_status_v1', 'bac_scan_id',
                   'result_id', 'awaiting_lead_approval', 'provider_outcome_unknown', 'ops.invalid',
                   'delivered', 'inbox'):
        assert secret not in raw, secret
    # Operations unreachable: the page keeps the last good snapshot.
    status.script = [TimeoutError('down'), Response(500, {})]
    app.worker_tick(); app.worker_tick()
    assert page_status(app, token).json()['lifecycle'] == lifecycle
    assert [c['url'] for c in ops.calls] == ['https://ops.invalid/v1/starter-scans']  # recorded once, no mail


def test_unbound_scan_page_status_never_reads_operations(app_env):
    app, store, queue, bac, outbox, ops, status = app_env
    res = store.reserve(code='TEST', email=EMAIL, agent_label='Agent', endpoint_url='https://agent.example.test',
                        transport='rest')
    finish(store, queue, res['scan_id'])
    for _ in range(3):
        app.worker_tick()
    assert ops.calls == [] and status.calls == []
    lifecycle = page_status(app, res['status_token']).json()['lifecycle']
    assert (lifecycle['report_status'], lifecycle['delivery_status']) == ('awaiting_results', 'not_ready')


@pytest.mark.parametrize('report,delivery', [('approved', 'provider_accepted'), ('approved', 'provider_rejected'),
                                             ('approved', 'queued'), ('approved', 'reconcile_required'),
                                             ('changes_requested', 'not_ready'), ('awaiting_report', 'not_ready')])
def test_operations_status_renders_in_starter_page_fixture(app_env, tmp_path, report, delivery):
    """Actual /scan/v1/scan-status response built from an Operations snapshot -> page-owner consumer."""
    app, store, queue, bac, outbox, ops, status = app_env
    sid, token = bound_scan(store, queue, bac)
    app.worker_tick()
    status.current[sid] = body(sid, outbox.row(sid)['operations_starter_id'], report=report, delivery=delivery)
    app.worker_tick()
    response = page_status(app, token).json()
    expected = [REPORT_TEXT[REPORT_MAP[report]], PAGE_TEXT[DELIVERY_MAP[delivery]]]
    fixture = tmp_path / 'public-response.json'
    fixture.write_text(json.dumps(dict(response=response, expected_text=expected)))
    consumer = Path(__file__).parent / 'fixtures' / 'starter-page-v1'
    result = subprocess.run(['node', str(consumer / 'verify.cjs'), str(consumer / 'scan.js'), str(fixture)],
                            check=True, text=True, capture_output=True)
    assert 'supplied backend response rendered by actual page script' in result.stdout


@pytest.mark.parametrize('report,delivery', [('changes_requested', 'not_ready'),
    ('approved', 'provider_rejected'), ('approved', 'provider_accepted')])
def test_accepted_revision_superseded_after_restart(env, report, delivery):
    store, queue, bac, outbox, path = env
    ops = FakeOperations()
    sid, rid = acknowledged(store, queue, bac, outbox, ops)
    fake = FakeStatus(body(sid, rid, report='approved', delivery='provider_accepted'))
    client = client_for(fake)  # POST raises: reconciliation must never resend
    assert outbox.poll_status(client, now=1) == (sid, 'recorded')
    outbox = OperationsOutbox(path)
    fake.current[sid] = body(sid, rid, report='review', delivery='not_ready')
    assert outbox.poll_status(client, now=2) == (sid, 'recorded')
    assert outbox.public_lifecycle(sid)['report_status'] == 'review'
    assert outbox.public_lifecycle(sid)['delivery_status'] == 'not_ready'
    outbox = OperationsOutbox(path)
    fake.current[sid] = body(sid, rid, report=report, delivery=delivery)
    assert outbox.poll_status(client, now=3) == (sid, 'recorded')
    assert outbox.public_lifecycle(sid)['report_status'] == REPORT_MAP[report]
    assert outbox.public_lifecycle(sid)['delivery_status'] == DELIVERY_MAP[delivery]
    assert outbox.flush(client, now=10**9) == []
    assert len(ops.calls) == 1 and len(fake.calls) == 3


def test_stale_local_roles_cannot_compete_with_operations(app_env, monkeypatch):
    import hashlib
    app, store, queue, bac, outbox, ops, status = app_env
    sid, token = bound_scan(store, queue, bac)
    for role in ('ANALYST', 'REVIEWER', 'LEAD'):
        monkeypatch.setenv('QA_RELEASE_' + role + '_TOKEN_SHA256', hashlib.sha256(role.encode()).hexdigest())
        monkeypatch.setenv('QA_RELEASE_' + role + '_ACTOR_ID', role.lower())
    # A stale approved local revision must never be consulted, including before an ack.
    def forbidden(*args, **kwargs):
        pytest.fail('local final report authority was consulted')
    monkeypatch.setattr(app.release, 'public_lifecycle', forbidden)
    monkeypatch.setattr(app.release, 'released', forbidden)
    monkeypatch.setattr(app.bac, 'report_decision', forbidden)
    client = TestClient(app.app)
    authoring = {'request_id': sid, 'case_id': 'case', 'evidence_sha256': 'a' * 64,
                 'summary': 'Synthetic report', 'recommendations': []}
    calls = [(f'/scan/v1/bac/scans/{sid}/revisions', authoring, 'ANALYST')]
    for route, role in [('review', 'REVIEWER'), ('decision', 'LEAD')]:
        calls.append((f'/scan/v1/bac/revisions/rev_{"b" * 32}/{route}',
                      {'revision_sha256': 'a' * 64, 'nonce': 'n' * 16, 'decision': 'approve'}, role))
    for route, payload, role in calls:
        for credential in (role, 'wrong', ''):
            headers = {'Authorization': 'Bearer ' + credential} if credential else {}
            response = client.post(route, json=payload, headers=headers)
            assert response.status_code == 410
    assert app.release.db.execute('SELECT count(*) FROM release_revisions').fetchone()[0] == 0
    assert outbox.row(sid) is None
    lifecycle = page_status(app, token).json()['lifecycle']
    assert (lifecycle['report_status'], lifecycle['delivery_status']) == ('awaiting_results', 'not_ready')
    report_token = store.scan(sid)['report_token']
    assert app._report_access(report_token) is None
    app.worker_tick()  # result handoff/poll remain active; no second native decision chain
    assert outbox.row(sid)['state'] == 'acknowledged'
    assert app._report_access(report_token) is None


def test_public_metadata_requires_persisted_scope_and_queue_ack(app_env):
    app, store, queue, bac, outbox, ops, status = app_env
    res = store.reserve(code='TEST', email=EMAIL, agent_label='Agent',
                        endpoint_url='https://agent.example.test', transport='rest')
    sid, token = res['scan_id'], res['status_token']
    queue.defer_bac(sid, sid, {})
    pending = page_status(app, token).json()
    assert pending['queue_acknowledged'] is False
    assert pending['cancel_available'] is False
    assert pending['accepted_scope_version'] is None
    assert pending['last_confirmed_progress_at'] is None
    assert sid not in pending['request_reference'] and token not in str(pending)
    bind(bac, sid)
    payload = json.dumps({'description': json.dumps({'scope_ref': 'starter-100@4'})})
    bac.db.execute('UPDATE bac_intake SET payload=? WHERE request_id=?', (payload, sid))
    # BAC acknowledgement alone still does not prove admission to the execution queue.
    acknowledged = page_status(app, token).json()
    assert acknowledged['accepted_scope_version'] == 'starter-100@4'
    assert acknowledged['queue_acknowledged'] is False
    assert acknowledged['request_reference'] == pending['request_reference']
    queue.awaiting_bac.pop(sid)
    queue.pending[sid] = ({}, 0)
    queued = page_status(app, token).json()
    assert queued['queue_acknowledged'] is True and queued['cancel_available'] is True
