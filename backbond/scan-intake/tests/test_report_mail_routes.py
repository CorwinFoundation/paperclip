import base64
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))
from report_mail_access import ReportMailAccess
from report_mail_routes import router


@pytest.fixture
def service(tmp_path):
    box = ReportMailAccess(str(tmp_path / 'mail.sqlite'))
    config = dict(enabled=True, tenant_id='bac', key_id='key',
                  founders=['founder@example.com'], public_origin='https://testserver')
    record = SimpleNamespace(tenant_id='bac', key_id='key', status='active',
                             expires_at=9999999999, scopes=['POST /scan/v1/reports/mail'])
    gateway = SimpleNamespace(resolve_record=lambda t: record if t == 'sealed' else None)
    transport = Mock(return_value='message-id')
    app = FastAPI()
    app.include_router(router(outbox=box, gateway=lambda: gateway, settings=lambda: config,
                              credentials=lambda: {}, transport=transport))
    return TestClient(app, base_url='https://testserver'), box, config, record, transport


def payload():
    value = dict(schema_version='report_mail@1', mode='founders', target_name='Target',
                 run_date='2026-10-23', note='Review this report', recipients=[], approval_ref=None)
    for kind, raw in [('pdf', b'%PDF-1.7 exact bytes'), ('html', b'<script>danger()</script>')]:
        value[kind + '_base64'] = base64.b64encode(raw).decode()
        value[kind + '_sha256'] = hashlib.sha256(raw).hexdigest()
    return value


def submit(client):
    return client.post('/scan/v1/reports/mail', json=payload(), headers={'Authorization': 'Bearer sealed'})


def test_auth_hold_validation_and_retry(service):
    client, box, config, record, transport = service
    assert client.post('/scan/v1/reports/mail', json=payload()).status_code == 403
    record.scopes = []
    assert submit(client).status_code == 403
    record.scopes = ['POST /scan/v1/reports/mail']
    config['enabled'] = False
    assert submit(client).status_code == 503
    transport.assert_not_called()
    config['enabled'] = True
    response = submit(client)
    assert response.status_code == 200
    assert submit(client).json() == response.json()
    transport.assert_called_once()
    assert transport.call_args.kwargs['pdf'] == b'%PDF-1.7 exact bytes'
    assert 'founder@example.com:' not in transport.call_args.kwargs['text']


def test_recipient_challenge_session_and_opaque_html(service):
    client, box, config, record, transport = service
    ident = submit(client).json()['request_id']
    route = '/scan/v1/mailed-reports/' + ident
    assert b'Verify your report access' in client.get(route).content
    headers = {'Origin': 'https://testserver'}
    assert client.post(route + '/verification', json={'email': 'founder@example.com'}).status_code == 403
    before = transport.call_count
    unknown = client.post(route + '/verification', json={'email': 'outsider@example.com'}, headers=headers)
    assert unknown.status_code == 200 and transport.call_count == before
    known = client.post(route + '/verification', json={'email': 'founder@example.com'}, headers=headers)
    assert known.json() == unknown.json()
    code = transport.call_args.kwargs['text'].split()[-1]
    assert client.post(route + '/session', json={'code': '000000' if code != '000000' else '111111'}, headers=headers).status_code == 403
    assert client.post(route + '/session', json={'code': code}, headers=headers).status_code == 200
    assert client.post(route + '/session', json={'code': code}, headers=headers).status_code == 403
    response = client.get(route)
    assert response.content == b'<script>danger()</script>'
    assert response.headers['content-disposition'].startswith('attachment;')
    assert 'sandbox' in response.headers['content-security-policy']
    assert client.get(route + '?format=pdf').content == b'%PDF-1.7 exact bytes'
    with TestClient(client.app, base_url='https://testserver') as outsider:
        assert b'Verify your report access' in outsider.get(route).content


def test_expiry_attempt_limit_and_report_binding(tmp_path):
    box = ReportMailAccess(str(tmp_path / 'mail.sqlite'))
    ident = box.submit(payload(), actor='key', founders=['founder@example.com'],
                       links={'founder@example.com': 'https://testserver/report'})
    challenge, code = box.issue(ident, 'founder@example.com', now=100)
    assert box.verify('0' * 64, challenge, code, now=101) is None
    wrong = '000000' if code != '000000' else '111111'
    for _ in range(5):
        assert box.verify(ident, challenge, wrong, now=102) is None
    assert box.verify(ident, challenge, code, now=103) is None
    challenge, code = box.issue(ident, 'founder@example.com', now=500)
    assert box.verify(ident, challenge, code, now=1100) is None
    challenge, code = box.issue(ident, 'founder@example.com', now=1200)
    session = box.verify(ident, challenge, code, now=1201)
    assert box.artifact(ident, session, 'pdf', now=1202)
    assert box.artifact(ident, session, 'pdf', now=3001) is None


def test_body_bound_and_customer_approval_fail_before_provider(service, monkeypatch):
    client, box, config, record, transport = service
    body = payload()
    body.update(mode='customer', recipients=['customer@example.com'])
    assert client.post('/scan/v1/reports/mail', json=body,
                       headers={'Authorization': 'Bearer sealed'}).status_code == 422
    transport.assert_not_called()
    body['approval_ref'] = 'BAC-74'
    assert client.post('/scan/v1/reports/mail', json=body,
                       headers={'Authorization': 'Bearer sealed'}).status_code == 200
    assert transport.call_args.kwargs['cc'] == ['founder@example.com']
    monkeypatch.setattr('report_mail_routes.MAX_BODY', 10)
    assert submit(client).status_code == 413
    assert transport.call_count == 1


def test_existing_verifier_budgets_and_idle_expiry(tmp_path):
    box = ReportMailAccess(str(tmp_path / 'mail.sqlite'))
    ident = box.submit(payload(), actor='key', founders=['founder@example.com'],
                       links={'founder@example.com': 'https://testserver/report'})
    for _ in range(5):
        assert box.issue(ident, 'founder@example.com', request_key='client', now=100)
    assert box.issue(ident, 'founder@example.com', request_key='client', now=101) is None
    challenge, code = box.issue(ident, 'founder@example.com', request_key='recipient', now=102)
    session = box.verify(ident, challenge, code, now=103)
    assert box.artifact(ident, session, 'html', now=104)
    assert box.artifact(ident, session, 'html', now=704) is None


@pytest.mark.parametrize('denial', ['idle', 'revoked', 'invalid', 'mismatch'])
def test_browser_session_recovery(service, monkeypatch, denial):
    client, box, config, record, transport = service
    clock = [1000.0]
    monkeypatch.setattr('qa_service.time.time', lambda: clock[0])
    ident = submit(client).json()['request_id']
    route = '/scan/v1/mailed-reports/' + ident
    headers = {'Origin': 'https://testserver'}

    def issue_and_verify():
        assert client.post(route + '/verification', json={'email': 'founder@example.com'},
                           headers=headers).status_code == 200
        code = transport.call_args.kwargs['text'].split()[-1]
        challenge = client.cookies.get('bb_mailed_challenge', domain='testserver.local', path=route)
        assert client.post(route + '/session', json={'code': code}, headers=headers).status_code == 200
        # Replay even with the original challenge cookie is denied.
        client.cookies.set('bb_mailed_challenge', challenge, domain='testserver.local', path=route)
        assert client.post(route + '/session', json={'code': code}, headers=headers).status_code == 403
        return client.cookies.get('bb_mailed_session', domain='testserver.local', path=route)

    session = issue_and_verify()
    assert client.get(route).content == b'<script>danger()</script>'
    if denial == 'idle':
        clock[0] += 601  # Actual verifier idle expiry; browser cookie is still retained.
    elif denial == 'revoked':
        box.recipients.revoke_report_session(session)
    elif denial == 'invalid':
        client.cookies.set('bb_mailed_session', 'malformed.invalid', domain='testserver.local', path=route)
    else:
        other = payload()
        other['target_name'] = 'Other report'
        other_id = client.post('/scan/v1/reports/mail', json=other,
                              headers={'Authorization': 'Bearer sealed'}).json()['request_id']
        route = '/scan/v1/mailed-reports/' + other_id
        # Deliberately present the original report's session at the other route.
        client.cookies.set('bb_mailed_session', session, domain='testserver.local', path=route)
    assert client.cookies.get('bb_mailed_session', domain='testserver.local', path=route)
    denied = client.get(route + '?format=pdf')
    assert denied.status_code == 200
    assert b'Verify your report access' in denied.content
    assert b'%PDF' not in denied.content and b'danger()' not in denied.content
    assert 'content-disposition' not in denied.headers
    assert 'Max-Age=0' in denied.headers['set-cookie']
    assert 'Path=' + route in denied.headers['set-cookie']
    assert client.cookies.get('bb_mailed_session', domain='testserver.local', path=route) is None
    # Missing/foreign origin and a nonrecipient cannot renew authority.
    before = transport.call_count
    for origin in [None, 'https://attacker.example']:
        h = {} if origin is None else {'Origin': origin}
        assert client.post(route + '/verification', json={'email': 'founder@example.com'}, headers=h).status_code == 403
        assert client.post(route + '/session', json={'code': '123456'}, headers=h).status_code == 403
    assert client.post(route + '/verification', json={'email': 'outsider@example.com'}, headers=headers).status_code == 200
    assert transport.call_count == before
    assert b'Verify your report access' in client.get(route).content
    issue_and_verify()
    assert client.get(route + '?format=pdf').content == b'%PDF-1.7 exact bytes'


def test_recovery_preserves_route_attempt_and_issuance_budgets(service):
    client, box, config, record, transport = service
    route = '/scan/v1/mailed-reports/' + submit(client).json()['request_id']
    headers = {'Origin': 'https://testserver'}
    for attempt in range(5):
        before = transport.call_count
        assert client.post(route + '/verification', json={'email': 'founder@example.com'}, headers=headers).status_code == 200
        assert transport.call_count == before + 1
        code = transport.call_args.kwargs['text'].split()[-1]
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(5):
            assert client.post(route + '/session', json={'code': wrong}, headers=headers).status_code == 403
        assert client.post(route + '/session', json={'code': code}, headers=headers).status_code == 403
        assert b'Verify your report access' in client.get(route).content
    before = transport.call_count
    assert client.post(route + '/verification', json={'email': 'founder@example.com'}, headers=headers).status_code == 200
    assert transport.call_count == before
    assert b'Verify your report access' in client.get(route + '?format=pdf').content
