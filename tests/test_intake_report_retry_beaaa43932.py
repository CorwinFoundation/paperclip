"""Report recovery retains execution and cannot dispatch another scan."""
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backbond/scan-intake/server'))
from qa_service import IntakeStore, IntakeError, translate_callback

@pytest.fixture
def recovery(tmp_path):
    store = IntakeStore(str(tmp_path / 'intake.sqlite'))
    store.db.execute('PRAGMA foreign_keys=OFF')
    token = 'owner_status_token_123456789'
    store.db.execute("INSERT INTO scans(id,code,partner,email,agent_label,endpoint_host,transport,pack_version,created_at,status,scanner_id,status_token_hash) VALUES('s1','c','p','e@x.test','a','h','http','starter-100@4','2026-10-27T00:00:00+00:00','running','remote',?)", (hashlib.sha256(token.encode()).hexdigest(),))
    payload = {'scan_id':'remote','status':'complete','starter_pack_id':'starter-100@4', 'result_rows':[{'test_id':str(i),'family':'prompt_injection','prompt':'x','reply':'y','verdict':'passed'} for i in range(100)]}
    failed = dict(payload, incomplete=True, incomplete_reason='report_preparation_error:ValueError')
    report_token = store.finish('s1', translate_callback(failed))
    return store, token, payload, failed, report_token


def test_guarded_refresh_is_concurrent_and_preserves_identity(recovery):
    store, token, payload, failed, report_token = recovery
    before = dict(store.scan('s1'))
    assert not store.refresh_report_failure('s1', 'remote', failed)
    changed = json.loads(json.dumps(payload)); changed['result_rows'][0]['reply'] = 'changed'
    with pytest.raises(IntakeError): store.refresh_report_failure('s1', 'remote', changed)
    with pytest.raises(IntakeError): store.refresh_report_failure('s1', 'foreign', payload)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.refresh_report_failure('s1', 'remote', payload), range(4)))
    assert results.count(True) == 1
    after = dict(store.scan('s1'))
    assert after == {**before, 'outcome':'complete', 'incomplete_reason':None}
    assert store.report(report_token)['counts']['passed'] == 100
    assert store.db.execute('SELECT count(*) FROM mail_outbox').fetchone()[0] == 0


def test_retry_private_errors_reload_and_no_dispatch(recovery, monkeypatch):
    store, token, payload, failed, report_token = recovery
    monkeypatch.setenv('QA_DB_PATH', store.db.execute('PRAGMA database_list').fetchone()[2])
    import app
    from fastapi.testclient import TestClient
    monkeypatch.setattr(app, 'store', store)
    monkeypatch.setattr(app, '_headers', lambda value: {'Authorization': value['tenant_id']})
    monkeypatch.setattr(app, '_engine_progress', lambda _: {})
    monkeypatch.setattr(app, 'queue', object())  # Any dispatch/callback is a test failure.
    calls=[]
    def post(url, **kw):
        calls.append((url,kw))
        raise RuntimeError('secret renderer traceback')
    monkeypatch.setattr(app.httpx, 'post', post)
    client=TestClient(app.app)
    assert client.post('/scan/v1/retry-report',json={}).status_code == 422
    assert client.post('/scan/v1/retry-report',json={'status_token':'foreign_token_123456789'}).status_code == 404
    for _ in range(2):
        response=client.post('/scan/v1/retry-report',json={'status_token':token})
        assert response.status_code == 503 and 'secret' not in response.text
        reopened=IntakeStore(store.db.execute('PRAGMA database_list').fetchone()[2])
        assert len(json.loads(reopened.scan('s1')['tests_json'])) == 100
        reopened.db.close()
    class Receipt: status_code=200
    monkeypatch.setattr(app.httpx,'post',lambda url,**kw: (calls.append((url,kw)) or Receipt()))
    monkeypatch.setattr(app,'_terminal_scan',lambda row: payload)
    assert client.post('/scan/v1/retry-report',json={'status_token':token}).status_code == 200
    assert client.post('/scan/v1/retry-report',json={'status_token':token}).status_code == 409
    assert len(calls) == 3
    assert all(url.endswith('/scans/remote/retry-report') and kw['headers']['Authorization']=='partner-p' for url,kw in calls)
    assert store.scan('s1')['report_token'] == report_token


def test_findings_worker_refreshes_finished_failure_once(recovery):
    from findings_intake import FindingsIntake
    store, token, payload, failed, report_token = recovery
    findings=FindingsIntake(store.db.execute('PRAGMA database_list').fetchone()[2])
    findings.accept('s1','starter-100@4')
    fetched=[]
    def terminal(row):
        fetched.append(row['scanner_id'])
        return payload
    for now in (100, 140):
        findings.capture(store, object(), terminal, lambda _: {}, now=now)
    assert fetched == ['remote']
    assert store.scan('s1')['outcome'] == 'complete'
    assert store.scan('s1')['report_token'] == report_token
    assert findings.accepted_scope('s1') == 'starter-100@4'
