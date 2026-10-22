import sys
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))
from bac_intake import BACIntake, BACUnconfirmed

REFS = dict(customer_ref='customer-1', agent_ref='agent-1', scope_ref='starter-100@4', code_ref='code-record-1')

class Response:
    status_code = 201
    def json(self):
        return dict(id='case-1', companyId='bac', assigneeAgentId='owner')

def client(tmp_path, post, token='injected'):
    return BACIntake(str(tmp_path / 'intake.sqlite'), base_url='https://bac.invalid',
                     company_id='bac', owner_id='owner', token=token, post=post)

def test_ack_survives_restart_and_replay(tmp_path):
    calls = []
    def post(route, **kw):
        calls.append((route, kw)); return Response()
    assert client(tmp_path, post).acknowledge('stable', **REFS) == 'case-1'
    assert client(tmp_path, post).acknowledge('stable', **REFS) == 'case-1'
    assert len(calls) == 1
    assert calls[0][0] == 'https://bac.invalid/api/companies/bac/issues'
    assert calls[0][1]['headers'] == {'Authorization': 'Bearer injected'}

@pytest.mark.parametrize('mode', ['timeout', '401', '403', 'malformed', 'wrong-owner'])
def test_no_ack_no_dispatch_or_second_create(tmp_path, mode):
    calls = []
    def post(*args, **kw):
        calls.append(1)
        if mode == 'timeout': raise TimeoutError()
        r = Response()
        if mode in ('401', '403'): r.status_code = int(mode)
        if mode == 'malformed': r.json = lambda: {}
        if mode == 'wrong-owner': r.json = lambda: dict(id='case', companyId='bac', assigneeAgentId='other')
        return r
    c = client(tmp_path, post)
    dispatched = []
    for service in (c, client(tmp_path, post)):
        with pytest.raises(BACUnconfirmed):
            service.acknowledge('stable', **REFS)
            dispatched.append(True)
    assert not dispatched and len(calls) == 1

def test_missing_auth_never_calls_bac(tmp_path):
    c = client(tmp_path, lambda *a, **k: pytest.fail('network call'), token='')
    with pytest.raises(BACUnconfirmed): c.acknowledge('stable', **REFS)

def test_concurrent_replay_creates_at_most_once(tmp_path):
    calls = []
    def post(*a, **k): calls.append(1); return Response()
    c = client(tmp_path, post)
    def attempt(_):
        try: return c.acknowledge('stable', **REFS)
        except BACUnconfirmed: return None
    with ThreadPoolExecutor(max_workers=4) as pool: results = list(pool.map(attempt, range(8)))
    assert len(calls) == 1 and 'case-1' in results
    with pytest.raises(BACUnconfirmed): c.acknowledge('stable', **dict(REFS, scope_ref='different'))


def test_lost_acceptance_reconciles_without_repost(tmp_path):
    accepted = []
    def post(route, **kw):
        assert route == 'https://bac.invalid/api/companies/bac/issues'
        assert kw['headers'] == {'Authorization': 'Bearer injected'}
        accepted.append(dict(kw['json'], id='case-1', companyId='bac'))
        raise TimeoutError()
    def get(route, **kw):
        assert route == 'https://bac.invalid/api/companies/bac/issues?q=stable'
        assert kw['headers'] == {'Authorization': 'Bearer injected'}
        r = Response(); r.status_code = 200; r.json = lambda: accepted
        return r
    c = client(tmp_path, post)
    with pytest.raises(BACUnconfirmed): c.acknowledge('stable', **REFS)
    c = client(tmp_path, lambda *a, **k: pytest.fail('re-POST'))
    c.get = get
    assert c.reconcile('stable') == 'case-1'
    assert c.acknowledge('stable', **REFS) == 'case-1'
    assert len(accepted) == 1

@pytest.mark.parametrize('records', [[], [{"id": "foreign", "companyId": "other"}], [{}, {}]])
def test_reconcile_requires_unique_matching_case(tmp_path, records):
    c = client(tmp_path, lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(BACUnconfirmed): c.acknowledge('stable', **REFS)
    r = Response(); r.status_code = 200; r.json = lambda: records
    c.get = lambda *a, **k: r
    with pytest.raises(BACUnconfirmed): c.reconcile('stable')


def test_start_scan_no_submit_on_unconfirmed():
    # Execute the actual route body with deterministic service boundaries.
    import ast
    from types import SimpleNamespace as NS
    source = Path(__file__).parents[1] / 'server' / 'app.py'
    tree = ast.parse(source.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'start_scan')
    fn.decorator_list = []; fn.returns = None
    for arg in fn.args.args: arg.annotation = None
    class HTTPError(Exception):
        def __init__(self, status, detail): self.status_code = status
    submitted = []
    cancelled = []
    retained = []
    def unconfirmed(*a, **k): raise BACUnconfirmed()
    clean = dict(code='private', email='x', agent_label='a', endpoint_url='private',
                 transport='http', model=None, connection_proof=None, auth='secret')
    body = NS(code='private', email='x', connection_proof=None, terms_version='v1',
              consent_improvement=False, pack_id='starter-100@4', model_dump=lambda: dict(clean))
    env = dict(controlled_operator=None, _controlled_claim=lambda *a: None, _bac_mode=lambda:"configured", _flag=lambda *a: True, _private_acceptance=lambda *a: False,
               _mail_configured=lambda: True, WEBHOOK_SECRET='fixture',
               os=NS(environ={'QA_READY':'1'}), _terms_version=lambda:'v1',
               _resolve_code=lambda c:dict(public=False,code=c,partner='p'),
               proofs=NS(redeem=lambda *a:True), _payload=lambda c:dict(auth='secret'),
               store=NS(reserve=lambda **k:dict(scan_id='stable',status_token='status'),
                        cancel_unsent=cancelled.append),
               bac=NS(acknowledge=unconfirmed, retry_identity=lambda scan, fields: scan), queue=NS(submit=lambda *a:submitted.append(a),
                        defer_bac=lambda *a: retained.append(a)),
               BACUnconfirmed=BACUnconfirmed, HTTPException=HTTPError,
               IntakeError=type('IntakeError',(Exception,),{}))
    exec(compile(ast.Module(body=[fn],type_ignores=[]), str(source), 'exec'),env)
    assert env['start_scan'](body, NS()) == dict(status='queued', status_token='status')
    assert submitted == [] and cancelled == []
    assert retained == [('stable', 'stable', {'auth': 'secret'})]


def test_cancelled_lost_201_retry_reuses_case_after_restart(tmp_path):
    accepted = []
    def post(route, **kw):
        accepted.append(dict(kw['json'], id='case-1', companyId='bac'))
        raise TimeoutError('lost 201')
    c = client(tmp_path, post)
    c.db.execute('CREATE TABLE scans (id TEXT PRIMARY KEY, status TEXT)')
    c.db.execute("INSERT INTO scans VALUES ('attempt-1','reserved')")
    fields = dict(email='Customer@Example.com', endpoint_url='https://private.example/path',
                  pack_id='starter-100@4', auth={'value':'secret'}, connection_proof='one')
    identity = c.retry_identity('attempt-1', fields)
    with pytest.raises(BACUnconfirmed): c.acknowledge(identity, **REFS)
    c.db.execute("UPDATE scans SET status='cancelled' WHERE id='attempt-1'")
    c.db.close()
    c = client(tmp_path, lambda *a, **k: pytest.fail('duplicate create'))
    c.db.execute("INSERT INTO scans VALUES ('attempt-2','reserved')")
    retry = dict(fields, email='customer@example.com', auth={'value':'refreshed'}, connection_proof='two')
    assert c.retry_identity('attempt-2', retry) == identity
    def get(route, **kw):
        assert route.endswith('/issues?q=attempt-1')
        assert kw['headers'] == {'Authorization': 'Bearer injected'}
        r = Response(); r.status_code = 200; r.json = lambda: accepted
        return r
    c.get = get
    assert c.acknowledge(identity, **REFS) == 'case-1'
    assert len(accepted) == 1
    stored = c.db.execute('SELECT fingerprint FROM bac_retry_identity').fetchone()[0]
    assert len(stored) == 64 and 'private' not in stored
    # A completed scan and a different scope each require a new case.
    c.db.execute("UPDATE scans SET status='finished' WHERE id='attempt-2'")
    assert c.retry_identity('attempt-3', retry) == 'attempt-3'
    assert c.retry_identity('attempt-4', dict(retry, pack_id='other')) == 'attempt-4'


def test_scheduled_orphan_reconciliation_survives_restart(tmp_path):
    accepted = []
    def post(route, **kw):
        accepted.append(dict(kw['json'], id='case-1', companyId='bac'))
        raise TimeoutError()
    c = client(tmp_path, post)
    with pytest.raises(BACUnconfirmed): c.acknowledge('orphan', **REFS)
    c.db.close()
    c = client(tmp_path, lambda *a, **k: pytest.fail('duplicate create'))
    calls = []
    def get(route, **kw):
        calls.append(route)
        assert kw['headers'] == {'Authorization': 'Bearer injected'}
        assert kw['follow_redirects'] is False
        r = Response(); r.status_code = 200; r.json = lambda: accepted
        return r
    c.get = get
    assert c.reconcile_pending(now=100) == [('orphan', 'case-1')]
    assert c.reconcile_pending(now=200) == []
    assert len(calls) == 1 and len(accepted) == 1


def test_scheduled_reconcile_backoff_and_batch_bound(tmp_path):
    c = client(tmp_path, lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    for request in ('a', 'b'):
        with pytest.raises(BACUnconfirmed): c.acknowledge(request, **REFS)
    calls = []
    def get(route, **kw):
        calls.append(route)
        raise TimeoutError()
    c.get = get
    assert c.reconcile_pending(now=100, limit=1) == []
    assert len(calls) == 1
    c.db.close()
    c = client(tmp_path, lambda *a, **k: pytest.fail('duplicate create'))
    c.get = get
    c.reconcile_pending(now=100)
    assert len(calls) == 2  # a is delayed; b still gets a turn
    c.reconcile_pending(now=109)
    assert len(calls) == 2
    c.reconcile_pending(now=110)
    assert len(calls) == 4
    c.reconcile_pending(now=129)
    assert len(calls) == 4
    c.reconcile_pending(now=130)
    assert len(calls) == 6


class RestrictedBAC:
    """Service gateway fixture: deny every route/body outside BAC_IDENTITY."""
    def __init__(self):
        self.records = []
        self.lose_response = False
        self.comments = []
        self.lose_comment = False

    def request(self, method, route, **kw):
        assert kw.pop('headers') == {'Authorization': 'Bearer injected'}
        assert kw.pop('timeout') == 20
        assert kw.pop('follow_redirects') is False
        comment_route = 'https://bac.invalid/api/issues/case-1/comments'
        if route == comment_route:
            assert self.records  # Only a service-created case is accessible.
            if method == 'POST':
                payload = kw.pop('json')
                assert not kw and set(payload) == {'body'}
                event = json.loads(payload['body'])
                assert set(event) - {'snapshot'} == {'event_id', 'request_id', 'case_id', 'scan_id', 'type',
                                      'revision_sha256', 'reference', 'occurred_at'}
                if 'snapshot' in event:
                    snapshot = event['snapshot']
                    assert set(snapshot) == {'status', 'outcome', 'progress', 'reason_code', 'observed_at'}
                    assert set(snapshot['progress']) <= {'state', 'phase', 'processed_count',
                        'accepted_pack_total', 'usable_count', 'unavailable_count',
                        'heartbeat_at', 'updated_at', 'last_activity_at'}
                assert event['case_id'] == 'case-1'
                assert event['request_id'] in {json.loads(r['description'])['request_id'] for r in self.records}
                assert event['type'] in {'lifecycle', 'evidence', 'report_revision', 'send_receipt'}
                record = dict(payload, id='comment-' + str(len(self.comments)), issueId='case-1')
                self.comments.append(record)
                if self.lose_comment:
                    raise TimeoutError('lost append response')
                r = Response(); r.json = lambda: record
                return r
            assert method == 'GET' and not kw
            r = Response(); r.status_code = 200; r.json = lambda: list(self.comments)
            return r
        base = 'https://bac.invalid/api/companies/bac/issues'
        if method == 'POST':
            assert route == base
            payload = kw.pop('json')
            assert not kw
            assert set(payload) == {'title', 'description', 'status', 'priority', 'assigneeAgentId'}
            refs = json.loads(payload['description'])
            assert set(refs) == {'request_id', 'customer_ref', 'agent_ref', 'scope_ref', 'code_ref'}
            assert all(isinstance(v, str) and v for v in refs.values())
            assert payload['title'] == 'Starter request ' + refs['request_id']
            assert payload['status'] == 'todo' and payload['priority'] == 'medium'
            assert payload['assigneeAgentId'] == 'owner'
            self.records.append(dict(payload, id='case-1', companyId='bac'))
            if self.lose_response:
                raise TimeoutError('lost acknowledgment')
            return Response()
        assert method == 'GET'
        assert not kw
        from urllib.parse import quote
        assert route in {base + '?q=' + quote(json.loads(v['description'])['request_id'], safe='')
                         for v in self.records}
        r = Response(); r.status_code = 200; r.json = lambda: list(self.records)
        return r


def test_restrictive_gateway_contract_create_and_scheduled_recovery(tmp_path):
    gateway = RestrictedBAC()
    gateway.lose_response = True
    c = client(tmp_path, lambda route, **kw: gateway.request('POST', route, **kw))
    with pytest.raises(BACUnconfirmed):
        c.acknowledge('request /?&', **REFS)
    c.db.close()
    c = client(tmp_path, lambda *a, **k: pytest.fail('duplicate create'))
    c.get = lambda route, **kw: gateway.request('GET', route, **kw)
    assert c.reconcile_pending(now=100) == [('request /?&', 'case-1')]
    assert c.acknowledge('request /?&', **REFS) == 'case-1'
    assert len(gateway.records) == 1


@pytest.mark.parametrize('method,route', [
    ('POST', 'https://bac.invalid/api/issues/case-1/comments'),
    ('PATCH', 'https://bac.invalid/api/issues/case-1'),
    ('GET', 'https://bac.invalid/api/companies/other/issues?q=request'),
    ('GET', 'https://bac.invalid/api/companies/bac/issues'),
])
def test_restrictive_gateway_denies_unapproved_routes(method, route):
    with pytest.raises(AssertionError):
        RestrictedBAC().request(method, route, headers={'Authorization': 'Bearer injected'},
                                timeout=20, follow_redirects=False)


@pytest.mark.parametrize('extra', [{'endpoint_url': 'private'}, {'auth': 'secret'},
                                  {'email': 'private@example.com'}])
def test_restrictive_gateway_denies_extra_reference_fields(extra):
    payload = dict(title='Starter request r',
                   description=json.dumps(dict(REFS, request_id='r', **extra)),
                   status='todo', priority='medium', assigneeAgentId='owner')
    gateway = RestrictedBAC()
    with pytest.raises(AssertionError):
        gateway.request('POST', 'https://bac.invalid/api/companies/bac/issues',
                        json=payload, headers={'Authorization': 'Bearer injected'},
                        timeout=20, follow_redirects=False)
    assert gateway.records == []


@pytest.mark.parametrize('ending', ['recover', 'expire', 'cancel', 'close', 'unknown'])
def test_original_reservation_ephemeral_recovery(tmp_path, ending):
    import sqlite3
    from types import SimpleNamespace as NS
    from qa_runtime import ScanQueue
    # Minimal durable store exercises the production queue and BAC boundary.
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE scans (id TEXT PRIMARY KEY, status TEXT)')
    def state(scan_id, status):
        db.execute('UPDATE scans SET status=? WHERE id=?', (status, scan_id))
    store = NS(db=db, queued=lambda s:state(s,'queued'),
               starting=lambda s:state(s,'starting'), running=lambda s:state(s,'running'),
               interrupted=lambda s:state(s,'interrupted'),
               cancel_unsent=lambda s:state(s,'cancelled') or True,
               dispatch_unknown=lambda s:state(s,'dispatch_unknown'),
               scan=lambda s:db.execute('SELECT * FROM scans WHERE id=?',(s,)).fetchone(),
               record_provenance=lambda *a:None, bind_scanner=lambda *a:None)
    launches = []
    def start(payload):
        launches.append(dict(payload))
        if ending == 'unknown':
            raise TimeoutError()
        return {'scan_id':'remote-original'}
    q = ScanQueue(store, start, pending_ttl=10)
    db.execute("INSERT INTO scans VALUES ('original','reserved')")
    accepted = []
    def post(route, **kw):
        accepted.append(dict(kw['json'],id='case-1',companyId='bac'))
        raise TimeoutError()
    c = client(tmp_path, post)
    c.get = lambda *a, **k:NS(status_code=200, json=lambda:accepted)
    with pytest.raises(BACUnconfirmed): c.acknowledge('original', **REFS)
    payload = {'auth':'ephemeral-secret', 'system_prompt':'private-prompt'}
    q.defer_bac('original','original',payload,now=100)
    q.recover_bac(c,now=101)
    assert not launches and payload['auth'] == 'ephemeral-secret'
    if ending == 'expire': q.expire_pending(now=110)
    elif ending == 'cancel': q.cancel('original')
    elif ending == 'close': q.close()
    c.reconcile_pending(now=100)
    q.recover_bac(c,now=102)
    q.recover_bac(c,now=103)
    assert len(accepted) == 1
    assert len(launches) == int(ending in {'recover','unknown'})
    assert payload == {} and not q.awaiting_bac
    expected = {'recover':'running', 'unknown':'dispatch_unknown',
                'cancel':'cancelled', 'expire':'interrupted', 'close':'interrupted'}[ending]
    assert store.scan('original')['status'] == expected
    assert 'ephemeral-secret' not in c.db.execute('SELECT payload FROM bac_intake').fetchone()[0]


def test_recovery_capacity_and_restart_never_reconstruct_credentials(tmp_path):
    from qa_runtime import ScanQueue, QueueFull
    from qa_service import IntakeStore, Code
    path = str(tmp_path / 'scans.sqlite')
    store = IntakeStore(path)
    store.put_code(Code('TEST', 'test', 'Test', 'standard', 5, 5, '2099-01-01'))
    q = ScanQueue(store, lambda p:pytest.fail('must not dispatch'), max_pending=1)
    def reserve(email):
        return store.reserve(code='TEST', email=email, agent_label='Agent',
                             endpoint_url='https://example.test', transport='rest')['scan_id']
    first, second = reserve('a@example.test'), reserve('b@example.test')
    secret = {'auth':'memory-only'}
    q.defer_bac(first, first, secret)
    with pytest.raises(QueueFull): q.defer_bac(second, second, {'auth':'other'})
    restarted = ScanQueue(IntakeStore(path), lambda p:pytest.fail('must not dispatch'))
    assert restarted.store.scan(first)['status'] == 'interrupted'
    assert not restarted.awaiting_bac and not restarted.pending
    q.close()
    assert secret == {}


def event_client(tmp_path, gateway):
    c = client(tmp_path, lambda route, **kw: gateway.request('POST', route, **kw))
    c.get = lambda route, **kw: gateway.request('GET', route, **kw)
    return c


def test_event_lost_response_restart_and_concurrency(tmp_path):
    gateway = RestrictedBAC()
    c = event_client(tmp_path, gateway)
    c.acknowledge('stable', **REFS)
    def enqueue(_):
        return c.enqueue_event('stable', scan_id='local', event_type='evidence',
                               revision_sha256='a'*64, reference='intake:local')
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(set(pool.map(enqueue, range(8)))) == 1
    gateway.lose_comment = True
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: c.flush_events(now=100), range(8)))
    assert len(gateway.comments) == 1
    c.db.close()
    c = event_client(tmp_path, gateway)
    c.flush_events(now=110)
    assert c.db.execute('SELECT state FROM bac_events').fetchone()[0] == 'acknowledged'
    c.flush_events(now=200)
    assert len(gateway.comments) == 1


@pytest.mark.parametrize('remote', ['empty', 'duplicate', 'foreign'])
def test_uncertain_event_never_reposts(tmp_path, remote):
    gateway = RestrictedBAC(); c = event_client(tmp_path, gateway)
    c.acknowledge('stable', **REFS)
    c.enqueue_event('stable', scan_id='local', event_type='lifecycle',
                    revision_sha256='b'*64, reference='state:running')
    gateway.lose_comment = True
    c.flush_events(now=100)
    accepted = gateway.comments[0]
    gateway.comments = [] if remote == 'empty' else [accepted, accepted] if remote == 'duplicate' else [dict(accepted, issueId='other')]
    for now in (110, 130, 200, 1000): c.flush_events(now=now)
    assert c.db.execute('SELECT state FROM bac_events').fetchone()[0] == 'uncertain'
    assert len(gateway.comments) == {'empty':0, 'duplicate':2, 'foreign':1}[remote]


def test_missed_terminal_callback_recovery_and_opaque_evidence(tmp_path):
    from qa_runtime import ScanQueue
    from qa_service import IntakeStore, Code
    gateway = RestrictedBAC(); c = event_client(tmp_path, gateway)
    store = IntakeStore(str(tmp_path / 'scans.sqlite'))
    store.put_code(Code('TEST', 'test', 'Test', 'standard', 5, 5, '2099-01-01'))
    sid = store.reserve(code='TEST', email='private@example.test', agent_label='Private',
                        endpoint_url='https://private.test', transport='rest')['scan_id']
    queue = ScanQueue(store, lambda payload: {'scan_id':'remote'})
    # Reservation is intentionally made after queue startup recovery.
    store.db.execute("UPDATE scans SET status='reserved' WHERE id=?", (sid,))
    request = c.retry_identity(sid, dict(email='private@example.test'))
    c.acknowledge(request, **REFS)
    queue.submit(sid, {})
    bad = dict(scan_id='foreign', status='failed')
    c.capture_scans(store, queue, lambda row: bad, now=100)
    assert store.scan(sid)['status'] == 'running'
    value = dict(scan_id='remote', status='failed', incomplete=True,
                 incomplete_reason='scanner_restart_resume_exhausted', result_rows=[])
    c.capture_scans(store, queue, lambda row: value, now=130)
    c.capture_scans(store, queue, lambda row: pytest.fail('already terminal'), now=160)
    assert store.scan(sid)['status'] == 'finished'
    assert store.scan(sid)['outcome'] == 'failed'
    assert sid not in queue.active
    events = [json.loads(r[0]) for r in c.db.execute('SELECT payload FROM bac_events')]
    assert len(events) == 3  # running, failed, one evidence revision
    assert sum(e['type'] == 'evidence' for e in events) == 1
    assert all(e['scan_id'] == sid and e['case_id'] == 'case-1' for e in events)
    assert 'private' not in json.dumps(events)


@pytest.mark.parametrize('reference', ['https://private.test', 'private@example.test/path', 'raw prompt'])
def test_outbox_rejects_nonopaque_inputs(tmp_path, reference):
    gateway = RestrictedBAC(); c = event_client(tmp_path, gateway)
    c.acknowledge('stable', **REFS)
    with pytest.raises(BACUnconfirmed):
        c.enqueue_event('stable', scan_id='local', event_type='evidence',
                        revision_sha256='a'*64, reference=reference)


@pytest.mark.parametrize('terminal_status', ['complete', 'completed'])
def test_missed_successful_callback_uses_scanner_terminal_status(tmp_path, terminal_status):
    from qa_runtime import ScanQueue
    from qa_service import IntakeStore, Code
    gateway = RestrictedBAC()
    c = event_client(tmp_path, gateway)
    store = IntakeStore(str(tmp_path / 'scans.sqlite'))
    store.put_code(Code('TEST', 'test', 'Test', 'standard', 5, 5, '2099-01-01'))
    queue = ScanQueue(store, lambda payload: {'scan_id': 'remote'})
    sid = store.reserve(code='TEST', email='private@example.test', agent_label='Private',
                        endpoint_url='https://private.test', transport='rest')['scan_id']
    request = c.retry_identity(sid, dict(email='private@example.test'))
    c.acknowledge(request, **REFS)
    queue.submit(sid, {})
    value = dict(scan_id='remote', status=terminal_status, partial_completion=False,
                 pack_id='starter-100@4', result_rows=[
                     dict(test_id=f'case-{n}', family='f', prompt='test', reply='answer',
                          verdict='pass', pack_id='starter-100@4') for n in range(100)])
    c.capture_scans(store, queue, lambda row: value, now=100)
    assert store.scan(sid)['status'] == 'finished'
    assert store.scan(sid)['outcome'] == 'complete'
    assert sid not in queue.active
    c.capture_scans(store, queue, lambda row: pytest.fail('already terminal'), now=130)
    events = [json.loads(row[0]) for row in c.db.execute('SELECT payload FROM bac_events')]
    assert sum(event['type'] == 'evidence' for event in events) == 1


def running_fixture(tmp_path):
    from qa_service import IntakeStore, Code
    from qa_runtime import ScanQueue
    gateway = RestrictedBAC()
    c = event_client(tmp_path, gateway)
    store = IntakeStore(str(tmp_path / 'scans.sqlite'))
    store.put_code(Code('TEST', 'test', 'Test', 'standard', 5, 5, '2099-01-01'))
    queue = ScanQueue(store, lambda payload: {'scan_id': 'remote'})
    sid = store.reserve(code='TEST', email='private@example.test', agent_label='Private',
                        endpoint_url='https://private.test', transport='rest')['scan_id']
    c.retry_identity(sid, dict(email='private@example.test'))
    c.acknowledge(sid, **REFS)
    queue.submit(sid, {})
    return c, store, queue, sid


@pytest.mark.parametrize('status', ['incomplete', 'partial', 'complete'])
@pytest.mark.parametrize('rows', [[], [dict(test_id='one', verdict='pass', prompt='private')]])
def test_partial_terminal_contract_with_and_without_rows(tmp_path, status, rows):
    c, store, queue, sid = running_fixture(tmp_path)
    value = dict(scan_id='remote', status=status, partial_completion=True, result_rows=rows)
    c.capture_scans(store, queue, lambda _: value, now=100)
    expected = bool(rows)  # zero-row partial is not the released callback schema
    assert (store.scan(sid)['status'] == 'finished') == expected
    if expected:
        assert store.scan(sid)['outcome'] == 'incomplete'


def test_progress_changes_restart_staleness_and_privacy(tmp_path):
    c, store, queue, sid = running_fixture(tmp_path)
    def progress(count):
        return dict(state='running', processed_count=count, accepted_pack_total=50,
                    usable_count=count-1, unavailable_count=1,
                    heartbeat_at='1970-01-01T00:01:40+00:00',
                    auth='private', prompt='private', reason='private', timing={'secret': 'private'})
    for now, count in ((100, 4), (130, 4), (160, 5), (250, 5)):
        c.capture_scans(store, queue, now=now, fetch_progress=lambda _: progress(count))
    assert c.confirmed_progress_at(sid) == '1970-01-01T00:02:40+00:00'
    events = [json.loads(r[0]) for r in c.db.execute('SELECT payload FROM bac_events')]
    assert len(events) == 3  # same observation deduplicates; progress and stale change persist
    assert 'private' not in json.dumps(events)
    c.flush_events(now=300, limit=10)
    assert c.db.execute("SELECT COUNT(*) FROM bac_events WHERE state='acknowledged'").fetchone()[0] == 3
    assert events[-1]['snapshot']['reason_code'] == 'stale_progress'
    assert events[-1]['snapshot']['progress']['accepted_pack_total'] == 50
    c.db.close()
    c = event_client(tmp_path, RestrictedBAC())
    c.capture_scans(store, queue, now=280, fetch_progress=lambda _: progress(5))
    assert c.db.execute('SELECT COUNT(*) FROM bac_events').fetchone()[0] == 3
    assert c.confirmed_progress_at(sid) == '1970-01-01T00:02:40+00:00'


def test_public_lifecycle_durable_observation_and_no_inferred_approval(tmp_path):
    c, store, queue, sid = running_fixture(tmp_path)
    value = c.public_lifecycle(sid, now=100)
    assert value == dict(version='starter_lifecycle@1', intake_status='recorded',
                         report_status='unavailable', delivery_status='unavailable',
                         updated_at='1970-01-01T00:01:40+00:00')
    assert c.public_lifecycle(sid, now=130) == value
    assert c.public_lifecycle('absent')['updated_at'] is None
    c.db.close()
    assert event_client(tmp_path, RestrictedBAC()).public_lifecycle(sid, now=160) == value


def test_actual_public_backend_response_renders_in_page_owner_consumer(tmp_path, monkeypatch):
    import hashlib
    import os
    import subprocess
    os.environ.setdefault('QA_DB_PATH', str(tmp_path / 'app.sqlite'))
    import app
    from fastapi.testclient import TestClient
    c, store, queue, sid = running_fixture(tmp_path)
    token = 'fixture-status-token'
    store.db.execute('UPDATE scans SET status_token_hash=? WHERE id=?',
                     (hashlib.sha256(token.encode()).hexdigest(), sid))
    monkeypatch.setattr(app, 'store', store)
    monkeypatch.setattr(app, 'bac', c)
    monkeypatch.setattr(app, '_bac_mode', lambda: 'configured')
    # Authenticated progress transport is independently covered; no network here.
    monkeypatch.setattr(app, '_engine_progress', lambda _: {})
    response = TestClient(app.app).post('/scan/v1/scan-status', json={'status_token': token})
    assert response.status_code == 200
    assert response.json()['lifecycle']['intake_status'] == 'recorded'
    assert set(response.json()['lifecycle']) == {'version', 'intake_status', 'report_status',
                                                'delivery_status', 'updated_at'}
    fixture = tmp_path / 'public-response.json'
    fixture.write_text(json.dumps(dict(response=response.json(), expected_text=[
        'Intake recorded.', 'Report preparation is awaiting scan results.',
        'Report delivery is not ready.'])))  # BEAAA-42596: real durable report/delivery values
    consumer = Path(__file__).parent / 'fixtures' / 'starter-page-v1'
    result = subprocess.run(['node', str(consumer / 'verify.cjs'), str(consumer / 'scan.js'), str(fixture)],
                            check=True, text=True, capture_output=True)
    assert 'supplied backend response rendered by actual page script' in result.stdout


def test_private_resolver_requires_exact_case_scan_and_revision(tmp_path):
    import hashlib
    c, store, queue, sid = running_fixture(tmp_path)
    value = dict(scan_id='remote', status='complete', result_rows=[
        dict(test_id='one', verdict='pass', prompt='private', reply='private')])
    c.capture_scans(store, queue, lambda _: value, now=100)
    digest = hashlib.sha256(store.scan(sid)['tests_json'].encode()).hexdigest()
    bound = dict(request_id=sid, case_id='case-1', scan_id=sid, revision_sha256=digest)
    evidence = c.resolve_evidence(store, **bound)
    assert evidence['results'] == [dict(ordinal=1, verdict='pass')]
    assert 'private' not in json.dumps(evidence)
    for field in bound:
        with pytest.raises(BACUnconfirmed):
            c.resolve_evidence(store, **dict(bound, **{field: 'foreign'}))


def test_composed_evidence_http_survives_private_store_restart(tmp_path):
    import hashlib
    from fastapi.testclient import TestClient
    from bac_gateway import create_evidence_app
    from qa_service import IntakeStore
    c, store, queue, sid = running_fixture(tmp_path)
    c.capture_scans(store, queue, lambda _: dict(scan_id='remote', status='complete', result_rows=[
        dict(test_id='one', verdict='pass', prompt='PRIVATE_PROMPT', reply='PRIVATE_REPLY')]), now=100)
    digest = hashlib.sha256(store.scan(sid)['tests_json'].encode()).hexdigest()
    binding = dict(request_id=sid, case_id='case-1', scan_id=sid, revision_sha256=digest)
    # Reopen both databases; no resolver stub or in-memory binding survives.
    intake_path = c.db.execute('PRAGMA database_list').fetchone()[2]
    store_path = store.db.execute('PRAGMA database_list').fetchone()[2]
    c.db.close()
    store.db.close()
    c = BACIntake(intake_path, base_url='https://bac.invalid', company_id='bac',
                  owner_id='owner', token='intake-secret', post=lambda *a, **k: pytest.fail('network'))
    store = IntakeStore(store_path)
    def compose():
        return TestClient(create_evidence_app(intake=c, store=store,
            actors={'analyst-secret': ('analyst', 'Analyst'), 'lead-secret': ('lead', 'Lead'),
                    'foreign-secret': ('foreign', 'Analyst')},
            grants={'analyst': ['case-1'], 'lead': ['case-1']}))
    client = compose()
    for token in ('analyst-secret', 'lead-secret'):
        response = client.post('/tools/starter/evidence', json=binding,
                               headers={'Authorization': 'Bearer ' + token})
        assert response.status_code == 200
        assert response.json() == dict(binding, outcome='complete', results=[dict(ordinal=1, verdict='pass')])
    for token in ('foreign-secret', 'intake-secret', 'unknown'):
        assert client.post('/tools/starter/evidence', json=binding,
                           headers={'Authorization': 'Bearer ' + token}).status_code == 403
    headers = {'Authorization': 'Bearer analyst-secret'}
    for field in binding:
        value = '0' * 64 if field == 'revision_sha256' else 'foreign'
        assert client.post('/tools/starter/evidence', json=dict(binding, **{field: value}), headers=headers).status_code == 403
    assert client.post('/tools/starter/evidence?extra=1', json=binding, headers=headers).status_code == 403
    assert client.get('/api/issues/case-1', headers=headers).status_code == 401
    # A changed durable revision invalidates the formerly accepted digest.
    store.db.execute("UPDATE scans SET tests_json='[]' WHERE id=?", (sid,))
    assert compose().post('/tools/starter/evidence', json=binding, headers=headers).status_code == 403
    with pytest.raises(ValueError):
        create_evidence_app(intake=c, store=store,
            actors={'intake-secret': ('lead', 'Lead')}, grants={'lead': ['case-1']})
