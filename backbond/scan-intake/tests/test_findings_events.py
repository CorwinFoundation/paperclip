"""Immediate hints accelerate durable gates; they never become approvals."""
import json
import threading
from types import SimpleNamespace as NS

import pytest
from test_findings_workflow import setup, publish, MailHTTP
from findings_events import NativeEvents, IntakeEvents
from findings_workflow import COMPANY, REVIEWER, FindingsWorkflow
from findings_client import FindingsReports


def test_native_stream_filters_and_reconciles_after_reconnect():
    stopped = threading.Event(); calls = []; wakes = []
    events = iter([
        '{bad', json.dumps({'type': 'activity.logged', 'companyId': 'other', 'payload': {'entityType': 'issue'}}),
        json.dumps({'type': 'heartbeat.run.updated', 'companyId': COMPANY, 'payload': {'entityType': 'issue'}}),
        json.dumps({'type': 'activity.logged', 'companyId': COMPANY, 'payload': {'entityType': 'issue'}})])
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def recv(self, **kwargs):
            try: return next(events)
            except StopIteration: stopped.set(); raise ConnectionError()
    def connect(url, **kwargs): calls.append((url, kwargs)); return Socket()
    feed = NativeEvents(NS(upstream='http://127.0.0.1:3100', upstream_token='protected'), COMPANY, wakes.append, connect=connect)
    feed.run(stopped)
    assert wakes[0] is None and len(wakes) == 2
    assert calls[0][0] == 'ws://127.0.0.1:3100/api/companies/' + COMPANY + '/events/ws'
    assert 'protected' not in calls[0][0]
    assert calls[0][1]['additional_headers'] == {'Authorization': 'Bearer protected'}
    assert not feed.connected


def test_early_or_forged_event_cannot_release_then_closed_review_wakes_delivery(tmp_path):
    native, flow, value = setup(tmp_path)
    publish(native, flow, value, status='in_progress')
    client = FindingsReports(str(tmp_path/'intake.sqlite')); client.enqueue(value); http = MailHTTP(flow)
    kwargs = dict(base_url='https://bac.example', token='existing', http=http,
                  mail_credentials=lambda:dict(provider='resend', api_key='protected'))
    client.tick(now=100, **kwargs)
    initial = flow.wait_changes(timeout=0)['cursor']
    flow.native_changed({'verdict': 'SHIP', 'authorAgentId': REVIEWER})
    flow.tick(now=2, force=True)  # Scheduled next_at=16 has not arrived.
    client.expedite(); client.tick(now=101, **kwargs)
    assert not http.sends and flow.status('scan1')['state'] != 'approved'
    native.issues['reviewer']['status'] = 'done'
    flow.native_changed(); flow.tick(now=3, force=True)
    assert flow.wait_changes(initial, timeout=0)['cursor'] != initial
    client.expedite(); client.tick(now=102, **kwargs)
    assert len(http.sends) == 1 and flow.status('scan1')['state'] == 'approved'
    for _ in range(3):
        flow.native_changed(); flow.tick(now=4, force=True); client.expedite(); client.tick(now=103, **kwargs)
    assert len(http.sends) == 1 and flow.status('scan1')['state'] == 'sent'


def test_cursor_wait_wakes_on_committed_change_and_restart(tmp_path):
    native, flow, _ = setup(tmp_path)
    cursor = flow.wait_changes(timeout=0)['cursor']; result = []; ready = threading.Event()
    def wait():
        ready.set(); result.append(flow.wait_changes(cursor, timeout=2))
    thread = threading.Thread(target=wait); thread.start(); assert ready.wait(1)
    flow.update('scan1', error='test_visible_change'); thread.join(1)
    assert not thread.is_alive() and result[0]['cursor'] != cursor
    restarted = FindingsWorkflow(flow.path, flow.gateway)
    assert restarted.wait_changes(result[0]['cursor'], timeout=0)['cursor'] != result[0]['cursor']
    with pytest.raises(Exception): flow.wait_changes('attacker', timeout=0)


def test_expedite_does_not_break_delivery_lease(tmp_path):
    native, flow, value = setup(tmp_path); publish(native, flow, value); flow.tick(now=20)
    client = FindingsReports(str(tmp_path/'intake.sqlite')); client.enqueue(value)
    client.update('scan1', lease_until=200, next_at=200)
    http = MailHTTP(flow); client.expedite()
    client.tick(now=100, base_url='https://bac.example',token='existing',http=http,
                mail_credentials=lambda:dict(provider='resend',api_key='protected'))
    assert not http.sends


def test_fix_correction_and_second_independent_review_advance_without_due_timer(tmp_path):
    native, flow, value = setup(tmp_path)
    publish(native, flow, value, verdict='FIX')
    flow.tick(now=2, force=True)
    assert flow.status('scan1')['state'] == 'writing'
    assert flow.status('scan1')['artifacts'] is None
    publish(native, flow, value, round=2, status='in_progress')
    flow.native_changed(); flow.tick(now=3, force=True)
    assert flow.status('scan1')['state'] == 'reviewing'
    assert flow.status('scan1')['artifacts'] is None
    native.issues['reviewer']['status'] = 'done'
    flow.native_changed(); flow.tick(now=4, force=True)
    assert flow.status('scan1')['state'] == 'approved' and flow.status('scan1')['round'] == 2


def test_native_events_cannot_extend_the_two_round_limit(tmp_path):
    native, flow, value = setup(tmp_path)
    publish(native, flow, value, round=2, verdict='FIX')
    flow.tick(now=2, force=True)
    publish(native, flow, value, round=3)
    flow.native_changed(); flow.tick(now=3, force=True)
    assert flow.status('scan1')['state'] == 'needs_attention'
    assert flow.status('scan1')['artifacts'] is None


def test_changes_route_keeps_auth_and_rejects_extra_or_duplicate_parameters(tmp_path):
    from fastapi.testclient import TestClient
    from bac_gateway import create_app
    _, flow, _ = setup(tmp_path)
    client = TestClient(create_app(NS(client_token='existing',upstream_token='protected'), findings_workflow=flow))
    route='/findings/v1/workflows/changes'; headers={'Authorization':'Bearer existing'}
    assert client.get(route).status_code == 401
    assert client.get(route,headers=headers).json()['schema_version'] == 'findings_changes@1'
    for query in ['?cursor=bad','?cursor=&cursor=','?recipient=other@example.com']:
        assert client.get(route+query,headers=headers).status_code == 403
    assert client.post(route,headers=headers,json={}).status_code == 403


def test_intake_notification_is_cursor_only_and_deduplicated():
    stopped=threading.Event(); wakes=[]; requests=[]
    class HTTP:
        def get(self,url,**kwargs):
            requests.append((url,kwargs))
            if len(requests)==3: stopped.set()
            return NS(status_code=200,json=lambda:dict(schema_version='findings_changes@1',cursor='a'*32+':1'))
    IntakeEvents('https://bac.example/boundary','existing',HTTP(),lambda:wakes.append(True)).run(stopped)
    assert wakes == [True] and len(requests)==3
    assert requests[1][1]['params']=={'cursor':'a'*32+':1'}
