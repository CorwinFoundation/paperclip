import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))
from bac_gateway import COMPANY, LEAD, Gateway, canonical, create_app

CASES = '/api/companies/' + COMPANY + '/issues'
REFS = dict(request_id='stable', customer_ref='intake:stable', agent_ref='intake:stable', scope_ref='starter-100@4', code_ref='intake:stable')
PAYLOAD = dict(title='Starter request stable', description=json.dumps(REFS), status='todo', priority='medium', assigneeAgentId=LEAD)


class Upstream:
    def __init__(self):
        self.cases, self.events, self.calls = [], [], []
        self.drop = False
        self.redirect = False

    def request(self, method, url, **kw):
        assert kw['follow_redirects'] is False
        assert kw['headers'] == {'Authorization': 'Bearer upstream-secret'}
        self.calls.append((method, url))
        if self.redirect:
            return httpx.Response(302, headers={'location': 'https://foreign.invalid'})
        if method == 'POST':
            if url.endswith('/comments'):
                value = dict(kw['json'], id='comment-1', issueId='case-1', authorAgentId='gateway-agent')
                self.events.append(value)
            else:
                value = dict(kw['json'], id='case-1', companyId=COMPANY, createdByAgentId='gateway-agent')
                self.cases.append(value)
            if self.drop:
                raise TimeoutError('upstream-secret must never escape')
            return httpx.Response(201, json=value)
        return httpx.Response(200, json=self.events if url.endswith('/comments') else self.cases)


def setup(tmp_path, upstream=None):
    upstream = upstream or Upstream()
    g = Gateway(str(tmp_path / 'gateway.sqlite'), upstream='https://bac.invalid', upstream_token='upstream-secret',
                upstream_agent='gateway-agent', client_token='intake-secret', transport=upstream)
    return TestClient(create_app(g), headers={'Authorization': 'Bearer intake-secret'}), upstream


def event(**changes):
    value = dict(request_id='stable', case_id='case-1', scan_id='scan-1', type='lifecycle', revision_sha256='a'*64, reference='state:running')
    value.update(changes)
    value['event_id'] = hashlib.sha256(canonical(value).encode()).hexdigest()
    value['occurred_at'] = 1234
    return {'body': canonical(value)}


def test_create_replay_restart_and_filter(tmp_path):
    c, u = setup(tmp_path)
    assert c.post(CASES, json=PAYLOAD).status_code == 201
    u.cases.append(dict(PAYLOAD, id='foreign', companyId='foreign', private='never return'))
    c, _ = setup(tmp_path, u)
    r = c.get(CASES, params={'q': 'stable'})
    assert r.status_code == 200 and len(r.json()) == 1
    assert 'private' not in r.text and 'createdByAgentId' not in r.text
    assert c.post(CASES, json=PAYLOAD).status_code == 201
    assert sum(m == 'POST' for m, _ in u.calls) == 1


def test_lost_acceptance_restart_empty_then_recover(tmp_path):
    c, u = setup(tmp_path)
    u.drop = True
    r = c.post(CASES, json=PAYLOAD)
    assert r.status_code == 403 and 'secret' not in r.text
    saved = u.cases[:]
    u.cases.clear()
    c, _ = setup(tmp_path, u)
    assert c.post(CASES, json=PAYLOAD).status_code == 403
    u.cases = saved
    assert c.get(CASES, params={'q': 'stable'}).json()[0]['id'] == 'case-1'
    assert sum(m == 'POST' for m, _ in u.calls) == 1


def test_concurrent_process_connections_claim_once(tmp_path):
    c, u = setup(tmp_path)
    clients = [setup(tmp_path, u)[0] for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda client: client.post(CASES, json=PAYLOAD).status_code, clients))
    assert 201 in results
    assert sum(m == 'POST' for m, _ in u.calls) == 1


@pytest.mark.parametrize('method,path', [
    ('get', '/api/companies/other/issues?q=stable'), ('get', '/api/issues/case-1'),
    ('patch', '/api/issues/case-1'), ('delete', '/api/issues/case-1'),
    ('post', '/api/companies/'+COMPANY+'/agents'), ('post', '/api/agents/lead/keys'),
    ('post', '/api/approvals/approval/approve'), ('post', '/api/issues/case-1/checkout'),
    ('get', CASES), ('get', CASES+'?q=unknown'), ('get', CASES+'?q=stable&q=other'),
    ('get', '/api/issues/foreign/comments'), ('post', CASES+'/'),
    ('post', CASES+'?extra=1'), ('post', CASES.replace('/issues', '/%69ssues'))])
def test_denied_routes_make_no_upstream_call(tmp_path, method, path):
    c, u = setup(tmp_path)
    assert c.request(method.upper(), path).status_code == 403
    assert u.calls == []


@pytest.mark.parametrize('patch', [dict(assigneeAgentId='other'), dict(priority='high'), dict(status='done'),
    dict(extra='secret'), dict(description=json.dumps(dict(REFS, endpoint='https://secret.invalid'))),
    dict(description=json.dumps(dict(REFS, customer_ref='customer@example.com'))),
    dict(description=json.dumps(dict(REFS, code_ref='https://secret.invalid')))])
def test_reject_body_before_network(tmp_path, patch):
    c, u = setup(tmp_path)
    assert c.post(CASES, json=dict(PAYLOAD, **patch)).status_code == 403
    assert not u.calls


def test_auth_redirect_identity_conflict_and_spoofed_owner(tmp_path):
    c, u = setup(tmp_path)
    assert c.post(CASES, json=PAYLOAD, headers={'Authorization':'Bearer wrong'}).status_code == 401
    assert not u.calls
    u.redirect = True
    assert c.post(CASES, json=PAYLOAD).status_code == 403
    u.redirect = False
    u.cases = [dict(PAYLOAD, id='case-1', companyId=COMPANY, createdByAgentId='other')]
    assert c.get(CASES+'?q=stable').status_code == 403
    assert c.post(CASES, json=dict(PAYLOAD, description=json.dumps(dict(REFS, scope_ref='different')))).status_code == 403
    assert sum(m == 'POST' for m, _ in u.calls) == 1


def test_comments_ownership_lost_acceptance_and_authorship(tmp_path):
    c, u = setup(tmp_path)
    assert c.post(CASES, json=PAYLOAD).status_code == 201
    u.drop = True
    route = '/api/issues/case-1/comments'
    assert c.post(route, json=event()).status_code == 403
    c, _ = setup(tmp_path, u)
    assert c.post(route, json=event()).status_code == 201
    assert sum(m == 'POST' and url.endswith('/comments') for m, url in u.calls) == 1
    # An event body claiming approval grants no authority; foreign authors are omitted.
    u.events.append(dict(u.events[0], id='forged', authorAgentId=LEAD))
    u.events.append(dict(u.events[0], id='human-forged', authorUserId='local-board'))
    u.events.append(dict(id='private', issueId='case-1', body='secret', authorAgentId='other'))
    r = c.get(route)
    assert len(r.json()) == 1 and 'secret' not in r.text
    bad = event(type='approval')
    assert c.post(route, json=bad).status_code == 403
    u.cases[0]['assigneeAgentId'] = 'other'
    assert c.get(route).status_code == 403


def test_duplicate_recovery_and_wrong_event_binding_stay_closed(tmp_path):
    c, u = setup(tmp_path)
    assert c.post(CASES, json=PAYLOAD).status_code == 201
    u.cases.append(dict(u.cases[0], id='case-2'))
    assert c.get(CASES+'?q=stable').status_code == 403
    u.cases.pop()
    route = '/api/issues/case-1/comments'
    assert c.post(route, json=event(request_id='other')).status_code == 403
    assert c.post(route, json=event(reference='https://secret.invalid')).status_code == 403
    assert c.post(route, json=event()).status_code == 201
    u.events.append(dict(u.events[0], id='duplicate'))
    assert c.post(route, json=event()).status_code == 403


def test_duplicate_json_keys_and_get_body_rejected(tmp_path):
    c, u = setup(tmp_path)
    assert c.post(CASES, content='{"title":"one","title":"two"}').status_code == 403
    assert c.request('GET', CASES+'?q=stable', json={}).status_code == 403
    assert not u.calls


def test_evidence_actor_case_revision_and_projection(tmp_path):
    from bac_gateway import EvidenceTool
    c, upstream = setup(tmp_path)
    gateway = Gateway(str(tmp_path / 'tool.sqlite'), upstream='https://bac.invalid',
                      upstream_token='upstream-secret', upstream_agent='gateway-agent',
                      client_token='intake-secret', transport=upstream)
    binding = dict(request_id='stable', case_id='case-1', scan_id='scan-1', revision_sha256='a'*64)

    class Resolver:
        calls = 0
        def resolve_evidence(self, store, **kw):
            self.calls += 1
            assert store is sentinel
            if kw != binding:
                raise ValueError('private resolver detail')
            return dict(kw, outcome='complete', results=[dict(ordinal=1, verdict='pass', prompt='private')], secret='private')

    sentinel, resolver = object(), Resolver()
    tool = EvidenceTool(intake=resolver, store=sentinel,
                        actors={'analyst-secret': ('analyst-1', 'Analyst'), 'lead-secret': (LEAD, 'Lead')},
                        grants={'analyst-1': {'case-1'}, LEAD: {'case-1'}})
    client = TestClient(create_app(gateway, tool))
    route = '/tools/starter/evidence'
    for token in ('analyst-secret', 'lead-secret'):
        response = client.post(route, json=binding, headers={'Authorization':'Bearer '+token})
        assert response.status_code == 200
        assert response.json() == dict(binding, outcome='complete', results=[dict(ordinal=1, verdict='pass')])
        assert 'private' not in response.text
    before = resolver.calls
    for token, body in [('intake-secret', binding), ('unknown', binding),
                        ('analyst-secret', dict(binding, case_id='foreign')),
                        ('analyst-secret', dict(binding, actor_id=LEAD))]:
        assert client.post(route, json=body, headers={'Authorization':'Bearer '+token}).status_code == 403
    assert resolver.calls == before
    for patch in [dict(request_id='foreign'), dict(scan_id='foreign'), dict(revision_sha256='b'*64)]:
        response = client.post(route, json=dict(binding, **patch), headers={'Authorization':'Bearer analyst-secret'})
        assert response.status_code == 403 and 'private' not in response.text
    for method, path in [('GET', route), ('POST', route+'?case_id=case-1'), ('POST', '/tools/starter/%65vidence')]:
        assert client.request(method, path, json=binding, headers={'Authorization':'Bearer analyst-secret'}).status_code in (401, 403)
    assert not upstream.calls
    # Default deployment factory cannot accidentally activate an unconfigured tool.
    assert c.post(route, json=binding).status_code == 403


@pytest.mark.parametrize('origin', ['http://127.0.0.1:3100', 'http://[::1]:3100/'])
def test_literal_local_upstream_maps_native_routes(tmp_path, origin):
    u = Upstream()
    g = Gateway(str(tmp_path / 'local.sqlite'), upstream=origin, upstream_token='upstream-secret',
                upstream_agent='gateway-agent', client_token='intake-secret', transport=u)
    c = TestClient(create_app(g), headers={'Authorization': 'Bearer intake-secret'})
    assert c.post(CASES, json=PAYLOAD).status_code == 201
    assert c.get(CASES, params={'q': 'stable'}).status_code == 200
    assert c.post('/api/issues/case-1/comments', json=event()).status_code == 201
    assert c.get('/api/issues/case-1/comments').status_code == 200
    assert all(url.startswith(origin.rstrip('/') + '/api/') for _, url in u.calls)


@pytest.mark.parametrize('origin', [
    'http://localhost:3100', 'http://127.1:3100', 'http://2130706433:3100',
    'http://127.0.0.2:3100', 'http://127.0.0.1', 'http://127.0.0.1:8794',
    'http://[::ffff:127.0.0.1]:3100', 'http://remote.invalid:3100',
    'http://127.0.0.1:3100@remote.invalid', 'http://user@127.0.0.1:3100',
    'http://127.0.0.1:3100/api', 'http://127.0.0.1:3100?x=1',
    'http://127.0.0.1:3100#x', 'http://127.0.0.1:03100',
])
def test_plaintext_upstream_exception_is_exact(tmp_path, origin):
    with pytest.raises(ValueError):
        Gateway(str(tmp_path / 'denied.sqlite'), upstream=origin, upstream_token='upstream-secret',
                upstream_agent='gateway-agent', client_token='intake-secret', transport=Upstream())
    assert not (tmp_path / 'denied.sqlite').exists()


def approval_record(**changes):
    value = dict(type='report_approval', request_id='stable', case_id='case-1',
                 scan_id='scan-1', revision_sha256='a'*64, decision='approve')
    value.update(changes)
    return dict(id='lead-decision', companyId=COMPANY, issueId='case-1',
                authorAgentId=LEAD, authorUserId=None, body=canonical(value))


APPROVAL_ROUTE = '/api/issues/case-1/starter-report-approval?scan_id=scan-1&revision_sha256=' + 'a'*64


def test_native_approval_requires_recorded_revision_and_survives_restart(tmp_path):
    c, u = setup(tmp_path)
    assert c.post(CASES, json=PAYLOAD).status_code == 201
    u.events.append(approval_record())
    assert c.get(APPROVAL_ROUTE).status_code == 403
    assert c.post('/api/issues/case-1/comments', json=event(type='report_revision')).status_code == 201
    c, _ = setup(tmp_path, u)
    result = c.get(APPROVAL_ROUTE)
    assert result.status_code == 200
    assert result.json() == [dict(json.loads(approval_record()['body']), id='lead-decision', authorAgentId=LEAD)]
    u.events.append(approval_record(decision='reject'))
    assert c.get(APPROVAL_ROUTE).status_code == 403


@pytest.mark.parametrize('patch', [dict(authorAgentId='gateway-agent'), dict(authorAgentId='other'),
    dict(authorUserId='local-board'), dict(companyId='foreign'), dict(issueId='foreign'),
    dict(deletedAt='2026-10-19'), dict(body='private text'), dict(id=None)])
def test_native_approval_rejects_wrong_native_identity_and_projects(tmp_path, patch):
    c, u = setup(tmp_path)
    assert c.post(CASES, json=PAYLOAD).status_code == 201
    assert c.post('/api/issues/case-1/comments', json=event(type='report_revision')).status_code == 201
    u.events.append(dict(approval_record(), **patch, private='secret'))
    result = c.get(APPROVAL_ROUTE)
    assert result.status_code == 200 and result.json() == [] and 'secret' not in result.text


@pytest.mark.parametrize('changes', [dict(request_id='other'), dict(case_id='other'),
    dict(scan_id='other'), dict(revision_sha256='b'*64), dict(decision='maybe'),
    dict(type='lifecycle'), dict(authorAgentId=LEAD)])
def test_native_approval_body_binding(tmp_path, changes):
    c, u = setup(tmp_path)
    assert c.post(CASES, json=PAYLOAD).status_code == 201
    assert c.post('/api/issues/case-1/comments', json=event(type='report_revision')).status_code == 201
    u.events.append(approval_record(**changes))
    assert c.get(APPROVAL_ROUTE).json() == []


def test_native_approval_route_acl(tmp_path):
    c, u = setup(tmp_path)
    assert c.get(APPROVAL_ROUTE).status_code == 403
    assert not u.calls
    assert c.post(CASES, json=PAYLOAD).status_code == 201
    for route in (APPROVAL_ROUTE + '&scan_id=scan-1', APPROVAL_ROUTE + '&extra=1',
                  APPROVAL_ROUTE.replace('scan_id=scan-1&', ''), APPROVAL_ROUTE.replace('case-1', 'foreign')):
        assert c.get(route).status_code == 403
    assert c.post(APPROVAL_ROUTE, json=approval_record()).status_code == 403
    assert c.post('/api/issues/case-1/comments', json={'body': approval_record()['body']}).status_code == 403


def test_native_approval_needs_upstream_report_acknowledgment(tmp_path):
    c, u = setup(tmp_path)
    assert c.post(CASES, json=PAYLOAD).status_code == 201
    u.drop = True
    assert c.post('/api/issues/case-1/comments', json=event(type='report_revision')).status_code == 403
    report = u.events.pop()
    u.events.append(approval_record())
    c, _ = setup(tmp_path, u)
    assert c.get(APPROVAL_ROUTE).status_code == 403
    u.events.append(report)
    assert c.get(APPROVAL_ROUTE).status_code == 200
    assert sum(m == 'POST' and url.endswith('/comments') for m, url in u.calls) == 1

def attempt(tmp_path):
    import sqlite3
    with sqlite3.connect(tmp_path / 'gateway.sqlite') as db:
        db.row_factory = sqlite3.Row
        return dict(db.execute('SELECT * FROM event_attempts').fetchone())


@pytest.mark.parametrize('status,outcome', [(403, 'refused'), (422, 'refused'),
    (302, 'uncertain'), (500, 'uncertain'), (409, 'uncertain'), (429, 'uncertain')])
def test_native_failure_retained_no_repost(tmp_path, status, outcome):
    class Failure(Upstream):
        def request(self, method, url, **kw):
            if method == 'POST' and url.endswith('/comments'):
                self.calls.append((method, url))
                # Include hostile content to prove only the safe projection persists.
                return httpx.Response(status, json={'id': 'response-1', 'secret': 'upstream-secret'})
            return super().request(method, url, **kw)
    c, u = setup(tmp_path, Failure())
    assert c.post(CASES, json=PAYLOAD).status_code == 201
    route = '/api/issues/case-1/comments'
    r = c.post(route, json=event())
    assert r.status_code == 403 and r.json()['outcome'] == outcome
    row = attempt(tmp_path)
    assert row['native_status'] == status and row['response_id'] == 'response-1'
    assert row['attempt_id'] and row['started_at'] and row['outcome'] == outcome
    assert 'secret' not in str(row) and 'secret' not in r.text
    c, _ = setup(tmp_path, u)
    assert c.post(route, json=event()).json()['outcome'] == outcome
    assert sum(m == 'POST' and url.endswith('/comments') for m, url in u.calls) == 1


def test_timeout_records_attempt_then_exact_authenticated_reconciliation(tmp_path):
    c, u = setup(tmp_path)
    assert c.post(CASES, json=PAYLOAD).status_code == 201
    u.drop = True
    route = '/api/issues/case-1/comments'
    assert c.post(route, json=event()).json()['outcome'] == 'uncertain'
    row = attempt(tmp_path)
    assert row['native_status'] is None and row['response_id'] is None
    c, _ = setup(tmp_path, u)
    assert c.post(route, json=event()).status_code == 201
    assert attempt(tmp_path)['confirmed_id'] == 'comment-1'
    assert attempt(tmp_path)['attempt_id'] == row['attempt_id']
    assert attempt(tmp_path)['outcome'] == 'confirmed'


def test_legacy_unknown_requires_exact_author_and_event_without_post(tmp_path):
    import sqlite3
    c, u = setup(tmp_path)
    assert c.post(CASES, json=PAYLOAD).status_code == 201
    payload = event()
    key = json.loads(payload['body'])['event_id']
    # Existing pre-migration operation: no retained native response or attempt.
    with sqlite3.connect(tmp_path / 'gateway.sqlite') as db:
        db.execute('INSERT INTO operations VALUES (?,?,?,?)', ('event', key, canonical(payload), 'case-1'))
    route = '/api/issues/case-1/comments'
    record = dict(payload, id='candidate', issueId='case-1', authorAgentId='gateway-agent')
    u.events = [dict(record, authorAgentId=None), dict(record, id='board', authorUserId='local-board'),
                dict(record, id='lead', authorAgentId=LEAD), dict(record, id='wrong-case', issueId='other'),
                dict(record, id='wrong-event', **event(scan_id='other'))]
    c, _ = setup(tmp_path, u)
    assert c.post(route, json=payload).json()['outcome'] == 'legacy_unknown'
    assert c.get(route).json() == []
    u.events.append(record)
    assert c.post(route, json=payload).status_code == 201
    assert attempt(tmp_path)['attempt_id'] is None
    assert attempt(tmp_path)['confirmed_id'] == 'candidate'
    assert not any(m == 'POST' and url.endswith('/comments') for m, url in u.calls)


@pytest.mark.parametrize('body', [None, {'id': 'authorless', 'issueId': 'case-1', 'authorAgentId': None}])
def test_201_without_valid_authenticated_event_remains_uncertain(tmp_path, body):
    class Invalid(Upstream):
        def request(self, method, url, **kw):
            if method == 'POST' and url.endswith('/comments'):
                self.calls.append((method, url))
                return httpx.Response(201, content=b'not-json') if body is None else httpx.Response(201, json=dict(body, **kw['json']))
            return super().request(method, url, **kw)
    c, u = setup(tmp_path, Invalid())
    assert c.post(CASES, json=PAYLOAD).status_code == 201
    route = '/api/issues/case-1/comments'
    assert c.post(route, json=event()).json()['outcome'] == 'uncertain'
    assert attempt(tmp_path)['native_status'] == 201
    c, _ = setup(tmp_path, u)
    assert c.post(route, json=event()).status_code == 403
    assert sum(m == 'POST' and url.endswith('/comments') for m, url in u.calls) == 1


def test_native_approval_after_uncertain_report_reconcile_never_reposts(tmp_path):
    c, u = setup(tmp_path)
    assert c.post(CASES, json=PAYLOAD).status_code == 201
    payload = event(type='report_revision')
    route = '/api/issues/case-1/comments'
    u.drop = True
    assert c.post(route, json=payload).json()['outcome'] == 'uncertain'
    u.drop = False
    u.events.append(approval_record())
    c, _ = setup(tmp_path, u)
    assert c.get(APPROVAL_ROUTE).status_code == 200
    assert c.post(route, json=payload).status_code == 201
    assert attempt(tmp_path)['outcome'] == 'confirmed'
    assert sum(m == 'POST' and url.endswith('/comments') for m, url in u.calls) == 1
    c, _ = setup(tmp_path, u)
    assert c.get(APPROVAL_ROUTE).status_code == 200


@pytest.mark.parametrize('match', ['missing', 'unique', 'duplicate', 'system', 'board', 'lead'])
def test_recovery_assignment_return_never_reposts_historical_intent(tmp_path, match):
    import sqlite3
    c, u = setup(tmp_path)
    assert c.post(CASES, json=PAYLOAD).status_code == 201
    payload = event()
    key = json.loads(payload['body'])['event_id']
    original = ('event', key, canonical(payload), 'case-1')
    with sqlite3.connect(tmp_path / 'gateway.sqlite') as db:
        db.execute('INSERT INTO operations VALUES (?,?,?,?)', original)
    record = dict(payload, id='historical', issueId='case-1', authorAgentId='gateway-agent', authorUserId=None)
    u.events = {
        'missing': [], 'unique': [record], 'duplicate': [record, dict(record, id='second')],
        'system': [dict(record, authorAgentId=None)],
        'board': [dict(record, authorAgentId=None, authorUserId='local-board')],
        'lead': [dict(record, authorAgentId=LEAD)],
    }[match]
    route = '/api/issues/case-1/comments'
    u.cases[0]['assigneeAgentId'] = 'gateway-agent'
    assert c.get(route).status_code == 403
    assert c.post(route, json=payload).status_code == 403
    # Simulate the native operator restoring the audited return owner, then restart.
    u.cases[0]['assigneeAgentId'] = LEAD
    c, _ = setup(tmp_path, u)
    response = c.post(route, json=payload)
    assert response.status_code == (201 if match == 'unique' else 403)
    with sqlite3.connect(tmp_path / 'gateway.sqlite') as db:
        assert db.execute("SELECT * FROM operations WHERE kind='event'").fetchone() == original
    assert sum(method == 'POST' for method, url in u.calls) == 1  # initial case only
