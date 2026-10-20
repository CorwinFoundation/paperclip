"""Actual backend route responses consumed by maintained page; fake transport only."""
import json
import subprocess
from pathlib import Path
from test_operations_status import app_env, page_status, bind, EMAIL


def test_metadata_response_roundtrip(app_env, tmp_path):
    app, store, queue, bac, outbox, ops, status = app_env
    res = store.reserve(code='TEST', email=EMAIL, agent_label='Agent', endpoint_url='https://agent.example.test', transport='rest')
    sid, token = res['scan_id'], res['status_token']
    queue.defer_bac(sid, sid, {})
    web = Path(__file__).parents[1] / 'web'
    def render(name, expected, hidden, metadata):
        response = page_status(app, token).json()
        fixture = tmp_path / (name + '.json')
        fixture.write_text(json.dumps(dict(response=response, expected_text=[expected], cancel_hidden=hidden, expected_metadata=metadata)))
        result = subprocess.run(['node', str(web/'verify.cjs'), str(web/'scan.js'), str(fixture)], check=True, capture_output=True, text=True)
        assert 'supplied backend response rendered' in result.stdout
        return response
    pending = render('pending', 'Request confirmation pending', True, {'accepted-scope':'Unavailable', 'confirmed-progress':'Unavailable'})
    assert pending['request_reference'].startswith('BB-')
    bind(bac, sid)
    payload = json.dumps({'description': json.dumps({'scope_ref':'starter-100@4'})})
    bac.db.execute('UPDATE bac_intake SET payload=? WHERE request_id=?', (payload,sid))
    render('acknowledged-only','Request confirmation pending',True,{'accepted-scope':'starter-100@4','case-reference':pending['request_reference']})
    queue.awaiting_bac.pop(sid)
    queue.pending[sid]=({},0)
    bac.db.execute('INSERT OR REPLACE INTO bac_confirmed_progress VALUES (?,?,?)',(sid,3,'2026-10-20T10:00:00+00:00'))
    render('confirmed-queue','Your scan is queued. You can cancel before dispatch.',False,{'accepted-scope':'starter-100@4','case-reference':pending['request_reference'],'confirmed-progress':'2026-10-20T10:00:00.000Z'})


def test_cancel_requires_durable_ack_and_execution_queue(app_env):
    from fastapi.testclient import TestClient
    app, store, queue, bac, outbox, ops, status = app_env
    res = store.reserve(code='TEST', email=EMAIL, agent_label='Agent',
                        endpoint_url='https://agent.example.test', transport='rest')
    sid, token = res['scan_id'], res['status_token']
    queue.defer_bac(sid, sid, {})
    client = TestClient(app.app)

    def cancel():
        return client.post('/scan/v1/scan-cancel', json={'status_token': token})

    # A direct POST cannot remove a request while BAC persistence is uncertain.
    assert cancel().status_code == 409
    assert sid in queue.awaiting_bac and store.scan(sid)['status'] == 'queued'
    # Even an execution-queue entry cannot bypass durable BAC acknowledgement.
    waiting = queue.awaiting_bac.pop(sid)
    queue.pending[sid] = ({}, 0)
    assert cancel().status_code == 409
    assert sid in queue.pending and store.scan(sid)['status'] == 'queued'
    queue.pending.pop(sid)
    queue.awaiting_bac[sid] = waiting
    bind(bac, sid)
    # BAC acknowledgement alone is not execution-queue membership.
    assert cancel().status_code == 409
    assert sid in queue.awaiting_bac
    queue.awaiting_bac.pop(sid)
    queue.pending[sid] = ({}, 0)
    assert page_status(app, token).json()['cancel_available'] is True
    assert cancel().json() == {'status': 'cancelled'}
    assert sid not in queue.pending and store.scan(sid)['status'] == 'cancelled'
    assert cancel().status_code == 409
