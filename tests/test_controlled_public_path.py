"""Real intake/Scanner HTTP parsing, queue, handler and paid ledger; offline I/O."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time

import httpx
from fastapi.testclient import TestClient
import pytest

from harness import controlled_permit_loader as loader
from harness.credentials import CredentialVault
from harness.customer_runner import _CustomerAgentHttpRunner, customer_agent_http_runner_factory
from scanners.customer_agent_walker import customer_agent_probe_run_fn
from scanners.live_probe_orchestrator import run_scan
from tests.test_partner_qa_starter_single_dispatch import _corpus, _IPC


@pytest.fixture(params=[False, True], ids=["synchronous", "durable-worker"])
def integrated(tmp_path, monkeypatch, request):
    server = Path('backbond/scan-intake/server').resolve()
    sys.path.insert(0, str(server))
    monkeypatch.setenv('BACKBOND_VARIABLE_FORMS_DB', str(tmp_path/'forms.sqlite'))
    monkeypatch.setenv('QA_DB_PATH', str(tmp_path/'intake.sqlite'))
    monkeypatch.setenv('QA_READY', '0')
    monkeypatch.setenv('QA_TERMS_APPROVED', '0')
    monkeypatch.setenv('QA_WEBHOOK_SECRET', 'inert-webhook')
    monkeypatch.setenv('QA_SCANNER_TOKEN', 'inert-service')
    monkeypatch.setenv('ARE_API_AUTH_TOKENS_JSON', json.dumps({'inert-service': 'partner-test'}))
    monkeypatch.setenv('OPENROUTER_API_KEY', 'inert-judge')
    monkeypatch.delenv('QA_CONTROLLED_OPERATOR_CONFIG', raising=False)
    spec = importlib.util.spec_from_file_location('controlled_test_intake', server/'app.py')
    intake = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, intake)
    spec.loader.exec_module(intake)
    from qa_service import Code
    from controlled_startup import load_operator
    from controlled_admission import ControlledAdmission, OperatorAdmission
    intake.store.put_code(Code('TEST', 'test', 'Test', 'standard', 50, 50, '2099-01-01'))
    fields = dict(code='TEST', email='fixture@example.test', agent_label='fixture',
        endpoint_url='https://api.backbond.ai/exact/fixture', transport='rest',
        auth={'type': 'bearer', 'value': 'inert-memory-target'}, pack_id='starter-100@4')
    parsed = intake.ConnectionFields.model_validate(dict(fields, endpoint_url='https://fixture.invalid'))
    normalized = parsed.model_dump()
    normalized['endpoint_url'] = fields['endpoint_url']
    payload = intake._payload(dict(normalized, partner='test'))
    digest = loader.fingerprint(payload)
    private = tmp_path/'private'
    private.mkdir(mode=0o700)
    scanner_db = private/'scanner.sqlite'
    record = dict(permit_id='p'*32, tenant_id='partner-test', actor='fixture', request_id='request',
        target_sha256=digest, token_sha256=hashlib.sha256(b'inert-operator').hexdigest(),
        expires_at=int(time.time())+300)
    loader.provision_disabled(scanner_db, record)
    loader.arm_once(scanner_db, record)
    monkeypatch.setenv('ARE_CONTROLLED_PERMIT_DB', str(scanner_db))
    intake_db = private/'intake-permit.sqlite'
    ledger = ControlledAdmission(intake_db)
    binding = dict(actor='fixture', request_id='request', target=digest)
    ledger.provision(**binding)
    OperatorAdmission(ledger, **binding, token_sha256=record['token_sha256'])
    ledger.enable()
    ledger.db.close()
    intake_db.chmod(0o600)
    config = dict(ledger_path=str(intake_db), scanner_permit_id=record['permit_id'],
        **binding, token_sha256=record['token_sha256'])
    config_path = private/'operator.json'
    config_path.write_text(json.dumps(config)); config_path.chmod(0o600)
    monkeypatch.setenv('QA_CONTROLLED_OPERATOR_CONFIG', str(config_path))
    intake.controlled_operator = load_operator()
    policy = dict(version=1, kind='exact-fixed-handler-complete-request', target_sha256=digest, upper_microusd=10)
    policy_path = private/'policy.json'
    policy_path.write_text(json.dumps(policy)); policy_path.chmod(0o600)
    target_config = dict(path=str(policy_path), expected_sha256=hashlib.sha256(policy_path.read_bytes()).hexdigest())
    target_path = private/'target.json'
    target_path.write_text(json.dumps(target_config)); target_path.chmod(0o600)
    monkeypatch.setenv('ARE_CONTROLLED_TARGET_CONFIG', str(target_path))
    calls = []
    def target_io(self, prompt):
        calls.append('target')
        return 'I cannot help with that request.'
    monkeypatch.setattr(_CustomerAgentHttpRunner, 'complete', target_io)
    def judge_io(url, **kwargs):
        from scanners.starter_judge import JUDGE_URL
        assert url == JUDGE_URL
        calls.append('judge')
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(
            {'verdict': 'passed', 'reason': 'Offline boundary fixture.'})}}]}, request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx, 'post', judge_io)
    corpus, corpus_hash = _corpus(tmp_path)
    from api import scan_handlers
    # Pin the inert fixture corpus, retaining real hash validation and full roster.
    monkeypatch.setattr(scan_handlers, 'STARTER_PACK_CORPUS_SNAPSHOT_HASH', corpus_hash)
    def execute(request, secret, *, controlled_execution_payload):
        from harness.controlled_target_policy import startup_target_config
        vault = CredentialVault()
        vault.register(request.credential_ref, secret)
        return run_scan(request, vault, _IPC(), corpus_tarball_path=corpus,
            probe_run_fn=customer_agent_probe_run_fn, runner_factory=customer_agent_http_runner_factory,
            controlled_target_config=startup_target_config(request.endpoint_config),
            controlled_execution_payload=controlled_execution_payload)
    from api.accepted_scan_store import AcceptedScanStore
    runtime = scan_handlers.ScanRuntime(run_scan_fn=execute,
        accepted_scan_store=AcceptedScanStore(private/'accepted.sqlite') if request.param else None)
    from api.app import create_app
    scanner = TestClient(create_app(scan_runtime=runtime))
    public = TestClient(intake.app)
    # Route actual httpx calls to the actual Scanner ASGI app, without replacing handlers.
    class LocalHTTP:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, url, **kw):
            response = scanner.post('/scanner/v1/'+url.rsplit('/',1)[-1], **kw)
            return response
        def get(self, url, **kw): return scanner.get('/scanner/v1/'+url.rsplit('/',1)[-1], **kw)
    original_client = httpx.Client
    monkeypatch.setattr(intake.httpx, 'Client', lambda **kw:
        original_client(**kw) if 'transport' in kw else LocalHTTP(**kw))
    # Isolate the optional service token file from the real host configuration.
    monkeypatch.setattr(intake, 'Path', lambda value: tmp_path/'absent-service-token-map'
        if str(value) == '/var/lib/backbond-qa/partner-code-tenants.json' else Path(value))
    headers = {'authorization': 'Bearer inert-operator', 'x-controlled-request-id': 'request'}
    yield intake, public, fields, headers, scanner_db, calls, private, runtime, scanner
    public.close(); scanner.close()
    sys.path.remove(str(server))


def _assert_controlled_permit_denial(response):
    # The authenticated controlled lane treats a consumed/expired permit as an
    # expected denial. The HTTP exchange succeeds, but no connection proof or
    # paid retry is authorized. Ordinary Scanner authentication errors remain 502.
    assert response.status_code == 200
    assert response.json() == {
        'ok': False,
        'error': 'Connection check failed. Check the endpoint, authentication and response settings.',
    }


def test_natural_check_then_one_real_metered_scan(integrated):
    intake, client, fields, headers, path, calls, private, runtime, _ = integrated
    assert client.post('/scan/v1/connection-checks', json=fields).status_code == 403
    assert not calls
    check = client.post('/scan/v1/connection-checks', json=fields, headers=headers)
    assert check.status_code == 200, check.text
    proof = check.json()['connection_proof']
    assert calls == ['target']
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT tests,reserved_microusd,consumed FROM controlled_admission').fetchone() == (0,10,0)
        assert db.execute('SELECT COUNT(*) FROM controlled_scan_binding').fetchone() == (0,)
    again = client.post('/scan/v1/connection-checks', json=fields, headers=headers)
    _assert_controlled_permit_denial(again)
    assert calls == ['target']
    body = dict(fields, consent_owner=True, consent_terms=True, terms_version=intake._terms_version(), connection_proof=proof)
    scan = client.post('/scan/v1/scans', json=body, headers=headers)
    assert scan.status_code == 200, scan.text
    assert scan.json()['status'] == 'running', scan.text
    for thread in list(runtime._background_threads.values()):
        thread.join(timeout=20)
        assert not thread.is_alive()
    assert calls.count('judge') == 100
    assert calls.count('target') == 101
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT tests,consumed,dispatch_state FROM controlled_admission').fetchone() == (100,1,'dispatch_unknown')
        attempted = {json.loads(row[0])[1] for row in db.execute('SELECT call_id FROM controlled_paid_calls')}
        form_binding = db.execute('SELECT scan_id,tenant_id,agent_id FROM controlled_accepted_form').fetchone()
        from scanners.starter_forms import starter_form_store, cases_from_form
        form = starter_form_store().get(form_binding[0], scope=tuple(form_binding[1:]), product='starter')
        assert attempted == {case.case_id for case in cases_from_form(form)}
        assert len(attempted) == 100
        assert 0 < db.execute('SELECT reserved_microusd FROM controlled_admission').fetchone()[0] <= 10_000_000
    before = len(calls)
    assert client.post('/scan/v1/scans', json=body, headers=headers).status_code == 409
    assert client.post('/scan/v1/scans', json=body).status_code == 403
    assert len(calls) == before
    assert len(runtime._results) == 1
    # Inspect generated persistent state/config only, never protected runtime inputs.
    for file in private.iterdir():
        data = file.read_bytes()
        assert b'inert-memory-target' not in data
        assert b'inert-operator' not in data
        assert b'credential_secret' not in data
    assert intake.os.environ['QA_READY'] == intake.os.environ['QA_TERMS_APPROVED'] == '0'


def test_expired_and_changed_execution_make_zero_paid_calls(integrated):
    intake, client, fields, headers, path, calls, *_ = integrated
    for changes in ({'auth': {'type':'bearer','value':'changed'}}, {'pack_id':'starter-100@5'},
                    {'endpoint_url':fields['endpoint_url']+'/other'}, {'model':'changed'}):
        assert client.post('/scan/v1/connection-checks', json=dict(fields, **changes), headers=headers).status_code == 403
    with sqlite3.connect(path) as db:
        db.execute('UPDATE controlled_permit_context SET expires_at=1')
    response = client.post('/scan/v1/connection-checks', json=fields, headers=headers)
    _assert_controlled_permit_denial(response)
    assert not calls


def test_lost_check_response_cannot_check_or_scan_again(integrated, monkeypatch):
    intake, client, fields, headers, path, calls, private, runtime, scanner = integrated
    post = scanner.post
    def lose_response(url, **kw):
        post(url, **kw)
        raise httpx.ReadTimeout('inert lost response')
    monkeypatch.setattr(scanner, 'post', lose_response)
    assert client.post('/scan/v1/connection-checks', json=fields, headers=headers).status_code == 502
    assert calls == ['target']
    monkeypatch.setattr(scanner, 'post', post)
    retry = client.post('/scan/v1/connection-checks', json=fields, headers=headers)
    _assert_controlled_permit_denial(retry)
    body = dict(fields, consent_owner=True, consent_terms=True,
        terms_version=intake._terms_version(), connection_proof='invented')
    assert client.post('/scan/v1/scans', json=body, headers=headers).status_code == 409
    assert calls == ['target']
    assert not runtime._results


def test_lost_scan_response_and_restart_do_not_redispatch(integrated, monkeypatch):
    intake, client, fields, headers, path, calls, private, runtime, scanner = integrated
    proof = client.post('/scan/v1/connection-checks', json=fields, headers=headers).json()['connection_proof']
    post = scanner.post
    def lose_response(url, **kw):
        response = post(url, **kw)
        assert response.status_code == 202
        raise httpx.ReadTimeout('inert lost response')
    monkeypatch.setattr(scanner, 'post', lose_response)
    body = dict(fields, consent_owner=True, consent_terms=True,
        terms_version=intake._terms_version(), connection_proof=proof)
    accepted = client.post('/scan/v1/scans', json=body, headers=headers)
    assert accepted.status_code == 200 and accepted.json()['status'] == 'dispatch_unknown'
    for thread in list(runtime._background_threads.values()):
        thread.join(timeout=20)
        assert not thread.is_alive()
    before = len(calls)
    monkeypatch.setattr(scanner, 'post', post)
    assert calls.count('judge') == 100
    from controlled_startup import load_operator
    from qa_runtime import ScanQueue
    intake.controlled_operator = load_operator()
    intake.queue = ScanQueue(intake.store, intake._scanner_start, before_dispatch=intake._controlled_dispatch)
    assert client.post('/scan/v1/scans', json=body, headers=headers).status_code == 409
    _assert_controlled_permit_denial(client.post('/scan/v1/connection-checks', json=fields, headers=headers))
    assert len(calls) == before
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT consumed,tests FROM controlled_admission').fetchone() == (1,100)


def test_legacy_rejection_does_not_consume_admission(integrated, monkeypatch):
    intake, client, fields, headers, _, calls, private, *_ = integrated
    monkeypatch.setenv('QA_REQUIRE_CONNECTION_PROOF', '0')
    body = dict(fields, consent_owner=True, consent_terms=True,
                terms_version=intake._terms_version())
    assert client.post('/scan/v1/scans', json=body, headers=headers).status_code == 503
    with sqlite3.connect(private/'intake-permit.sqlite') as db:
        assert db.execute('SELECT consumed FROM controlled_admission').fetchone() == (0,)
    assert not calls


def test_real_proof_reservation_failure_does_not_consume_admission(integrated, monkeypatch):
    intake, client, fields, headers, _, calls, private, *_ = integrated
    proof = client.post('/scan/v1/connection-checks', json=fields, headers=headers).json()['connection_proof']
    def reject(**kwargs):
        raise intake.IntakeError('fixture limit reached')
    monkeypatch.setattr(intake.store, 'reserve', reject)
    body = dict(fields, consent_owner=True, consent_terms=True,
                terms_version=intake._terms_version(), connection_proof=proof)
    assert client.post('/scan/v1/scans', json=body, headers=headers).status_code == 429
    with sqlite3.connect(private/'intake-permit.sqlite') as db:
        assert db.execute('SELECT consumed FROM controlled_admission').fetchone() == (0,)
    assert calls == ['target']


@pytest.mark.parametrize('after_binding', [False, True], ids=['before-write', 'lost-bind-response'])
def test_bind_failure_returns_pollable_terminal_status(integrated, monkeypatch, after_binding):
    intake, client, fields, headers, _, calls, private, runtime, scanner = integrated
    proof = client.post('/scan/v1/connection-checks', json=fields, headers=headers).json()['connection_proof']
    bind = intake.controlled_operator.bind_scan
    def fail(scan_id):
        if after_binding:
            bind(scan_id)
        raise RuntimeError('inert binding failure')
    monkeypatch.setattr(intake.controlled_operator, 'bind_scan', fail)
    monkeypatch.setattr(scanner, 'post', lambda *a, **kw: pytest.fail('Scanner I/O after binding failure'))
    body = dict(fields, consent_owner=True, consent_terms=True,
                terms_version=intake._terms_version(), connection_proof=proof)
    response = client.post('/scan/v1/scans', json=body, headers=headers)
    assert response.status_code == 200, response.text
    accepted = response.json()
    assert accepted['status'] == 'launch_failed'
    status = client.post('/scan/v1/scan-status', json={'status_token': accepted['status_token']})
    assert status.status_code == 200, status.text
    assert status.json()['status'] == 'launch_failed'
    assert status.json()['retry_allowed'] is False
    assert intake.store.scan(intake.store.status(accepted['status_token'])['scan_id'])['status'] == 'launch_failed'
    with sqlite3.connect(private/'intake-permit.sqlite') as db:
        assert db.execute('SELECT consumed FROM controlled_admission').fetchone() == (1,)
    assert calls == ['target']  # Only the preceding natural connection check.
    assert not runtime._results
    assert intake.store.db.execute(
        "SELECT count(*) FROM scans WHERE status IN ('reserved','queued','starting','running','dispatch_unknown')"
    ).fetchone()[0] == 0
    from controlled_startup import load_operator
    intake.controlled_operator = load_operator()
    assert client.post('/scan/v1/scans', json=body, headers=headers).status_code == 409
    assert intake.store.status(accepted['status_token'])['status'] == 'launch_failed'
    assert calls == ['target']
