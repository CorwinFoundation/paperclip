"""ASGI parsing boundary with inert service-owned binding; no network or activation."""
import hashlib
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from test_operations_status import app_env
from controlled_admission import ControlledAdmission, OperatorAdmission


def test_contextual_target_guard(app_env, tmp_path, monkeypatch):
    app = app_env[0]
    monkeypatch.setenv('QA_READY', '0')
    monkeypatch.setenv('QA_TERMS_APPROVED', '0')
    fields = dict(code='TEST', email='fixture@example.test', agent_label='fixture',
                  endpoint_url='https://api.backbond.ai/selected/full/path', transport='rest',
                  auth={'type':'bearer', 'value':'inert-target-secret'},
                  consent_owner=True, consent_terms=True, terms_version='fixture',
                  pack_id='starter-100@4')
    # Build the administrative fixture using an inert non-protected endpoint.
    parsed = app.ScanRequest.model_validate(dict(fields, endpoint_url='https://fixture.invalid/'))
    clean = parsed.model_dump()
    clean['endpoint_url'] = fields['endpoint_url']
    clean.pop('connection_proof')
    clean['partner'] = 'test'
    ledger = ControlledAdmission(str(tmp_path / 'one.sqlite'))
    binding = dict(actor='fixture', request_id='request', target=OperatorAdmission.fingerprint(app._payload(clean)))
    ledger.provision(**binding)  # stays disabled; validation never claims admission
    operator = OperatorAdmission(ledger, **binding,
        token_sha256=hashlib.sha256(b'inert-operator').hexdigest())
    probe = FastAPI()
    @probe.post('/parse')
    def parse(body=Depends(app._scan_request)):
        return {'validated': True}
    client = TestClient(probe)
    headers = {'authorization':'Bearer inert-operator', 'x-controlled-request-id':'request'}
    assert client.post('/parse', json=fields).status_code == 422
    monkeypatch.setattr(app, 'controlled_operator', operator)
    assert client.post('/parse', json=fields).status_code == 403
    assert client.post('/parse', json=fields, headers=headers).status_code == 200
    for changes in ({'endpoint_url':fields['endpoint_url']+'-other'},
                    {'auth':{'type':'bearer','value':'wrong'}}, {'pack_id':'starter-100@5'}):
        assert client.post('/parse', json=dict(fields, **changes), headers=headers).status_code == 403
    for changes in ({'consent_owner':False}, {'consent_terms':False},
                    {'transport':'openai_chat'}, {'caller_override':True}):
        assert client.post('/parse', json=dict(fields, **changes), headers=headers).status_code in {403,422}
    # Shared connection parsing retains its unconditional host denial.
    with pytest.raises(ValueError):
        app.ScanRequest.model_validate(fields)
    assert ledger.db.execute('SELECT consumed FROM controlled_admission').fetchone() == (0,)
    monkeypatch.setenv('QA_READY', '1')
    assert client.post('/parse', json=fields, headers=headers).status_code == 503
