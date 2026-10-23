"""Focused Findings-only regression; AST route, real store/queue, no ASGI/network."""
import ast
import hashlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
SERVER = Path(__file__).parents[1] / 'server'
sys.path.insert(0, str(SERVER))
from qa_runtime import ScanQueue, QueueFull, DispatchUnknown, DispatchNotSent
from qa_service import IntakeStore, IntakeError
from bac_intake import BACIntake, BACUnconfirmed
from findings_intake import FindingsIntake


def function(name, env):
    env.setdefault('os', NS(environ={}))
    tree = ast.parse(Path(os.environ.get('FINDINGS_ROUTE_SOURCE', str(SERVER / 'app.py'))).read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    fn.decorator_list = []; fn.returns = None
    for arg in fn.args.args: arg.annotation = None
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'app.py', 'exec'), env)
    return env[name]


class HTTPError(Exception):
    def __init__(self, status, detail): self.status_code = status


def fixture(tmp_path, capacity=3):
    path = str(tmp_path / 'intake.sqlite')
    store = IntakeStore(path)
    store.db.execute("INSERT INTO codes VALUES ('code','p','P','standard',10,1,'2099-12-31',1)")
    calls = []
    queue = ScanQueue(store, lambda payload: calls.append(dict(payload)) or {'scan_id': 'remote'}, capacity=capacity)
    bac = BACIntake(path, base_url='', company_id='', owner_id='', token='',
                    post=lambda *a, **k: pytest.fail('BAC I/O'), get=lambda *a, **k: pytest.fail('BAC I/O'))
    fields = dict(code='code', email='a@example.com', agent_label='agent', endpoint_url='https://fixture.invalid',
                  transport='http', model=None, connection_proof='proof', auth='fixture-secret', pack_id='starter-100@4')
    body = NS(**fields, terms_version='v1', consent_improvement=False, model_dump=lambda:dict(fields))
    env = dict(controlled_operator=None, _controlled_claim=lambda *a:None, _flag=lambda *a:True, _private_acceptance=lambda *a:False, WEBHOOK_SECRET='fixture', PROOF_MESSAGE='fixture proof expired',
               os=NS(environ={'QA_READY':'1'}), _terms_version=lambda:'v1',
               _resolve_code=lambda c:dict(public=False,code=c,partner='p'), proofs=NS(redeem=lambda *a:True),
               _payload=lambda c:dict(auth=c['auth']), _mail_configured=lambda:True, store=store, queue=queue, bac=bac, findings=FindingsIntake(path),
               _bac_mode=lambda:"absent", BACUnconfirmed=BACUnconfirmed, HTTPException=HTTPError, IntakeError=IntakeError,
               QueueFull=QueueFull, DispatchUnknown=DispatchUnknown, DispatchNotSent=DispatchNotSent)
    return path, store, queue, calls, body, env


def test_direct_dispatch_zero_bac_and_duplicate_guard(tmp_path):
    path, store, queue, calls, body, env = fixture(tmp_path)
    route = function('start_scan', env)
    assert route(body, NS())['status'] == 'running'
    assert calls == [{'auth':'fixture-secret'}]
    assert not queue.awaiting_bac
    with pytest.raises(HTTPError) as exc: route(body, NS())
    assert exc.value.status_code == 429 and len(calls) == 1
    assert store.db.execute('SELECT count(*) FROM bac_intake').fetchone()[0] == 0


@pytest.mark.parametrize('guard', ['code', 'proof', 'terms'])
def test_guards_prevent_dispatch(tmp_path, guard):
    _, store, _, calls, body, env = fixture(tmp_path)
    if guard == 'code': store.db.execute('UPDATE codes SET active=0')
    if guard == 'proof': env['proofs'].redeem = lambda *a:False
    if guard == 'terms': body.terms_version = 'old'
    with pytest.raises(HTTPError): function('start_scan', env)(body, NS())
    assert calls == []


def test_queued_cancel_without_bac(tmp_path):
    _, store, queue, calls, body, env = fixture(tmp_path, capacity=0)
    result = function('start_scan', env)(body, NS())
    row = store.status(result['status_token'])
    env['_status'] = lambda token: row
    assert function('scan_cancel', env)(NS(status_token=result['status_token'])) == {'status':'cancelled'}
    assert not calls and not queue.pending


def test_terminal_recovery_without_bac_and_private_progress(tmp_path):
    path, store, queue, calls, body, env = fixture(tmp_path)
    result = function('start_scan', env)(body, NS())
    scan = store.status(result['status_token'])['scan_id']
    findings = FindingsIntake(path)
    # Wrong remote identity must neither complete nor redispatch.
    findings.capture(store, queue, lambda row: {'scan_id':'foreign','status':'complete','result_rows':[{}]},
                     lambda scan: {'accepted_pack_total':100,'processed_count':1,'auth':'private'}, now=1)
    assert store.scan(scan)['status'] == 'running'
    findings = FindingsIntake(path)  # progress survives restart, no credentials retained
    assert findings.latest_progress(scan) == {'accepted_pack_total':100,'processed_count':1}
    findings.capture(store, queue, lambda row: {'scan_id':'remote','status':'failed','incomplete':True,
                     'incomplete_reason':'scanner_restart_resume_exhausted','result_rows':[]}, lambda scan:{}, now=32)
    assert store.scan(scan)['status'] == 'finished'
    assert store.scan(scan)['outcome'] == 'failed'
    assert not queue.active and len(calls) == 1
    assert store.db.execute('SELECT count(*) FROM mail_outbox').fetchone()[0] == 0


def test_worker_has_no_bac_operations_or_delivery_calls(tmp_path):
    calls=[]
    env=dict(os=NS(environ={}), _bac_mode=lambda:"absent", findings=NS(capture=lambda *a:calls.append('capture')), store=None,
             queue=NS(expire_pending=lambda:calls.append('expire')), _terminal_scan=None, _engine_progress=None)
    function('worker_tick', env)()
    assert calls == ['capture','expire']


def test_unknown_dispatch_retains_slot_and_never_retries(tmp_path):
    path, store, queue, calls, body, env = fixture(tmp_path)
    def lost(payload):
        calls.append(dict(payload))
        raise TimeoutError('fixture lost response')
    queue.start = lost
    result = function('start_scan', env)(body, NS())
    assert result['status'] == 'dispatch_unknown'
    scan = store.status(result['status_token'])['scan_id']
    assert scan in queue.active
    restarted = ScanQueue(store, lambda p:pytest.fail('redispatch'))
    assert scan in restarted.active
    assert store.db.execute('SELECT count(*) FROM scans').fetchone()[0] == 1


def test_progress_heartbeat_does_not_claim_advancement(tmp_path):
    path, store, queue, _, body, env = fixture(tmp_path)
    result = function('start_scan', env)(body, NS())
    scan = store.status(result['status_token'])['scan_id']
    findings = FindingsIntake(path)
    for now in [1, 32]:
        findings.capture(store, queue, lambda row:None, lambda scan:{'processed_count':1}, now=now)
    first = findings.confirmed_progress_at(scan)
    findings.capture(store, queue, lambda row:None, lambda scan:{'processed_count':2}, now=63)
    assert findings.confirmed_progress_at(scan) > first


@pytest.mark.parametrize('mode', ['invalid', 'configured'])
def test_bac_configuration_never_bypasses_ack(tmp_path, mode):
    _, store, queue, calls, body, env = fixture(tmp_path)
    env['_bac_mode'] = lambda: mode
    if mode == 'invalid':
        with pytest.raises(HTTPError) as exc:
            function('start_scan', env)(body, NS())
        assert exc.value.status_code == 503
    else:
        env['bac'].retry_identity = lambda scan, clean: scan
        def unconfirmed(*a, **k): raise BACUnconfirmed('unconfirmed')
        env['bac'].acknowledge = unconfirmed
        assert function('start_scan', env)(body, NS())['status'] == 'queued'
        assert queue.awaiting_bac
    assert calls == []


def test_configured_bac_ack_before_dispatch(tmp_path):
    _, _, queue, calls, body, env = fixture(tmp_path)
    env['_bac_mode'] = lambda: 'configured'
    env['bac'].retry_identity = lambda scan, clean: scan
    env['bac'].acknowledge = lambda *a, **k: calls.append('ack')
    assert function('start_scan', env)(body, NS())['status'] == 'running'
    assert calls == ['ack', {'auth': 'fixture-secret'}]
    assert not queue.awaiting_bac


@pytest.mark.parametrize('values,expected', [({}, 'absent'),
    ({'QA_BAC_URL':'https://bac.invalid'}, 'invalid'),
    ({'QA_BAC_URL':'invalid', 'QA_BAC_COMPANY_ID':'c', 'QA_BAC_LEAD_ID':'l', 'QA_BAC_TOKEN':'t'}, 'invalid'),
    ({'QA_BAC_URL':'https://bac.invalid', 'QA_BAC_COMPANY_ID':'c', 'QA_BAC_LEAD_ID':'l', 'QA_BAC_TOKEN':'t'}, 'configured')])
def test_bac_mode(values, expected):
    assert function('_bac_mode', {'os':NS(environ=values)})() == expected


def test_configured_worker_retains_recovery_order():
    calls=[]
    def record(name): return lambda *a, **k: calls.append(name)
    env=dict(_bac_mode=lambda:'configured', bac=NS(reconcile_pending=record('reconcile'),
        capture_scans=record('capture'), flush_events=record('events')),
        queue=NS(recover_bac=record('recover'), expire_pending=record('expire')),
        operations=NS(flush=record('flush'), poll_status=record('poll')),
        _operations_client=lambda:None, store=None, _terminal_scan=None,
        _engine_progress=None, _operations_enqueue=None)
    function('worker_tick', env)()
    assert calls == ['reconcile','recover','capture','events','flush','poll','expire']


def test_partial_progress_retains_denominator_across_restart(tmp_path):
    path, store, queue, _, body, env = fixture(tmp_path)
    result = function('start_scan', env)(body, NS())
    scan = store.status(result['status_token'])['scan_id']
    findings = FindingsIntake(path)
    findings.capture(store, queue, lambda row: None,
                     lambda scan: {'accepted_pack_total': 100, 'processed_count': 1}, now=1)
    findings = FindingsIntake(path)
    findings.capture(store, queue, lambda row: None,
                     lambda scan: {'processed_count': 2}, now=32)
    assert findings.latest_progress(scan) == {'accepted_pack_total': 100, 'processed_count': 2}
    # An explicitly observed denominator change must still replace the old value.
    findings.capture(store, queue, lambda row: None,
                     lambda scan: {'accepted_pack_total': 80, 'processed_count': 3}, now=63)
    assert FindingsIntake(path).latest_progress(scan) == {'accepted_pack_total': 80, 'processed_count': 3}


@pytest.mark.parametrize('route', ['scan_status', 'scan_cancel'])
@pytest.mark.parametrize('config', [
    {'QA_BAC_URL': 'https://bac.invalid'},
    {'QA_BAC_URL': 'malformed', 'QA_BAC_COMPANY_ID': 'c',
     'QA_BAC_LEAD_ID': 'l', 'QA_BAC_TOKEN': 't'},
])
def test_invalid_config_holds_queued_request(tmp_path, route, config):
    _, store, queue, calls, body, env = fixture(tmp_path, capacity=0)
    result = function('start_scan', env)(body, NS())
    scan = store.status(result['status_token'])['scan_id']
    env['_bac_mode'] = function('_bac_mode', {'os': NS(environ=config)})
    env['_status'] = lambda token: store.status(token)
    env.update(_engine_progress=lambda scan: {}, hashlib=hashlib, STARTER_PACK_IDS={'starter-100@4'})
    with pytest.raises(HTTPError) as exc:
        function(route, env)(NS(status_token=result['status_token']))
    assert exc.value.status_code == 503
    assert scan in queue.pending
    assert store.scan(scan)['status'] == 'queued'
    assert calls == []


def test_invalid_config_worker_holds_all_work():
    calls = []
    env = dict(_bac_mode=lambda: 'invalid', store=None,
               findings=NS(capture=lambda *a: calls.append('capture')),
               queue=NS(expire_pending=lambda: calls.append('expire')),
               _terminal_scan=None, _engine_progress=None)
    function('worker_tick', env)()
    assert calls == []


def test_controlled_pre_io_denial_frees_slot_without_resetting_one_use(tmp_path):
    from controlled_admission import ControlledAdmission, AdmissionClosed
    path, store, queue, calls, body, env = fixture(tmp_path)
    binding = dict(actor='operator', request_id='request', target='root-pending')
    admission = ControlledAdmission(path)
    admission.provision(**binding)
    admission.enable()
    admission.claim(**binding)
    admission.disable()
    queue.before_dispatch = lambda *_: admission.begin_dispatch(**binding)
    with pytest.raises(HTTPError) as exc:
        function('start_scan', env)(body, NS())
    assert exc.value.status_code == 502
    assert calls == []
    assert queue.active == {}
    assert store.db.execute('SELECT status FROM scans').fetchone()[0] == 'launch_failed'
    admission.enable()
    with pytest.raises(AdmissionClosed):
        admission.claim(**binding)
    # Restart cannot turn the rejected launch into dispatch_unknown or hold capacity.
    restarted = ScanQueue(IntakeStore(path), lambda _: pytest.fail('Scanner I/O'))
    assert restarted.active == {}


@pytest.mark.parametrize('route', ['connection_check', 'start_scan'])
@pytest.mark.parametrize('client_host', ['127.0.0.1', '::1', '198.51.100.1'])
def test_closed_http_routes_reject_expired_private_allowance(tmp_path, route, client_host):
    _, store, queue, calls, body, env = fixture(tmp_path)
    env['os'] = NS(environ={'QA_READY': '0', 'QA_ACCEPTANCE_TOKEN': 'expired-token'})
    # Even a successful legacy helper must not bypass the current hold.
    env['_private_acceptance'] = lambda *args: True
    body.code = 'QA-ACCEPT-20261006'
    body.email = 'ari@backbond.ai'
    request = NS(client=NS(host=client_host), headers={'x-qa-acceptance': 'expired-token'})
    env['_scanner_check'] = lambda *args: pytest.fail('closed connectivity I/O')
    with pytest.raises(HTTPError) as exc:
        function(route, env)(body, request)
    assert exc.value.status_code == 503
    assert calls == []
    assert queue.active == {} and not queue.pending
    assert store.db.execute('SELECT count(*) FROM scans').fetchone()[0] == 0


def test_terms_hold_rejects_private_allowance(tmp_path):
    _, store, _, calls, body, env = fixture(tmp_path)
    env['_flag'] = lambda name, default: name != 'QA_TERMS_APPROVED'
    env['_private_acceptance'] = lambda *args: True
    with pytest.raises(HTTPError) as exc:
        function('start_scan', env)(body, NS())
    assert exc.value.status_code == 503
    assert calls == []
    assert store.db.execute('SELECT count(*) FROM scans').fetchone()[0] == 0


def test_operator_http_verification_precedes_quota_and_claim_precedes_dispatch(tmp_path):
    import hashlib
    from controlled_admission import ControlledAdmission, OperatorAdmission, AdmissionClosed
    _, store, queue, calls, body, env = fixture(tmp_path)
    payload = {'agent_endpoint': {'url': 'https://inert.invalid', 'model': 'inert'},
               'credential_secret': 'inert-only', 'pack_id': body.pack_id}
    ledger = ControlledAdmission(str(tmp_path / 'operator.sqlite'))
    binding = dict(actor='operator', request_id='one-request', target=OperatorAdmission.fingerprint(payload))
    ledger.provision(**binding); ledger.enable()
    operator = OperatorAdmission(ledger, **binding,
        token_sha256=hashlib.sha256(b'inert-token').hexdigest())
    operator.scanner_permit_id = 'inert-permit'
    env.update(controlled_operator=operator, AdmissionClosed=AdmissionClosed,
               _payload=lambda c:dict(payload),
               _flag=lambda name, default: name not in {'QA_TERMS_APPROVED'},
               os=NS(environ={'QA_READY':'0'}))
    env['_controlled_claim'] = function('_controlled_claim', env)
    queue.before_dispatch = lambda scan_id, p: operator.before_dispatch(
        scan_id, {k:v for k,v in p.items() if k != 'controlled_permit_id'})
    route = function('start_scan', env)
    # Ordinary callers cannot reserve quota even though this fixture has an operator.
    with pytest.raises(HTTPError) as exc: route(body, NS(headers={}))
    assert exc.value.status_code == 403
    assert store.db.execute('SELECT COUNT(*) FROM scans').fetchone()[0] == 0
    request = NS(headers={'authorization': 'Bearer inert-token', 'x-controlled-request-id':'one-request'})
    original_reserve = store.reserve
    def reserve(**kwargs):
        assert ledger.db.execute('SELECT consumed FROM controlled_admission').fetchone() == (0,)
        return original_reserve(**kwargs)
    store.reserve = reserve
    assert route(body, request)['status'] == 'running'
    assert calls == [dict(payload, controlled_permit_id='inert-permit')]
    assert ledger.db.execute('SELECT dispatch_state FROM controlled_admission').fetchone() == ('dispatch_unknown',)
    store.reserve = original_reserve
    with pytest.raises(HTTPError): route(body, request)
    assert len(calls) == 1


@pytest.mark.parametrize('hold', ['terms', 'ready'])
def test_controlled_http_refuses_global_hold_removal(hold):
    from unittest.mock import Mock
    operator = Mock()
    env = dict(controlled_operator=operator, HTTPException=HTTPError,
               _flag=lambda *a: hold == 'terms',
               os=NS(environ={'QA_READY': '1' if hold == 'ready' else '0'}))
    with pytest.raises(HTTPError) as exc:
        function('_controlled_claim', env)(NS(headers={}), {})
    assert exc.value.status_code == 503
    operator.claim_http.assert_not_called()


@pytest.mark.parametrize('ready', ['0', '1'])
@pytest.mark.parametrize('client_host', ['127.0.0.1', '::1', '198.51.100.1'])
def test_controlled_connection_check_cannot_use_unmetered_legacy_route(tmp_path, ready, client_host):
    _, store, queue, calls, body, env = fixture(tmp_path)
    env['os'] = NS(environ={'QA_READY': ready})
    from controlled_admission import AdmissionClosed
    env['AdmissionClosed'] = AdmissionClosed
    env['_flag'] = lambda *a: False
    env['controlled_operator'] = object()
    env['_resolve_code'] = lambda *args: pytest.fail('legacy code resolution')
    env['_scanner_check'] = lambda *args: pytest.fail('unmetered connectivity I/O')
    request = NS(client=NS(host=client_host), headers={})
    with pytest.raises(HTTPError) as exc:
        function('connection_check', env)(body, request)
    assert exc.value.status_code == (503 if ready == '1' else 403)
    assert calls == []
    assert queue.active == {} and not queue.pending
    assert store.db.execute('SELECT count(*) FROM scans').fetchone()[0] == 0
