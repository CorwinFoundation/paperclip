"""Finished native SHIP -> durable email -> recipient authentication -> exact bytes."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))
from findings_client import FindingsReports
from findings_workflow import WorkflowError, digest
from native_reports import NativeReports
from test_findings_workflow import setup, publish, MailHTTP


def approved():
    return {'workflow_id': 'approved1', 'state': 'approved', 'artifacts': {
        kind: {'content': base64.b64encode(data).decode(), 'sha256': digest(data)}
        for kind, data in {'pdf': b'%PDF-exact-approved', 'html': b'<!doctype html><p>Exact approved report</p>'}.items()}}


def test_native_ship_through_mail_authentication_and_exact_artifacts(tmp_path, monkeypatch):
    native, flow, value = setup(tmp_path)
    publish(native, flow, value)
    flow.tick(now=20)
    path = str(tmp_path / 'intake.sqlite')
    outbox = FindingsReports(path)
    outbox.enqueue(value)
    transport = MailHTTP(flow)
    kwargs = dict(base_url='https://bac.example', token='integration', http=transport,
                  mail_credentials=lambda: {'provider': 'resend', 'api_key': 'test-only'})
    outbox.tick(now=100, **kwargs)
    assert transport.sends[0][1]['to'] == ['info@backbond.ai']
    assert transport.sends[0][1]['from'] == 'BackBond <reports@backbond.ai>'
    assert transport.sends[0][1]['reply_to'] == 'info@backbond.ai'
    token = outbox.native.token_for('scan1')
    assert 'https://backbond.ai/scan/r/' + token in transport.sends[0][1]['text']
    assert 'attachments' not in transport.sends[0][1]

    monkeypatch.setenv('QA_DB_PATH', str(tmp_path / 'app-import.sqlite'))
    app = importlib.import_module('app')
    monkeypatch.setattr(app, 'findings_reports', outbox)
    monkeypatch.setattr(app, 'report_issue_gate', type('Gate', (), {'allow': lambda *a, **kw: True})())
    codes = []
    monkeypatch.setattr(app, '_send_report_code', lambda recipient, code: codes.append((recipient, code)))
    browser = TestClient(app.app, base_url='https://api.backbond.ai')
    route = '/scan/v1/reports/' + token
    origin = {'Origin': 'https://backbond.ai'}
    assert browser.get(route).status_code == 401
    assert browser.get(route + '/artifacts/html').status_code == 401
    assert browser.post(route + '/verification', headers={'Origin': 'https://attacker.example'}).status_code == 403
    assert not codes
    assert browser.post(route + '/verification', headers=origin).status_code == 200
    assert codes[0][0] == 'info@backbond.ai'
    assert browser.post(route + '/session', headers=origin, json={'code': 'not-six-digits'}).status_code == 422
    session_response = browser.post(route + '/session', headers=origin, json={'code': codes[0][1]})
    assert session_response.status_code == 200
    session_cookie = session_response.headers['set-cookie']
    assert all(flag in session_cookie for flag in ['Secure', 'HttpOnly', 'SameSite=strict'])
    metadata = browser.get(route)
    assert metadata.json()['report_kind'] == 'native_findings'
    for kind in ['pdf', 'html']:
        artifact = browser.get(route + '/artifacts/' + kind)
        assert artifact.status_code == 200 and artifact.content == native.files[kind][1]
        assert artifact.headers['cache-control'] == 'no-store'
    # Direct artifact links must retain the complete document sandbox, even
    # when a browser cannot display the embedded preview.
    policy = browser.get(route + '/artifacts/html').headers['content-security-policy']
    assert 'sandbox' in [directive.strip() for directive in policy.split(';')]
    assert "default-src 'none'" in policy
    assert 'allow-scripts' not in policy and 'allow-same-origin' not in policy
    assert "form-action 'none'" in policy
    foreign = outbox.native.publish('approved1', approved())
    assert browser.get('/scan/v1/reports/' + foreign + '/artifacts/html',
                       headers={'Cookie': 'bb_report_session=' + browser.cookies.get('bb_report_session')}).status_code == 403
    assert browser.delete(route + '/session', headers=origin).status_code == 204
    assert browser.get(route).status_code == 401
    restarted = FindingsReports(path)
    restarted.tick(now=120, **kwargs)
    restarted.tick(now=140, **kwargs)
    # The separate publication above is explicitly queued; scan1 itself sends once.
    assert sum(token in send[1]['text'] for send in transport.sends) == 1
    assert flow.status('scan1')['state'] == 'sent'


def test_publication_rejects_unapproved_changed_or_corrupt_artifacts(tmp_path):
    reports = NativeReports(str(tmp_path / 'reports.sqlite'))
    for state in ['writing', 'reviewing', 'needs_attention']:
        value = approved(); value['state'] = state
        with pytest.raises(WorkflowError, match='not_approved'):
            reports.publish('approved1', value)
    value = approved(); value['artifacts']['pdf']['sha256'] = '0' * 64
    with pytest.raises(WorkflowError, match='artifact_mismatch'):
        reports.publish('approved1', value)
    token = reports.publish('approved1', approved())
    value = approved(); value['artifacts']['html'] = {'content': base64.b64encode(b'changed').decode(), 'sha256': digest(b'changed')}
    with pytest.raises(WorkflowError, match='published_artifact_conflict'):
        reports.publish('approved1', value)
    assert reports.token_for('approved1') == token


def test_auth_expiry_replay_limits_and_concurrent_publication(tmp_path):
    path = str(tmp_path / 'reports.sqlite')
    first, second = NativeReports(path), NativeReports(path)
    with ThreadPoolExecutor(2) as pool:
        tokens = list(pool.map(lambda service: service.publish('approved1', approved()), [first, second]))
    assert tokens[0] == tokens[1]
    token = tokens[0]
    now = time.time()
    recipient, challenge = first.issue_code(token, 'browser', now=now)
    assert recipient == 'info@backbond.ai'
    with ThreadPoolExecutor(2) as pool:
        sessions = list(pool.map(lambda service: service.verify_code(token, challenge, now=now + 1), [first, second]))
    assert sum(session is not None for session in sessions) == 1
    session = next(s for s in sessions if s)
    assert first.authorize(token, session, now=now + 2)
    assert not first.authorize(token, session, now=now + 603)
    for _ in range(4):
        assert first.issue_code(token, 'browser', now=now + 5)
    assert first.issue_code(token, 'browser', now=now + 5) is None
    _, challenge = first.issue_code(token, 'another-browser', now=now)
    assert first.verify_code(token, challenge, now=now + 601) is None
    assert first.publication(token, now=now + 181 * 86400) is None
    first.purge_expired(now=now + 181 * 86400)
    assert first.token_for('approved1') is None
    assert first.db.execute('SELECT count(*) FROM native_report_notifications').fetchone()[0] == 0


def test_legacy_uncertain_attachment_send_retains_its_key_and_recipient(tmp_path):
    native, flow, value = setup(tmp_path); publish(native, flow, value); flow.tick(now=20)
    outbox = FindingsReports(str(tmp_path / 'intake.sqlite')); outbox.enqueue(value)
    outbox.update('scan1', state='unknown', send_started=50)
    transport = MailHTTP(flow)
    outbox.tick(now=100, base_url='https://bac.example', token='integration', http=transport,
                mail_credentials=lambda: {'provider': 'resend', 'api_key': 'test'})
    key, body = transport.sends[0]
    assert key.startswith('findings-report:')
    assert body['to'] == ['owner@example.com'] and len(body['attachments']) == 2
    assert outbox.native.token_for('scan1') is None


def test_verification_code_provider_payload(tmp_path, monkeypatch):
    from unittest.mock import patch
    import httpx
    monkeypatch.setenv('QA_DB_PATH', str(tmp_path / 'code-app.sqlite'))
    app = importlib.import_module('app')
    credentials = tmp_path / 'mail.json'
    credentials.write_text(json.dumps({'provider': 'resend', 'api_key': 'test-only',
                                     'from': 'BackBond <info@backbond.ai>',
                                     'reply_to': 'wrong@example.com'}))
    monkeypatch.setattr(app, '_mail_credentials_path', lambda: credentials)
    with patch('report_mail_transport.httpx.Client') as client:
        post = client.return_value.__enter__.return_value.post
        post.return_value = httpx.Response(200, json={'id': 'code-message'})
        app._send_report_code('info@backbond.ai', '123456')
        payload = post.call_args.kwargs['json']
        assert payload['from'] == 'BackBond <reports@backbond.ai>'
        assert payload['reply_to'] == 'info@backbond.ai'
        assert payload['to'] == ['info@backbond.ai']
        assert '123456' in payload['text']
