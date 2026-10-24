"""The actual public connection route must preserve AG-UI configuration and proof binding."""
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_operations_status import app_env  # noqa: E402,F401


@pytest.mark.parametrize('valid', [True, False])
def test_agui_connection_has_no_scan_side_effects_and_only_valid_runs_issue_proof(app_env, monkeypatch, valid):
    app, store, *_ = app_env
    monkeypatch.setenv('QA_READY', '1')
    monkeypatch.setattr(app, 'controlled_operator', None)
    calls = []
    def check(payload):
        calls.append(payload['agent_endpoint'].copy())
        return valid, None if valid else 'connection failed'
    monkeypatch.setattr(app, '_scanner_check', check)
    fields = {'code': 'TEST', 'email': 'agui@example.test', 'agent_label': 'AG-UI agent',
              'endpoint_url': 'https://agent.example.test/crew_chat', 'transport': 'ag_ui', 'auth': {'type': 'none'}}
    before = store.db.execute('SELECT count(*) FROM scans').fetchone()[0]
    response = TestClient(app.app).post('/scan/v1/connection-checks', json=fields)
    assert response.status_code == 200
    result = response.json()
    assert result['ok'] is valid
    assert len(calls) == 1 and calls[0]['transport'] == 'ag_ui' and calls[0]['auth'] == {'type': 'none'}
    assert store.db.execute('SELECT count(*) FROM scans').fetchone()[0] == before
    if valid:
        bound = app.ConnectionFields.model_validate(fields).model_dump()
        assert not app.proofs.redeem(result['connection_proof'], dict(bound, transport='rest'))
    else:
        assert 'connection_proof' not in result


def test_intake_to_scan_payload_retains_selected_transport_and_exact_endpoint(app_env):
    app, *_ = app_env
    fields = app.ScanRequest.model_validate({'code': 'TEST', 'email': 'agui@example.test',
        'agent_label': 'AG-UI agent', 'endpoint_url': 'https://agent.example.test/crew_chat',
        'transport': 'ag_ui', 'auth': {'type': 'none'}, 'consent_owner': True, 'consent_terms': True})
    payload = app._payload(dict(fields.model_dump(), partner='test'))
    assert payload['agent_endpoint'] == {'url': fields.endpoint_url, 'transport': 'ag_ui', 'auth': {'type': 'none'}}
    assert payload['include_result_rows'] is True
