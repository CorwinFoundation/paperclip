"""Restricted Starter BAC boundary; upstream credentials never reach intake."""
import hashlib
import hmac
import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager, asynccontextmanager
import threading
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

COMPANY = '60bcddf3-87be-4197-8f1c-846bd967db11'
LEAD = '5da6979d-4906-49d7-a87b-4302d2b7dce2'
OPAQUE = re.compile(r'[A-Za-z0-9_:@.-]{1,200}\Z')
DIGEST = re.compile(r'[a-f0-9]{64}\Z')


class Denied(Exception):
    pass


class EventUnconfirmed(Denied):
    def __init__(self, outcome):
        self.outcome = outcome


def canonical(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def opaque(value):
    return isinstance(value, str) and bool(OPAQUE.fullmatch(value))


def strict_json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise Denied()
            result[key] = item
        return result
    return json.loads(value, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(Denied()))


class Gateway:
    def __init__(self, path, *, upstream, upstream_token, upstream_agent, client_token, transport):
        url = urlsplit(upstream)
        local_http = upstream in {'http://127.0.0.1:3100', 'http://127.0.0.1:3100/',
                                  'http://[::1]:3100', 'http://[::1]:3100/'}
        if ((url.scheme != 'https' and not local_http) or not url.hostname or url.username or url.password
                or url.path not in ('', '/') or url.query or url.fragment
                or not all((upstream_token, upstream_agent, client_token))
                or upstream_token == client_token):
            raise ValueError('invalid gateway configuration')
        self.upstream = upstream.rstrip('/')
        self.upstream_token, self.agent, self.client_token = upstream_token, upstream_agent, client_token
        self.transport = transport
        self.path = path
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS operations (kind TEXT, key TEXT, payload TEXT NOT NULL, '
                       'issue_id TEXT, PRIMARY KEY(kind,key))')

            db.execute('CREATE TABLE IF NOT EXISTS event_attempts ('
                       'event_id TEXT PRIMARY KEY, attempt_id TEXT, started_at REAL, '
                       'outcome TEXT NOT NULL, native_status INTEGER, response_id TEXT, '
                       'confirmed_id TEXT)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            with db:
                yield db
        finally:
            db.close()

    def claim(self, kind, key, payload, issue_id=None):
        encoded = canonical(payload)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload,issue_id FROM operations WHERE kind=? AND key=?', (kind, key)).fetchone()
            if row:
                if row[0] != encoded or (kind == 'event' and row[1] != issue_id):
                    raise Denied()
                return False
            db.execute('INSERT INTO operations VALUES (?,?,?,?)', (kind, key, encoded, issue_id))
            return True

    def upstream_call(self, method, route, payload=None):
        response = self.transport.request(method, self.upstream + route,
            headers={'Authorization': 'Bearer ' + self.upstream_token},
            **({'json': payload} if payload is not None else {}), timeout=20, follow_redirects=False)
        if response.status_code != (201 if method == 'POST' else 200):
            raise Denied()
        return response.json()

    def event_post(self, key, route, payload):
        # Persist before I/O. A crash at any point leaves an uncertain attempt;
        # the durable claim prevents every subsequent caller from posting again.
        with self.db() as db:
            db.execute('INSERT INTO event_attempts '
                       '(event_id,attempt_id,started_at,outcome) VALUES (?,?,?,?)',
                       (key, str(uuid.uuid4()), time.time(), 'uncertain'))
        try:
            response = self.transport.request('POST', self.upstream + route,
                headers={'Authorization': 'Bearer ' + self.upstream_token},
                json=payload, timeout=20, follow_redirects=False)
        except Exception:
            raise EventUnconfirmed('uncertain') from None
        status = response.status_code
        # These explicit validation/authentication refusals are terminal for this
        # intent. Other statuses (including redirects and 5xx) prove no absence.
        outcome = 'refused' if status in {400, 401, 403, 404, 405, 413, 415, 422} else 'uncertain'
        value = None
        try:
            value = response.json()
        except (ValueError, TypeError):
            pass
        response_id = value.get('id') if isinstance(value, dict) else None
        with self.db() as db:
            db.execute('UPDATE event_attempts SET native_status=?,response_id=?,outcome=? WHERE event_id=?',
                       (status, response_id if opaque(response_id) else None, outcome, key))
        # Never persist raw response bodies, headers, exception text or secrets.
        if status != 201 or not isinstance(value, dict):
            raise EventUnconfirmed(outcome)
        return value

    def event_outcome(self, key):
        with self.db() as db:
            row = db.execute('SELECT outcome FROM event_attempts WHERE event_id=?', (key,)).fetchone()
        return row[0] if row else 'legacy_unknown'

    def confirm_event(self, key, comment_id):
        with self.db() as db:
            db.execute("INSERT INTO event_attempts (event_id,outcome,confirmed_id) VALUES (?,'confirmed',?) "
                       "ON CONFLICT(event_id) DO UPDATE SET outcome='confirmed',confirmed_id=excluded.confirmed_id",
                       (key, comment_id))

    def case_payload(self, value):
        if (not isinstance(value, dict) or set(value) != {'title', 'description', 'status', 'priority', 'assigneeAgentId'}
                or value['status'] != 'todo' or value['priority'] != 'medium' or value['assigneeAgentId'] != LEAD):
            raise Denied()
        refs = strict_json(value['description'])
        if (not isinstance(refs, dict) or set(refs) != {'request_id', 'customer_ref', 'agent_ref', 'scope_ref', 'code_ref'}
                or not all(opaque(v) for v in refs.values()) or value['title'] != 'Starter request ' + refs['request_id']):
            raise Denied()
        if (any(refs[k] != 'intake:' + refs['request_id'] for k in ('customer_ref', 'agent_ref', 'code_ref'))
                or refs['scope_ref'] not in {'starter-100@1', 'starter-100@2', 'starter-100@3', 'starter-100@4', 'starter-100@5', 'starter-voice-50@1'}):
            raise Denied()
        return refs['request_id']

    def matching_case(self, record, payload):
        try:
            return (isinstance(record, dict) and opaque(record.get('id'))
                and record.get('companyId') == COMPANY and record.get('createdByAgentId') == self.agent
                and record.get('assigneeAgentId') == LEAD and record.get('title') == payload['title']
                and strict_json(record.get('description', '')) == strict_json(payload['description']))
        except (ValueError, TypeError, Denied):
            return False

    def confirm(self, key, records):
        with self.db() as db:
            row = db.execute("SELECT payload,issue_id FROM operations WHERE kind='case' AND key=?", (key,)).fetchone()
            if not row or not isinstance(records, list):
                raise Denied()
            payload = json.loads(row[0])
            matches = [r for r in records if self.matching_case(r, payload)]
            if len(matches) != 1 or (row[1] and row[1] != matches[0]['id']):
                raise Denied()
            record = matches[0]
            db.execute("UPDATE operations SET issue_id=? WHERE kind='case' AND key=?", (record['id'], key))
        return dict(payload, id=record['id'], companyId=COMPANY)

    def owned(self, issue_id):
        with self.db() as db:
            rows = db.execute("SELECT key FROM operations WHERE kind='case' AND issue_id=?", (issue_id,)).fetchall()
        if len(rows) != 1:
            raise Denied()
        key = rows[0][0]
        self.confirm(key, self.upstream_call('GET', '/api/companies/' + COMPANY + '/issues?q=' + quote(key, safe='')))
        return key

    def event(self, payload, issue_id, request_id):
        if not isinstance(payload, dict) or set(payload) != {'body'}:
            raise Denied()
        event = strict_json(payload['body'])
        if (not isinstance(event, dict) or set(event) != {'event_id', 'request_id', 'case_id', 'scan_id', 'type', 'revision_sha256', 'reference', 'occurred_at'}
                or event['request_id'] != request_id or event['case_id'] != issue_id
                or event['type'] not in {'lifecycle', 'evidence', 'report_revision', 'send_receipt'}
                or not all(opaque(event[k]) for k in ('request_id', 'case_id', 'scan_id', 'reference'))
                or not isinstance(event['revision_sha256'], str) or not DIGEST.fullmatch(event['revision_sha256'])
                or type(event['occurred_at']) not in (int, float) or not math.isfinite(event['occurred_at']) or event['occurred_at'] < 0):
            raise Denied()
        binding = {k: v for k, v in event.items() if k not in ('event_id', 'occurred_at')}
        if event['event_id'] != hashlib.sha256(canonical(binding).encode()).hexdigest():
            raise Denied()
        return event

    def comments(self, issue_id, records):
        if not isinstance(records, list):
            raise Denied()
        with self.db() as db:
            intents = [json.loads(r[0]) for r in db.execute("SELECT payload FROM operations WHERE kind='event' AND issue_id=?", (issue_id,))]
        safe = []
        for r in records:
            try:
                if (r.get('issueId') == issue_id and opaque(r.get('id')) and r.get('authorAgentId') == self.agent
                        and r.get('authorUserId') is None and any(strict_json(r.get('body', '')) == strict_json(p['body']) for p in intents)):
                    safe.append({'id': r['id'], 'issueId': issue_id, 'body': r['body']})
            except (ValueError, TypeError, AttributeError, Denied):
                continue
        return safe

    def approvals(self, issue_id, request_id, scan_id, revision, records):
        if not isinstance(records, list):
            raise Denied()
        # Require a durable local intent AND a native acknowledgment of that report.
        with self.db() as db:
            reports = [strict_json(json.loads(r[0])['body']) for r in db.execute(
                "SELECT payload FROM operations WHERE kind='event' AND issue_id=?", (issue_id,))]
        confirmed = self.comments(issue_id, records)
        if not any(e['type'] == 'report_revision' and e['scan_id'] == scan_id
                   and e['revision_sha256'] == revision
                   and any(strict_json(r['body']) == e for r in confirmed) for e in reports):
            raise Denied()
        safe = []
        fields = {'type', 'request_id', 'case_id', 'scan_id', 'revision_sha256', 'decision'}
        for record in records:
            try:
                if (record.get('issueId') != issue_id or record.get('companyId') != COMPANY
                        or record.get('authorAgentId') != LEAD or record.get('authorUserId') is not None
                        or record.get('deletedAt') is not None or not opaque(record.get('id'))):
                    continue
                value = strict_json(record.get('body', ''))
                if (not isinstance(value, dict) or set(value) != fields
                        or value['type'] != 'report_approval' or value['request_id'] != request_id
                        or value['case_id'] != issue_id or value['scan_id'] != scan_id
                        or value['revision_sha256'] != revision or value['decision'] not in {'approve', 'reject'}):
                    continue
                safe.append(dict(value, id=record['id'], authorAgentId=LEAD))
            except (ValueError, TypeError, AttributeError, Denied):
                continue
        # Duplicate/conflicting decisions require human resolution; do not select one.
        if len(safe) > 1:
            raise Denied()
        return safe

    def handle(self, method, path, query, payload):
        cases = '/api/companies/' + COMPANY + '/issues'
        if path == cases:
            if method == 'POST' and not query:
                key = self.case_payload(payload)
                if self.claim('case', key, payload):
                    return 201, self.confirm(key, [self.upstream_call('POST', cases, payload)])
                return 201, self.confirm(key, self.upstream_call('GET', cases + '?q=' + quote(key, safe='')))
            if method == 'GET' and len(query) == 1 and query[0][0] == 'q' and opaque(query[0][1]) and payload is None:
                key = query[0][1]
                with self.db() as db:
                    if not db.execute("SELECT 1 FROM operations WHERE kind='case' AND key=?", (key,)).fetchone():
                        raise Denied()
                return 200, [self.confirm(key, self.upstream_call('GET', cases + '?q=' + quote(key, safe='')))]
        approval_match = re.fullmatch(r'/api/issues/([A-Za-z0-9_-]+)/starter-report-approval', path)
        if approval_match and method == 'GET' and payload is None:
            if (len(query) != 2 or {k for k, _ in query} != {'scan_id', 'revision_sha256'}):
                raise Denied()
            params = dict(query)
            if not opaque(params['scan_id']) or not DIGEST.fullmatch(params['revision_sha256']):
                raise Denied()
            issue_id = approval_match[1]
            key = self.owned(issue_id)
            return 200, self.approvals(issue_id, key, params['scan_id'], params['revision_sha256'],
                self.upstream_call('GET', '/api/issues/' + issue_id + '/comments'))
        match = re.fullmatch(r'/api/issues/([A-Za-z0-9_-]+)/comments', path)
        if match and not query and method in ('GET', 'POST'):
            issue_id = match[1]
            key = self.owned(issue_id)
            route = '/api/issues/' + issue_id + '/comments'
            if method == 'GET' and payload is None:
                return 200, self.comments(issue_id, self.upstream_call('GET', route))
            if method == 'POST':
                event = self.event(payload, issue_id, key)
                fresh = self.claim('event', event['event_id'], payload, issue_id)
                records = ([self.event_post(event['event_id'], route, payload)] if fresh else self.upstream_call('GET', route))
                matches = [r for r in self.comments(issue_id, records) if strict_json(r['body']) == event]
                if len(matches) == 1:
                    self.confirm_event(event['event_id'], matches[0]['id'])
                    return 201, matches[0]
                outcome = self.event_outcome(event['event_id'])
                raise EventUnconfirmed(outcome if outcome != 'confirmed' else 'uncertain')
        raise Denied()


class EvidenceTool:
    """Protected composition: credentials/grants come from the operator, never a request."""
    def __init__(self, *, intake, store, actors, grants):
        # actors: token -> (agent_id, role); grants: agent_id -> case IDs.
        if (not actors or any(not token or role not in {'Analyst', 'Lead'} or not opaque(actor)
                              for token, (actor, role) in actors.items())):
            raise ValueError('invalid evidence actors')
        self.intake, self.store = intake, store
        self.actors = dict(actors)
        self.grants = {actor: frozenset(cases) for actor, cases in grants.items()}

    def resolve(self, authorization, payload):
        matches = [identity for token, identity in self.actors.items()
                   if hmac.compare_digest(authorization, 'Bearer ' + token)]
        if len(matches) != 1:
            raise Denied()
        actor, role = matches[0]
        fields = {'request_id', 'case_id', 'scan_id', 'revision_sha256'}
        if (not isinstance(payload, dict) or set(payload) != fields
                or not all(opaque(payload[k]) for k in fields - {'revision_sha256'})
                or not isinstance(payload['revision_sha256'], str)
                or not DIGEST.fullmatch(payload['revision_sha256'])
                or payload['case_id'] not in self.grants.get(actor, ())):
            raise Denied()
        value = self.intake.resolve_evidence(self.store, **payload)
        # Reproject even the private primitive: no raw evidence may cross this tool.
        if any(value.get(k) != payload[k] for k in fields):
            raise Denied()
        results = value['results']
        if (value['outcome'] not in {'complete', 'failed', 'incomplete'}
                or not isinstance(results, list)
                or any(type(r.get('ordinal')) is not int or r['ordinal'] != n
                       or r.get('verdict') not in {'pass', 'fail', 'could_not_run'}
                       for n, r in enumerate(results, 1))):
            raise Denied()
        return dict(payload, outcome=value['outcome'],
                    results=[dict(ordinal=r['ordinal'], verdict=r['verdict']) for r in results])


def create_evidence_app(*, intake, store, actors, grants):
    """Private intake-host app: no upstream BAC identity or passthrough routes."""
    tool = EvidenceTool(intake=intake, store=store, actors=actors, grants=grants)
    if any(hmac.compare_digest(token, intake.token) for token in tool.actors):
        raise ValueError("evidence credentials must differ from intake credential")
    return create_app(None, tool)


def create_app(gateway, evidence_tool=None, findings_workflow=None):
    if gateway is not None and evidence_tool is not None and any(
            hmac.compare_digest(token, reserved)
            for token in evidence_tool.actors
            for reserved in (gateway.client_token, gateway.upstream_token)):
        raise ValueError('evidence credentials must be distinct')
    @asynccontextmanager
    async def lifespan(app):
        stopped = threading.Event()
        def worker():
            while not stopped.is_set():
                try:
                    findings_workflow.tick()
                except Exception:
                    pass
                stopped.wait(10)
        thread = threading.Thread(target=worker, daemon=True) if findings_workflow else None
        if thread:
            thread.start()
        try:
            yield
        finally:
            stopped.set()
            if thread:
                thread.join(timeout=1)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False, lifespan=lifespan)

    @app.api_route('/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'])
    async def dispatch(request: Request, path: str):
        try:
            auth = request.headers.getlist('authorization')
            evidence_path = request.scope['raw_path'] == b'/tools/starter/evidence'
            if len(auth) != 1:
                return JSONResponse({'error': 'unauthorized'}, status_code=401)
            if not evidence_path and (gateway is None or not hmac.compare_digest(auth[0], 'Bearer ' + gateway.client_token)):
                return JSONResponse({'error': 'unauthorized'}, status_code=401)
            raw = request.scope['raw_path'].decode('ascii')
            findings_path = raw.startswith('/findings/v1/workflows')
            if '%' in raw or '//' in raw or len(await request.body()) > (2_000_000 if findings_path else 8192):
                raise Denied()
            body = await request.body()
            payload = strict_json(body) if body else None
            if findings_path:
                from starlette.concurrency import run_in_threadpool
                if findings_workflow is None or request.url.query:
                    raise Denied()
                if raw == '/findings/v1/workflows' and request.method == 'GET':
                    return JSONResponse(await run_in_threadpool(findings_workflow.readiness))
                if raw == '/findings/v1/workflows' and request.method == 'POST':
                    return JSONResponse(await run_in_threadpool(findings_workflow.submit, payload))
                match = re.fullmatch(r'/findings/v1/workflows/([A-Za-z0-9_-]{1,128})(/delivery|/evidence)?', raw)
                if match and request.method == 'GET' and not match[2]:
                    return JSONResponse(await run_in_threadpool(findings_workflow.status, match[1]))
                if match and request.method == 'POST' and match[2]:
                    action = findings_workflow.receipt if match[2] == '/delivery' else findings_workflow.supplement
                    return JSONResponse(await run_in_threadpool(action, match[1], payload))
                raise Denied()
            if evidence_path:
                if evidence_tool is None or request.method != 'POST' or request.url.query:
                    raise Denied()
                from starlette.concurrency import run_in_threadpool
                value = await run_in_threadpool(evidence_tool.resolve, auth[0], payload)
                return JSONResponse(value)
            # Keep blocking upstream I/O off the event loop.
            from starlette.concurrency import run_in_threadpool
            status, value = await run_in_threadpool(gateway.handle, request.method, raw,
                                                    list(request.query_params.multi_items()), payload)
            return JSONResponse(value, status_code=status)
        except EventUnconfirmed as exc:
            return JSONResponse({'error': 'BAC event unconfirmed', 'outcome': exc.outcome}, status_code=403)
        except Exception:
            return JSONResponse({'error': 'BAC operation unconfirmed or denied'}, status_code=403)
    return app


def app_from_env():
    import os
    import httpx
    from pathlib import Path
    credentials = Path(os.environ['CREDENTIALS_DIRECTORY'])
    gateway = Gateway(os.environ['BAC_GATEWAY_DB'], upstream=os.environ['BAC_UPSTREAM_URL'],
        upstream_agent=os.environ['BAC_UPSTREAM_AGENT_ID'],
        upstream_token=(credentials / 'upstream-token').read_text().strip(),
        client_token=(credentials / 'client-token').read_text().strip(), transport=httpx.Client())
    from findings_workflow import FindingsWorkflow
    workflow = FindingsWorkflow(gateway.path, gateway) if os.environ.get('BAC_FINDINGS_ENABLED') == '1' else None
    return create_app(gateway, findings_workflow=workflow)
