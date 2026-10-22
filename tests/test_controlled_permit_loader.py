import hashlib
import sqlite3
import time
import pytest
from harness import controlled_permit_loader as loader

PAYLOAD = {'agent_endpoint': {'url': 'https://fixture.invalid'},
           'credential_secret': 'offline-fixture', 'pack_id': 'starter-100@4'}

def bind_cases(permit, tmp_path, monkeypatch):
    """Real temporary @4 authority, shared by offline boundary fixtures."""
    from scanners.starter_forms import reserve_starter_cases
    monkeypatch.setenv('BACKBOND_VARIABLE_FORMS_DB', str(tmp_path / 'forms.sqlite'))
    cases, _ = reserve_starter_cases(scan_id=permit.scan_id, tenant_id='tenant', agent_id='agent')
    permit.bind_accepted_form(tenant_id='tenant', agent_id='agent', pack_id='starter-100@4')
    return cases


def setup_state(tmp_path, monkeypatch):
    path = tmp_path / 'private' / 'permit.sqlite'
    record = dict(permit_id='p'*32, tenant_id='tenant', actor='operator',
                  request_id='request', target_sha256=loader.fingerprint(PAYLOAD),
                  token_sha256=hashlib.sha256(b'fixture').hexdigest(),
                  expires_at=int(time.time())+300)
    loader.provision_disabled(path, record)
    monkeypatch.setenv('ARE_CONTROLLED_PERMIT_DB', str(path))
    return path, record

def arm_fixture(path):
    # Offline fixture only; production CLI has no arm command.
    with sqlite3.connect(path) as db:
        db.execute('UPDATE controlled_admission SET enabled=1')

def context(record, payload=PAYLOAD, tenant='tenant'):
    return loader.validate_permit_context(record['permit_id'], tenant_id=tenant, payload=payload)

def test_missing_configuration_never_creates_state(monkeypatch, tmp_path):
    path = tmp_path/'absent.sqlite'
    monkeypatch.setenv('ARE_CONTROLLED_PERMIT_DB', str(path))
    with pytest.raises(loader.AdmissionClosed):
        loader.validate_permit_context('p'*32, tenant_id='tenant', payload=PAYLOAD)
    assert not path.exists()

def test_disabled_wrong_tenant_and_full_payload(tmp_path, monkeypatch):
    path, record = setup_state(tmp_path, monkeypatch)
    with pytest.raises(loader.AdmissionClosed):
        context(record)
    arm_fixture(path)
    with pytest.raises(loader.AdmissionClosed):
        context(record, tenant='foreign')
    with pytest.raises(loader.AdmissionClosed):
        context(record, dict(PAYLOAD, pack_id='changed'))
    assert context(record)['target_sha256'] == record['target_sha256']

def test_restart_unknown_and_disable_preserve_state(tmp_path, monkeypatch):
    path, record = setup_state(tmp_path, monkeypatch)
    arm_fixture(path)
    ctx = context(record)
    loader.bind_scanner_permit(ctx, scan_id='0123456789abcdef0123456789abcdef')
    permit = loader.load_bound_scanner_permit(ctx, scan_id='0123456789abcdef0123456789abcdef')
    cases = bind_cases(permit, tmp_path, monkeypatch)
    calls = []
    permit.call(test_id=cases[0].case_id, phase='target', upper_microusd=4,
                operation=lambda: calls.append('called'))
    permit.admission.ledger.db.close()
    with pytest.raises(loader.AdmissionClosed):
        loader.bind_scanner_permit(ctx, scan_id='second')
    permit = loader.load_bound_scanner_permit(ctx, scan_id='0123456789abcdef0123456789abcdef')
    cases = bind_cases(permit, tmp_path, monkeypatch)
    with pytest.raises(loader.AdmissionClosed):
        permit.call(test_id=cases[0].case_id, phase='target', upper_microusd=4,
                    operation=lambda: pytest.fail('replay'))
    permit.admission.ledger.db.close()
    loader.disable(path)
    loader.provision_disabled(path, record)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT enabled,consumed,reserved_microusd FROM controlled_admission').fetchone() == (0,1,4)
    assert calls == ['called']

def test_expiry_checked_on_existing_adapter_before_io(tmp_path, monkeypatch):
    path, record = setup_state(tmp_path, monkeypatch)
    arm_fixture(path)
    ctx = context(record)
    loader.bind_scanner_permit(ctx, scan_id='0123456789abcdef0123456789abcdef')
    permit = loader.load_bound_scanner_permit(ctx, scan_id='0123456789abcdef0123456789abcdef')
    cases = bind_cases(permit, tmp_path, monkeypatch)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE controlled_permit_context SET expires_at=1')
    with pytest.raises(loader.AdmissionClosed):
        permit.call(test_id=cases[0].case_id, phase='target', upper_microusd=1,
                    operation=lambda: pytest.fail('expired'))
    assert permit.admission.ledger.db.execute('SELECT reserved_microusd FROM controlled_admission').fetchone() == (0,)
    permit.admission.ledger.db.close()

def test_no_replacement_and_unsafe_storage(tmp_path, monkeypatch):
    path, record = setup_state(tmp_path, monkeypatch)
    with pytest.raises(loader.AdmissionClosed):
        loader.provision_disabled(path, dict(record, request_id='replacement'))
    path.chmod(0o644)
    with pytest.raises(loader.AdmissionClosed):
        context(record)


def test_handler_rejects_descriptor_and_unknown_permit_before_runtime(monkeypatch):
    from api.scan_handlers import scan_handler, HandlerError
    monkeypatch.delenv('ARE_CONTROLLED_PERMIT_DB', raising=False)
    for body, status in [
        ({'agent_endpoint': {'_controlled_permit': {'permit_id': 'p'*32}}}, 422),
        ({**PAYLOAD, 'controlled_permit_id': 'p'*32}, 403),
    ]:
        with pytest.raises(HandlerError) as exc:
            scan_handler(body, tenant_header='tenant', runtime=None)
        assert exc.value.status == status


def test_controlled_preflight_cannot_create_runner():
    from api.scan_handlers import connectivity_check_handler, HandlerError
    with pytest.raises(HandlerError) as exc:
        connectivity_check_handler(
            {**PAYLOAD, 'controlled_permit_id': 'p'*32},
            runner_factory=lambda *a, **k: pytest.fail('unmetered preflight'))
    assert exc.value.status == 503


def test_connectivity_authentication_precedes_handler(monkeypatch):
    import asyncio
    import httpx
    from api.app import create_app
    monkeypatch.setenv('ARE_API_AUTH_TOKENS_JSON', '{"legitimate":"tenant"}')
    app = create_app()
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            denied = await client.post('/scanner/v1/connectivity-check',
                json={**PAYLOAD, 'controlled_permit_id': 'p'*32})
            authenticated = await client.post('/scanner/v1/connectivity-check',
                headers={'Authorization': 'Bearer legitimate'},
                json={**PAYLOAD, 'controlled_permit_id': 'p'*32})
            return denied, authenticated
    denied, authenticated = asyncio.run(request())
    assert denied.status_code == 401
    assert authenticated.status_code == 403


def test_real_handler_binds_before_kickoff_and_carries_only_descriptor(tmp_path, monkeypatch):
    from api.scan_handlers import scan_handler, HandlerError
    body = dict(PAYLOAD, tenant_id='tenant',
                agent_endpoint={'host': 'agent.customer.example.com'},
                credential_ref='phase1-probe.v1', corpus_snapshot_hash='sha256:'+'a'*64,
                scanner_version='0.3.0', corpus_version='0.1.0', seed=42,
                max_cost_usd=10.0, scan_mode='live_probe', k_replicates=5)
    body['pack_id'] = 'starter-100@4'
    path, record = setup_state(tmp_path, monkeypatch)
    # Distinct offline fixture for the exact real-handler body.
    with sqlite3.connect(path) as db:
        db.execute('UPDATE controlled_admission SET target=?', (loader.fingerprint(body),))
    record['target_sha256'] = loader.fingerprint(body)
    arm_fixture(path)
    class ObservedKickoff(Exception):
        pass
    class Runtime:
        def kickoff(self, request, secret, **kwargs):
            descriptor = request.endpoint_config['_controlled_permit']
            assert set(descriptor) == {'permit_id', 'tenant_id', 'target_sha256'}
            assert secret == body['credential_secret']
            with sqlite3.connect(path) as db:
                assert db.execute('SELECT consumed,dispatch_state FROM controlled_admission').fetchone() == (1, 'dispatch_unknown')
                assert db.execute('SELECT scan_id FROM controlled_scan_binding').fetchone() == ('scan',)
            raise ObservedKickoff()
    request = dict(body, controlled_permit_id=record['permit_id'])
    with pytest.raises(ObservedKickoff):
        scan_handler(request, tenant_header='tenant', runtime=Runtime(), scan_id_factory=lambda:'scan')
    with pytest.raises(HandlerError) as exc:
        scan_handler(request, tenant_header='tenant', runtime=Runtime(), scan_id_factory=lambda:'second')
    assert exc.value.status == 403


def test_preflight_expiry_checked_before_reservation_and_io(tmp_path, monkeypatch):
    path, record = setup_state(tmp_path, monkeypatch)
    arm_fixture(path)
    admission = loader._load(record['permit_id'], 'tenant')
    try:
        with sqlite3.connect(path) as db:
            db.execute('UPDATE controlled_permit_context SET expires_at=1')
        with pytest.raises(loader.AdmissionClosed, match='expired'):
            admission.ledger.preflight_call(
                **admission.binding, upper_microusd=1,
                operation=lambda: pytest.fail('expired preflight dispatched'))
        assert admission.ledger.db.execute(
            'SELECT reserved_microusd FROM controlled_admission').fetchone() == (0,)
        assert admission.ledger.db.execute('SELECT COUNT(*) FROM controlled_preflight').fetchone() == (0,)
    finally:
        admission.ledger.db.close()


def test_missing_preflight_storage_rejected_without_repair(tmp_path, monkeypatch):
    path, record = setup_state(tmp_path, monkeypatch)
    arm_fixture(path)
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE controlled_preflight')
    with pytest.raises(loader.AdmissionClosed, match='Incomplete permit schema'):
        context(record)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='controlled_preflight'").fetchall() == []


@pytest.mark.parametrize('unknown', [False, True])
def test_bound_preflight_commits_before_io_and_never_replays(tmp_path, monkeypatch, unknown):
    path, record = setup_state(tmp_path, monkeypatch)
    arm_fixture(path)
    calls = []
    def operation():
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT reserved_microusd FROM controlled_admission').fetchone() == (123,)
            assert db.execute('SELECT state FROM controlled_preflight').fetchone() == ('check_unknown',)
        calls.append(1)
        if unknown:
            raise TimeoutError('offline unknown')
        return 'ok'
    def execute(**changes):
        args = dict(tenant_id='tenant', payload=PAYLOAD, upper_microusd=123, operation=operation)
        args.update(changes)
        return loader.execute_preflight(record['permit_id'], **args)
    with pytest.raises(loader.AdmissionClosed):
        execute(payload=dict(PAYLOAD, pack_id='wrong'))
    with pytest.raises(loader.AdmissionClosed):
        execute(tenant_id='wrong')
    with pytest.raises(loader.AdmissionClosed):
        execute(upper_microusd=None)
    assert calls == []
    if unknown:
        with pytest.raises(TimeoutError):
            execute()
    else:
        assert execute() == 'ok'
    with pytest.raises(loader.AdmissionClosed):
        execute()
    assert calls == [1]
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT state FROM controlled_preflight').fetchone() == (
            'check_unknown' if unknown else 'returned',)


def test_supported_arm_is_one_time_even_after_disable(tmp_path, monkeypatch):
    path, record = setup_state(tmp_path, monkeypatch)
    loader.arm_once(path, record)
    assert context(record)['permit_id'] == record['permit_id']
    with pytest.raises(loader.AdmissionClosed):
        loader.arm_once(path, record)
    loader.disable(path)
    with pytest.raises(loader.AdmissionClosed):
        loader.arm_once(path, record)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT enabled,consumed FROM controlled_admission').fetchone() == (0, 0)


@pytest.mark.parametrize('mutation', ['rebound', 'expired', 'consumed', 'unknown', 'preflight'])
def test_arm_refuses_changed_or_attempted_reservations(tmp_path, monkeypatch, mutation):
    path, record = setup_state(tmp_path, monkeypatch)
    if mutation == 'rebound':
        record = dict(record, request_id='other')
    else:
        with sqlite3.connect(path) as db:
            if mutation == 'expired':
                db.execute('UPDATE controlled_permit_context SET expires_at=1')
            elif mutation == 'consumed':
                db.execute('UPDATE controlled_admission SET consumed=1')
            elif mutation == 'unknown':
                db.execute("UPDATE controlled_admission SET dispatch_state='dispatch_unknown'")
            else:
                db.execute("INSERT INTO controlled_preflight VALUES(1,'check_unknown')")
    with pytest.raises(loader.AdmissionClosed):
        loader.arm_once(path, record)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT enabled FROM controlled_admission').fetchone() == (0,)


def test_cli_arm_preserves_record_and_does_not_dispatch(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    from pathlib import Path
    path, record = setup_state(tmp_path, monkeypatch)
    command = [sys.executable, str(Path(__file__).resolve().parents[1] /
               'scripts/controlled_permit_state.py'), 'arm-once', '--db', str(path)]
    first = subprocess.run(command, input=json.dumps(record), text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    second = subprocess.run(command, input=json.dumps(record), text=True, capture_output=True)
    assert second.returncode == 1
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT request_id,enabled,consumed,dispatch_state '
                          'FROM controlled_admission').fetchone() == ('request', 1, 0, 'unclaimed')
        assert db.execute('SELECT count(*) FROM controlled_scan_binding').fetchone() == (0,)
