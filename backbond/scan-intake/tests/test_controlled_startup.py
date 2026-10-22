"""Offline protected startup checks, preserving disabled/consumed state."""
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "server"))
import pytest
from controlled_admission import ControlledAdmission, OperatorAdmission, AdmissionClosed
from controlled_startup import load_operator


def fixture_config(tmp_path, monkeypatch):
    folder = tmp_path / 'private'
    folder.mkdir(mode=0o700)
    ledger_path = folder / 'ledger.sqlite'
    ledger = ControlledAdmission(str(ledger_path))
    binding = dict(actor='operator', request_id='request', target='a'*64)
    ledger.provision(**binding)
    OperatorAdmission(ledger, **binding, token_sha256=hashlib.sha256(b'offline').hexdigest())
    ledger.db.close()
    ledger_path.chmod(0o600)
    config = dict(ledger_path=str(ledger_path), scanner_permit_id='p'*32, **binding, token_sha256=hashlib.sha256(b'offline').hexdigest())
    path = folder / 'startup.json'
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    monkeypatch.setenv('QA_CONTROLLED_OPERATOR_CONFIG', str(path))
    monkeypatch.setenv('QA_READY', '0')
    monkeypatch.setenv('QA_TERMS_APPROVED', '0')
    return path, config


def test_default_disabled(monkeypatch):
    monkeypatch.delenv('QA_CONTROLLED_OPERATOR_CONFIG', raising=False)
    assert load_operator() is None


def test_load_preserves_disabled_state(tmp_path, monkeypatch):
    path, config = fixture_config(tmp_path, monkeypatch)
    payload = {'agent_endpoint': {'url': 'https://offline.invalid'}, 'credential_secret': 'offline'}
    config['target'] = OperatorAdmission.fingerprint(payload)
    path.write_text(json.dumps(config))
    import sqlite3
    with sqlite3.connect(config['ledger_path']) as db:
        db.execute('UPDATE controlled_admission SET target=?', (config['target'],))
    operator = load_operator()
    import sqlite3
    with sqlite3.connect(operator.path) as db:
        assert db.execute('SELECT enabled,consumed FROM controlled_admission').fetchone() == (0,0)
    operator.authenticate_http({'authorization':'Bearer offline', 'x-controlled-request-id':'request'})
    with pytest.raises(AdmissionClosed):
        operator.claim_http({'authorization':'Bearer offline', 'x-controlled-request-id':'request'}, payload)


@pytest.mark.parametrize('bad', ['ready','terms','mode','missing','symlink','mismatch','duplicate'])
def test_rejects_bad_configuration(tmp_path, monkeypatch, bad):
    path, config = fixture_config(tmp_path, monkeypatch)
    if bad == 'ready': monkeypatch.setenv('QA_READY','1')
    elif bad == 'terms': monkeypatch.setenv('QA_TERMS_APPROVED','1')
    elif bad == 'mode': path.chmod(0o644)
    elif bad == 'missing': path.unlink()
    elif bad == 'symlink':
        link = path.parent / 'link'
        link.symlink_to(path)
        monkeypatch.setenv('QA_CONTROLLED_OPERATOR_CONFIG',str(link))
    elif bad == 'mismatch':
        config['request_id'] = 'wrong'
        path.write_text(json.dumps(config))
    elif bad == 'duplicate': path.write_text('{"actor":"a","actor":"b"}')
    with pytest.raises(AdmissionClosed): load_operator()


def test_startup_adapter_dispatches_in_worker_thread_and_rejects_restart(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    path, config = fixture_config(tmp_path, monkeypatch)
    payload = {'agent_endpoint': {'url': 'https://offline.invalid'},
               'credential_secret': 'offline'}
    config['target'] = OperatorAdmission.fingerprint(payload)
    path.write_text(json.dumps(config))
    with sqlite3.connect(config['ledger_path']) as db:
        db.execute('UPDATE controlled_admission SET target=?,enabled=1', (config['target'],))
    operator = load_operator()
    headers = {'authorization': 'Bearer offline', 'x-controlled-request-id': 'request'}
    operator.claim_http(headers, payload)
    operator.bind_scan('offline-scan')
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(operator.before_dispatch, 'offline-scan', payload).result()
    restarted = load_operator()
    with pytest.raises(AdmissionClosed):
        restarted.claim_http(headers, payload)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(AdmissionClosed):
            pool.submit(restarted.before_dispatch, 'offline-scan', payload).result()
    with sqlite3.connect(config['ledger_path']) as db:
        assert db.execute('SELECT consumed,dispatch_state FROM controlled_admission').fetchone() == (1, 'dispatch_unknown')


def test_actual_http_route_startup_queue_and_lost_response(tmp_path, monkeypatch):
    import runpy
    import sqlite3
    from pathlib import Path
    from types import SimpleNamespace as NS
    helpers = runpy.run_path(str(Path(__file__).with_name('test_findings_dispatch.py')))
    _, store, queue, calls, body, env = helpers['fixture'](tmp_path)
    path, config = fixture_config(tmp_path, monkeypatch)
    payload = {'agent_endpoint': {'url': body.endpoint_url}, 'credential_secret': 'offline'}
    config['target'] = OperatorAdmission.fingerprint(payload)
    path.write_text(json.dumps(config))
    with sqlite3.connect(config['ledger_path']) as db:
        db.execute('UPDATE controlled_admission SET target=?,enabled=1', (config['target'],))
    operator = load_operator()
    env.update(controlled_operator=operator, AdmissionClosed=AdmissionClosed,
               _flag=lambda *a: False, os=NS(environ={'QA_READY': '0'}),
               _payload=lambda _: dict(payload))
    claim = helpers['function']('_controlled_claim', env)
    env['_controlled_claim'] = claim
    queue.before_dispatch = helpers['function']('_controlled_dispatch', env)
    route = helpers['function']('start_scan', env)
    bad = NS(headers={'authorization': 'Bearer ordinary', 'x-controlled-request-id': 'request'})
    with pytest.raises(helpers['HTTPError']) as exc:
        route(body, bad)
    assert exc.value.status_code == 403
    assert not calls
    request = NS(headers={'authorization': 'Bearer offline', 'x-controlled-request-id': 'request'})
    def unknown(payload):
        calls.append(payload)
        raise TimeoutError('acceptance response lost')
    queue.start = unknown
    result = route(body, request)
    assert result['status'] == 'dispatch_unknown'
    assert len(calls) == 1
    env['controlled_operator'] = load_operator()
    with pytest.raises(helpers['HTTPError']):
        route(body, request)
    assert len(calls) == 1
    assert store.status(result['status_token'])['status'] == 'dispatch_unknown'
    assert env['os'].environ == {'QA_READY': '0'}
