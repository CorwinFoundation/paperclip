"""Native attribution, immutable artifacts and crash-safe automatic delivery."""
import base64
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import zipfile

import pytest
sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))
from findings_workflow import (FindingsWorkflow, WorkflowError, canonical, digest,
    evidence_archive, CEO, COMPANY, WRITER, REVIEWER, WRITER_SCHEMA, REVIEW_SCHEMA)
from findings_client import FindingsReports


def request():
    return dict(workflow_id='scan1', scanner_id='remote1', recipient='owner@example.com',
        agent_label='Submitted agent', transport='rest', pack_version='starter-100@4',
        started_at='2026-10-23T10:00:00+00:00', finished_at='2026-10-23T10:04:00+00:00',
        accepted_total=1, provenance={}, tests=[dict(id='FAC-24', family='made_up_facts',
        prompt='A question', reply='The whole recorded reply.', verdict='fail')])


def response(value=None, status=200, content=b''):
    return NS(status_code=status, content=content, json=lambda: value)


class Native:
    def __init__(self):
        self.issues = {}; self.comments = {}; self.files = {}; self.calls = []; self.lose_create = False

    def request(self, method, url, **kwargs):
        path = url.removeprefix('http://127.0.0.1:3100')
        self.calls.append((method, path))
        if path.startswith('/api/companies/' + COMPANY + '/issues?'):
            items = list(self.issues.values())
            if '?parentId=' in path:
                items = [v for v in items if v.get('parentId') == path.split('=')[1]]
            return response(items)
        if method == 'POST' and path == '/api/companies/' + COMPANY + '/issues':
            ident = 'recovery' if kwargs['json'].get('parentId') else 'parent'
            value = dict(kwargs['json'], id=ident, companyId=COMPANY, createdByAgentId='service')
            self.issues[ident] = value
            if self.lose_create:
                self.lose_create = False
                raise TimeoutError('lost after native commit')
            return response(value, 201)
        if method == 'POST' and path.endswith('/attachments'):
            issue = path.split('/')[-2]; filename, data, media = kwargs['files']['file']
            ident = 'archive-' + issue; self.files[ident] = (issue, data)
            return response({'id': ident, 'originalFilename': filename}, 201)
        if path.endswith('/attachments'):
            issue = path.split('/')[-2]
            return response([{'id': k, 'originalFilename': 'findings-evidence-scan1.zip'} for k, (owner, _) in self.files.items() if owner == issue])
        if path.startswith('/api/attachments/'):
            return response(content=self.files[path.split('/')[3]][1])
        if path.endswith('/comments'):
            if method == 'POST':
                ident=path.split('/')[3]; item=dict(kwargs['json'],id='comment'+str(len(self.calls)),authorAgentId='service')
                self.comments.setdefault(ident,[]).append(item)
                return response(item,201)
            return response(self.comments.get(path.split('/')[3], []))
        if method == 'PATCH':
            ident = path.split('/')[-1]; self.issues[ident].update(kwargs['json'])
            return response(self.issues[ident])
        raise AssertionError((method, path))


def setup(tmp_path):
    native = Native()
    gateway = NS(transport=native, upstream='http://127.0.0.1:3100', upstream_token='native-only', agent='service')
    flow = FindingsWorkflow(str(tmp_path / 'native.sqlite'), gateway)
    value = request(); flow.submit(value); flow.tick(now=1)
    return native, flow, value


def publish(native, flow, value, *, verdict='SHIP', author=REVIEWER, status='done', round=1, changes=None):
    for ident, agent in [('writer', WRITER), ('reviewer', REVIEWER)]:
        native.issues[ident] = dict(id=ident, companyId=COMPANY, parentId='parent',
            createdByAgentId=CEO, assigneeAgentId=agent, status=status if ident == 'reviewer' else 'blocked')
    files = {'pdf': b'%PDF-1.4\n native Findings report', 'html': b'<html><title>Findings Report</title><body>Native report</body></html>'}
    artifacts = {}
    for kind, data in files.items():
        native.files[kind] = ('writer', data)
        artifacts[kind] = {'attachment_id': kind, 'sha256': digest(data)}
    handoff = dict(schema_version=WRITER_SCHEMA, workflow_id=value['workflow_id'], round=round,
        evidence_sha256=flow.status(value['workflow_id'])['evidence_sha256'], artifacts=artifacts,
        mechanical=dict(quote_failures=0, fonts_ok=True, spill=False, gaps_px=[100, 100]))
    review = dict(handoff, schema_version=REVIEW_SCHEMA, verdict=verdict, fixes=[])
    if changes: changes(review)
    native.comments['writer'] = [dict(authorAgentId=WRITER, body=canonical(handoff))]
    native.comments['reviewer'] = [dict(authorAgentId=author, body=canonical(review))]


def test_whole_evidence_archive_deterministic_and_idempotent(tmp_path):
    value = request(); data = evidence_archive(value)
    assert data == evidence_archive(value)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert len(archive.namelist()) == 7
        assert json.loads(archive.read('run.json'))['cases'][0]['exchanges'][0]['reply'] == value['tests'][0]['reply']
        for line in archive.read('SHA256SUMS').decode().splitlines():
            sha, name = line.split('  '); assert digest(archive.read(name)) == sha
    native, flow, _ = setup(tmp_path)
    flow.submit(value)
    assert sum(m == 'POST' and p.endswith('/issues') for m, p in native.calls) == 1
    value['recipient'] = 'attacker@example.com'
    with pytest.raises(WorkflowError, match='immutable_request_conflict'): flow.submit(value)


def test_unknown_parent_create_reconciles_without_second_post(tmp_path):
    native = Native(); native.lose_create = True
    gateway = NS(transport=native, upstream='http://127.0.0.1:3100', upstream_token='local', agent='service')
    path = str(tmp_path / 'native.sqlite'); flow = FindingsWorkflow(path, gateway)
    flow.submit(request()); flow.tick(now=1)
    restarted = FindingsWorkflow(path, gateway); restarted.tick(now=100)
    assert restarted.status('scan1')['state'] == 'writing'
    assert sum(m == 'POST' and p.endswith('/issues') for m, p in native.calls) == 1


@pytest.mark.parametrize('author,status,verdict', [(WRITER, 'done', 'SHIP'), (REVIEWER, 'in_progress', 'SHIP'), (REVIEWER, 'done', 'FIX')])
def test_unapproved_or_wrong_role_never_releases(tmp_path, author, status, verdict):
    native, flow, value = setup(tmp_path)
    publish(native, flow, value, author=author, status=status, verdict=verdict)
    flow.tick(now=20)
    assert flow.status('scan1')['state'] != 'approved'
    assert flow.status('scan1')['artifacts'] is None


@pytest.mark.parametrize('mutate', [lambda v:v['artifacts']['pdf'].update(sha256='0'*64),
    lambda v:v['mechanical'].update(gaps_px=[264,85]), lambda v:v.update(evidence_sha256='0'*64)])
def test_binding_and_mechanical_failures_hold(tmp_path, mutate):
    native, flow, value = setup(tmp_path); publish(native, flow, value, changes=mutate)
    flow.tick(now=20)
    assert flow.status('scan1')['state'] == 'needs_attention'
    assert flow.status('scan1')['artifacts'] is None


def test_artifact_bytes_must_match_and_stay_on_writer_issue(tmp_path):
    native, flow, value = setup(tmp_path); publish(native, flow, value)
    native.files['pdf'] = ('foreign', native.files['pdf'][1])
    flow.tick(now=20)
    assert flow.status('scan1')['error'] == 'foreign_attachment'


def test_duplicate_identical_native_verdict_is_idempotent(tmp_path):
    native,flow,value=setup(tmp_path);publish(native,flow,value)
    native.comments['reviewer'].append(deepcopy(native.comments['reviewer'][0]))
    flow.tick(now=20)
    assert flow.status('scan1')['state']=='approved'
    assert not any(method=='POST' and path.endswith('/comments') for method,path in native.calls)


def resolved_handoff_review(native):
    ship = native.comments['reviewer'][0]
    ship.update(id='ship', createdAt='2026-10-23T16:26:00Z')
    fix = deepcopy(ship)
    fix.update(id='fix', createdAt='2026-10-23T16:21:00Z')
    value = json.loads(fix['body'])
    value.update(verdict='FIX', fixes=[{'component':'Writer handoff', 'instruction':'Add the missing security family note.'}])
    fix['body'] = canonical(value)
    # Native API ordering is not chronological.
    native.comments['reviewer'] = [ship, fix]
    return ship, fix


def test_same_reviewer_resolves_unchanged_handoff_fix_and_delivers_once(tmp_path):
    native, flow, value = setup(tmp_path); publish(native, flow, value)
    resolved_handoff_review(native); flow.tick(now=20)
    assert flow.status('scan1')['state'] == 'approved'
    client=FindingsReports(str(tmp_path/'intake.sqlite'));client.enqueue(value);http=MailHTTP(flow)
    kwargs=dict(base_url='https://bac.example',token='existing',http=http,
                mail_credentials=lambda:dict(provider='resend',api_key='protected'))
    client.tick(now=100,**kwargs);client.tick(now=120,**kwargs);client.tick(now=140,**kwargs)
    assert len(http.sends)==1 and flow.status('scan1')['state']=='sent'
    token = client.native.token_for('scan1')
    assert 'https://backbond.ai/scan/r/' + token in http.sends[0][1]['text']
    assert client.native.artifact(token, 'pdf') == native.files['pdf'][1]


@pytest.mark.parametrize('invalid', ['reverse','same_time','missing_time','naive_time','escalate',
    'changed_hash','changed_measurements','later_fix','duplicate_fix_tied_with_ship',
    'other_issue','open_review','wrong_author'])
def test_fix_resolution_keeps_ambiguous_or_unapproved_delivery_closed(tmp_path, invalid):
    native,flow,value=setup(tmp_path);publish(native,flow,value)
    ship,fix=resolved_handoff_review(native)
    if invalid=='reverse':ship['createdAt']='2026-10-23T16:20:00Z'
    elif invalid=='same_time':ship['createdAt']=fix['createdAt']
    elif invalid=='missing_time':fix.pop('createdAt')
    elif invalid=='naive_time':fix['createdAt']='2026-10-23T16:21:00'
    elif invalid=='escalate':
        v=json.loads(fix['body']);v['verdict']='ESCALATE';fix['body']=canonical(v)
    elif invalid in {'changed_hash','changed_measurements'}:
        v=json.loads(fix['body'])
        if invalid=='changed_hash':v['artifacts']['pdf']['sha256']='0'*64
        else:v['mechanical']['gaps_px']=[101,100]
        fix['body']=canonical(v)
    elif invalid=='later_fix':
        again=deepcopy(fix);again.update(id='revoked',createdAt='2026-10-23T16:27:00Z')
        native.comments['reviewer'].append(again)
    elif invalid=='duplicate_fix_tied_with_ship':
        again=deepcopy(fix);again.update(id='ambiguous',createdAt=ship['createdAt'])
        native.comments['reviewer'].append(again)
    elif invalid=='other_issue':
        native.issues['other']=dict(native.issues['reviewer'],id='other')
        native.comments['other']=[native.comments['reviewer'].pop()]
    elif invalid=='open_review':native.issues['reviewer']['status']='in_progress'
    elif invalid=='wrong_author':ship['authorAgentId']=WRITER
    flow.tick(now=20)
    assert flow.status('scan1')['state']!='approved'
    assert flow.status('scan1')['artifacts'] is None


def test_conflict_recovery_rereads_closed_independent_review_without_new_work(tmp_path):
    native,flow,value=setup(tmp_path);publish(native,flow,value)
    resolved_handoff_review(native)
    flow.update('scan1',state='needs_attention',error='conflicting_reviewer_verdicts')
    calls=len(native.calls)
    result=flow.recheck_conflicted_review('scan1')
    assert result['state']=='approved'
    assert all(method=='GET' for method,_ in native.calls[calls:])
    with pytest.raises(WorkflowError,match='not_a_conflicted_review'):
        flow.recheck_conflicted_review('scan1')


def test_conflict_recovery_cannot_clear_an_unresolved_or_unrelated_hold(tmp_path):
    native,flow,value=setup(tmp_path);publish(native,flow,value)
    ship,fix=resolved_handoff_review(native);fix.pop('createdAt')
    flow.update('scan1',state='needs_attention',error='conflicting_reviewer_verdicts')
    with pytest.raises(WorkflowError,match='conflicting_reviewer_verdicts'):
        flow.recheck_conflicted_review('scan1')
    assert flow.status('scan1')['state']=='needs_attention' and flow.status('scan1')['artifacts'] is None
    flow.update('scan1',error='review_escalate')
    with pytest.raises(WorkflowError,match='not_a_conflicted_review'):
        flow.recheck_conflicted_review('scan1')


class MailHTTP:
    def __init__(self, flow): self.flow=flow; self.sends=[]; self.accepted={}; self.lose=False
    def get(self, url, **kwargs): return response(self.flow.status('scan1'))
    def post(self, url, **kwargs):
        if url.endswith('/delivery'): return response(self.flow.receipt('scan1', kwargs['json']))
        if url.endswith('/evidence'): return response(self.flow.supplement('scan1', kwargs['json']))
        if url.endswith('/findings/v1/workflows'): return response(self.flow.submit(kwargs['json']))
        assert url == 'https://api.resend.com/emails'
        key=kwargs['headers']['Idempotency-Key']; body=deepcopy(kwargs['json']); self.sends.append((key, body))
        if key in self.accepted: assert self.accepted[key] == body
        self.accepted[key]=body
        if self.lose: self.lose=False; raise TimeoutError('accepted response lost')
        return response({'id':'message1'}, 200)


def test_full_approved_flow_delivers_exact_bytes_once_across_restart(tmp_path):
    native, flow, value = setup(tmp_path); publish(native, flow, value); flow.tick(now=20)
    assert flow.status('scan1')['state'] == 'approved'
    path=str(tmp_path/'intake.sqlite'); client=FindingsReports(path); client.enqueue(value); http=MailHTTP(flow)
    kwargs=dict(base_url='https://bac.example/boundary', token='integration', http=http,
                mail_credentials=lambda:dict(provider='resend', api_key='server-only'))
    client.tick(now=100,**kwargs)
    assert len(http.sends)==1
    body=http.sends[0][1]; assert body['to']==['info@backbond.ai']
    assert body['from'] == 'BackBond <reports@backbond.ai>'
    assert body['reply_to'] == 'info@backbond.ai'
    assert 'attachments' not in body
    token = client.native.token_for('scan1')
    assert 'https://backbond.ai/scan/r/' + token in body['text']
    for kind in ['pdf','html']:
        assert client.native.artifact(token, kind) == native.files[kind][1]
    restarted=FindingsReports(path); restarted.tick(now=120,**kwargs); restarted.tick(now=140,**kwargs)
    assert len(http.sends)==1 and flow.status('scan1')['state']=='sent'
    assert restarted.lifecycle('scan1')['delivery_status']=='provider_accepted'


def test_unknown_send_reuses_identical_key_and_payload(tmp_path):
    native,flow,value=setup(tmp_path); publish(native,flow,value); flow.tick(now=20)
    path=str(tmp_path/'intake.sqlite'); client=FindingsReports(path); client.enqueue(value); http=MailHTTP(flow); http.lose=True
    kwargs=dict(base_url='https://bac.example',token='integration',http=http,mail_credentials=lambda:dict(provider='resend',api_key='local'))
    client.tick(now=100,**kwargs); assert client.lifecycle('scan1')['delivery_status']=='unknown'
    restarted=FindingsReports(path); restarted.tick(now=120,**kwargs)
    assert len(http.sends) == 1  # Durable notification retry waits sixty seconds.
    restarted.tick(now=200,**kwargs); restarted.tick(now=220,**kwargs)
    assert len(http.sends)==2 and http.sends[0]==http.sends[1] and len(http.accepted)==1
    assert flow.status('scan1')['state']=='sent'


def test_unknown_send_outside_window_is_held_not_resent(tmp_path):
    native,flow,value=setup(tmp_path); publish(native,flow,value); flow.tick(now=20)
    client=FindingsReports(str(tmp_path/'intake.sqlite'));client.enqueue(value);http=MailHTTP(flow);http.lose=True
    kwargs=dict(base_url='https://bac.example',token='integration',http=http,mail_credentials=lambda:dict(provider='resend',api_key='local'))
    client.tick(now=100,**kwargs);client.tick(now=100+24*3600,**kwargs)
    assert len(http.sends)==1 and client.lifecycle('scan1')['delivery_status']=='unknown'


@pytest.mark.parametrize('modify', [lambda v:v.update(accepted_total=True),lambda v:v['tests'][0].update(reply=''),
    lambda v:v.update(recipient='owner@example.com\r\nBcc: attacker@example.com'),lambda v:v.update(provenance={'auth':'secret'})])
def test_incomplete_or_sensitive_input_rejected(tmp_path,modify):
    native,flow,value=setup(tmp_path);value['workflow_id']='other';modify(value)
    with pytest.raises(WorkflowError):flow.submit(value)


def test_gateway_routes_require_existing_service_auth_and_have_no_decision_write(tmp_path):
    from fastapi.testclient import TestClient
    from bac_gateway import create_app
    native,flow,value=setup(tmp_path)
    gateway=NS(client_token='existing-integration',upstream_token='server-only')
    client=TestClient(create_app(gateway,findings_workflow=flow))
    route='/findings/v1/workflows/scan1'
    assert client.get(route).status_code==401
    headers={'Authorization':'Bearer existing-integration'}
    assert client.get(route,headers=headers).status_code==200
    assert client.post(route+'/approve',headers=headers,json={'verdict':'SHIP'}).status_code==403
    assert client.post(route+'/delivery',headers=headers,json={'message_id':'fake','pdf_sha256':'0'*64,'html_sha256':'0'*64}).status_code==403
    assert client.get(route+'?recipient=attacker@example.com',headers=headers).status_code==403


def test_capture_cutover_does_not_mail_historical_customers(tmp_path):
    from qa_service import IntakeStore
    path=str(tmp_path/'intake.sqlite');store=IntakeStore(path)
    store.db.execute("INSERT INTO codes VALUES ('test','p','P','standard',10,10,'2099-12-31',1)")
    reservation=store.reserve(code='test',email='owner@example.com',agent_label='Agent',endpoint_url='https://example.com',transport='rest',
        terms_version='v0.2.2',pack_version='starter-100@4',improvement_consent=False)
    ident=reservation['scan_id'];store.bind_scanner(ident,'remote1')
    store.finish(ident,request()['tests'],now='2026-10-22T10:04:00+00:00')
    reports=FindingsReports(path)
    reports.capture(store,lambda row:1,since='2026-10-23T00:00:00+00:00')
    assert reports.db.execute('SELECT count(*) FROM findings_report_outbox').fetchone()[0]==0
    reports.capture(store,lambda row:1,since='2026-10-23T00:00:00+00:00',replay=[ident])
    assert reports.db.execute('SELECT count(*) FROM findings_report_outbox').fetchone()[0]==1
    payload = reports.db.execute('SELECT payload FROM findings_report_outbox').fetchone()[0]
    assert json.loads(payload)['recipient'] == 'info@backbond.ai'
    reports.capture(store,lambda row:pytest.fail('read again'),since='2026-10-23T00:00:00+00:00',replay=[ident])


def test_completed_agui_scan_captures_and_hands_off_original_evidence_once(tmp_path):
    from qa_service import IntakeStore
    path=str(tmp_path/'intake.sqlite');store=IntakeStore(path)
    store.db.execute("INSERT INTO codes VALUES ('test','p','P','standard',10,10,'2099-12-31',1)")
    reservation=store.reserve(code='test',email='owner@example.com',agent_label='AG-UI agent',
        endpoint_url='https://agent.example/crew_chat',transport='ag_ui',terms_version='v0.2.2',
        pack_version='starter-100@4',improvement_consent=False)
    ident=reservation['scan_id'];store.bind_scanner(ident,'agui-remote')
    tests=[dict(request()['tests'][0],id='CASE-'+str(i),verdict='fail' if i<8 else 'pass') for i in range(100)]
    store.finish(ident,tests,now='2026-10-24T10:04:00+00:00')
    before=store.db.execute('SELECT tests_json FROM scans WHERE id=?',(ident,)).fetchone()[0]
    reports=FindingsReports(path)
    blocked=request();blocked['workflow_id']='previous-invalid'
    reports.enqueue(blocked)
    reports.update('previous-invalid',state='failed',error='invalid_target_response_delivery_blocked')
    reports.capture(store,lambda row:100,since='2026-10-23T00:00:00+00:00')
    captured=reports.db.execute('SELECT payload FROM findings_report_outbox WHERE scan_id=?',(ident,)).fetchone()
    assert captured is not None  # Previously failed with evidence_capture_unconfirmed / invalid_scope.
    value=json.loads(captured[0]);assert value['transport']=='ag_ui' and value['tests']==tests
    assert reports.db.execute('SELECT count(*) FROM findings_capture_errors').fetchone()[0]==0
    native=Native()
    gateway=NS(transport=native,upstream='http://127.0.0.1:3100',upstream_token='fixture',agent='service')
    flow=FindingsWorkflow(str(tmp_path/'workflow.sqlite'),gateway)
    flow.submit(value);flow.tick(now=1)
    assert flow.status(ident)['state']=='writing'
    archive=next(data for owner,data in native.files.values())
    with zipfile.ZipFile(io.BytesIO(archive)) as evidence:
        assert json.loads(evidence.read('target.json'))['transport']=='ag_ui'
        cases=json.loads(evidence.read('run.json'))['cases']
        assert len(cases)==100 and cases[0]['exchanges'][0]['reply']==tests[0]['reply']
    reports.capture(store,lambda row:pytest.fail('must not recapture'),since='2026-10-23T00:00:00+00:00')
    flow.submit(value)
    assert sum(method=='POST' and route.endswith('/issues') for method,route in native.calls)==1
    assert store.db.execute('SELECT tests_json FROM scans WHERE id=?',(ident,)).fetchone()[0]==before
    old=reports.db.execute('SELECT state,error,send_started,message_id FROM findings_report_outbox WHERE scan_id=?',('previous-invalid',)).fetchone()
    assert tuple(old)==('failed','invalid_target_response_delivery_blocked',None,None)


def selection(value):
    return dict(schema_version='scanner-selection@1', scanner_id=value['scanner_id'], tenant_id='partner-test',
        pack_version=value['pack_version'], corpus_version='0.1.0', corpus_snapshot_hash='sha256:'+'1'*64,
        seed=42, form_hash='2'*64, policy_hash='3'*64, bank_version='starter-bank@4.1',
        expected_families={'made_up_facts':1}, selected_cases=[{'id':'FAC-24','family':'made_up_facts'}],
        unavailable_case_ids=[], excluded_prior_form_ids=[], source='persisted pre-dispatch form')


def test_selection_is_bound_to_cases_pins_and_original_capture(tmp_path):
    from findings_workflow import checked_selection
    value=request(); chosen=selection(value)
    checked_selection(chosen,value)
    for key, bad in [('scanner_id','other'),('selected_cases',[{'id':'FAC-25','family':'made_up_facts'}]),
                     ('expected_families',{'made_up_facts':2}),('unavailable_case_ids',['FAC-24']),('form_hash','bad')]:
        changed=deepcopy(chosen);changed[key]=bad
        with pytest.raises(WorkflowError):checked_selection(changed,value)
    value['provenance']['seed']=43
    with pytest.raises(WorkflowError,match='pin_mismatch'):checked_selection(chosen,value)


def test_escalation_recovery_appends_evidence_and_requires_fresh_review(tmp_path):
    native,flow,value=setup(tmp_path);publish(native,flow,value,verdict='ESCALATE');flow.tick(now=20)
    before=flow.status('scan1');assert before['state']=='needs_attention'
    recovered=flow.supplement('scan1',selection(value));assert recovered['state']=='writing'
    assert before['request_sha256']==recovered['request_sha256']
    assert before['evidence_sha256']!=recovered['evidence_sha256']
    calls=len(native.calls);flow.supplement('scan1',selection(value));assert len(native.calls)==calls
    flow.tick(now=40);assert flow.status('scan1')['state']!='approved'  # old role attestations cannot release this revision
    publish(native,flow,value,round=2);flow.tick(now=60)
    assert flow.status('scan1')['state']=='approved'
    changed=selection(value);changed['policy_hash']='4'*64
    with pytest.raises(WorkflowError,match='immutable_selection'):flow.supplement('scan1',changed)
    with flow.db() as db:
        original=db.execute('SELECT request FROM findings_workflows').fetchone()[0]
        supplement=db.execute('SELECT previous_evidence_sha FROM findings_selection_supplements').fetchone()[0]
    assert json.loads(original)==value and supplement==before['evidence_sha256']


def test_intake_recovers_evidence_hold_then_delivers_fresh_ship_once(tmp_path):
    native,flow,value=setup(tmp_path);publish(native,flow,value,verdict='ESCALATE');flow.tick(now=20)
    reports=FindingsReports(str(tmp_path/'intake.sqlite'));reports.enqueue(value);http=MailHTTP(flow)
    kwargs=dict(base_url='https://bac.example',token='integration',http=http,mail_credentials=lambda:dict(provider='resend',api_key='local'))
    reports.tick(now=100,**kwargs)
    assert reports.lifecycle('scan1')['report_status']=='changes_requested' and not http.sends
    reports.tick(now=170,recover_selection=lambda ident:selection(value),**kwargs)
    assert flow.status('scan1')['state']=='writing' and not http.sends
    publish(native,flow,value,round=2);flow.tick(now=200)
    reports.tick(now=230,recover_selection=lambda ident:pytest.fail('should read remote state first'),**kwargs)
    reports.tick(now=250,**kwargs);reports.tick(now=270,**kwargs)
    assert len(http.sends)==1 and flow.status('scan1')['state']=='sent'


def test_final_content_escalation_is_held_without_repeating_capture_recovery(tmp_path):
    native,flow,value=setup(tmp_path);publish(native,flow,value,round=2,verdict='ESCALATE');flow.tick(now=20)
    reports=FindingsReports(str(tmp_path/'intake.sqlite'));reports.enqueue(value)
    reports.tick(now=100,base_url='https://bac.example',token='integration',http=MailHTTP(flow),
        mail_credentials=lambda:pytest.fail('no unapproved mail'),
        recover_selection=lambda ident:pytest.fail('final content review is not a missing-selection recovery'))
    row=reports.db.execute('SELECT state,error FROM findings_report_outbox').fetchone()
    assert dict(row)=={'state':'held','error':'review_escalate'}
    assert reports.lifecycle('scan1')['report_status']=='changes_requested'


def test_held_escalation_observes_later_independent_round_and_delivers_once(tmp_path):
    native, flow, value = setup(tmp_path)
    publish(native, flow, value, verdict='ESCALATE')
    flow.tick(now=20)
    assert flow.status('scan1')['error'] == 'review_escalate'
    old = deepcopy(native.comments['reviewer'][0])
    publish(native, flow, value, round=2)
    native.comments['reviewer'].append(old)
    calls = len(native.calls)
    flow.tick(now=100)
    assert flow.status('scan1')['state'] == 'approved'
    assert all(method == 'GET' for method, _ in native.calls[calls:])
    client = FindingsReports(str(tmp_path / 'intake.sqlite'))
    client.enqueue(value)
    http = MailHTTP(flow)
    kwargs = dict(base_url='https://bac.example', token='existing', http=http,
                  mail_credentials=lambda: dict(provider='resend', api_key='protected'))
    for now in (100, 120, 140):
        client.tick(now=now, **kwargs)
    assert len(http.sends) == 1
    assert flow.status('scan1')['state'] == 'sent'


def test_held_conflict_observes_existing_same_reviewer_fix_resolution(tmp_path):
    native, flow, value = setup(tmp_path)
    publish(native, flow, value)
    ship, fix = resolved_handoff_review(native)
    native.comments['reviewer'] = [fix]
    flow.update('scan1', state='needs_attention', error='conflicting_reviewer_verdicts')
    native.comments['reviewer'].insert(0, ship)
    flow.tick(now=100)
    assert flow.status('scan1')['state'] == 'approved'


def test_reconciliation_keeps_unresolved_escalation_held(tmp_path):
    native, flow, value = setup(tmp_path)
    publish(native, flow, value, verdict='ESCALATE')
    flow.tick(now=20)
    calls = len(native.calls)
    flow.tick(now=100)
    assert len(native.calls) > calls
    assert flow.status('scan1')['state'] == 'needs_attention'
    assert flow.status('scan1')['artifacts'] is None
    assert flow.status('scan1')['error'] == 'review_escalate'


@pytest.mark.parametrize('mismatch', [None, 'workflow_id', 'round', 'evidence_sha256', 'artifacts'])
def test_founder_correction_requires_every_exact_binding(mismatch):
    from findings_workflow import AUTHORIZED_CORRECTION, authorized_correction
    value = deepcopy(AUTHORIZED_CORRECTION)
    if mismatch:
        value[mismatch] = 'different'
    assert authorized_correction(value) is (mismatch is None)


@pytest.mark.parametrize('authorized', [True, False])
def test_round_four_recovery_keeps_native_review_and_artifact_checks(tmp_path, monkeypatch, authorized):
    import findings_workflow
    native, flow, value = setup(tmp_path)
    publish(native, flow, value, round=4)
    handoff = json.loads(native.comments['writer'][0]['body'])
    if authorized:
        monkeypatch.setattr(findings_workflow, 'AUTHORIZED_CORRECTION', {
            k: deepcopy(handoff[k]) for k in ('workflow_id', 'round', 'evidence_sha256', 'artifacts')})
    flow.update('scan1', state='needs_attention', error='invalid_review_round')
    # Round 3 is retained as audit history, never treated as the operative verdict.
    historical = dict(handoff, round=3)
    native.comments['writer'].append(dict(authorAgentId=WRITER, body=canonical(historical)))
    flow.tick(now=20)
    assert flow.status('scan1')['state'] == ('approved' if authorized else 'needs_attention')
    if not authorized:
        assert flow.status('scan1')['error'] == 'invalid_review_round'


def test_authorized_round_four_still_requires_independent_matching_review(tmp_path, monkeypatch):
    import findings_workflow
    native, flow, value = setup(tmp_path)
    publish(native, flow, value, round=4, author=WRITER)
    handoff = json.loads(native.comments['writer'][0]['body'])
    monkeypatch.setattr(findings_workflow, 'AUTHORIZED_CORRECTION', {
        k: deepcopy(handoff[k]) for k in ('workflow_id', 'round', 'evidence_sha256', 'artifacts')})
    flow.update('scan1', state='needs_attention', error='invalid_review_round')
    flow.tick(now=20)
    assert flow.status('scan1')['state'] == 'needs_attention'
