"""Selected execution and global limits with scripted transports; no AWS calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from contextlib import redirect_stdout, contextmanager
from unittest.mock import patch

from evals.amazon_quick_assessment import digest, load_profile
from harness.amazon_quick import Ledger, _sha, load_target
from harness.amazon_quick_accounting import admit_global_budget, checked_budget, ledger_digest, claim_selected_phase
from harness.amazon_quick_batch import ConversationLedger
from harness.runner import RunnerError
from tests import test_quick_assessment_v12 as fixtures
from tests.test_amazon_quick_batch import target, row
from tools import quick_assessment as cli
from tools.quick_qualification_probe import fixture_probe

ORDINALS = [4,21,22,31,33,40,51,68,92,93,95,96,97,98,100,101,102,103,104,105,106,107,108,116,
            161,162,163,164,165,166,167,168,241,242,243,244,275,276,278,283,351,352,353,354,411,412,413,418]


@contextmanager
def connection(path):
    db=sqlite3.connect(path)
    try:
        with db:yield db
    finally:db.close()


def manifest(profile, ordinals=ORDINALS):
    from collections import Counter
    entries, total, families = [], Counter(), Counter()
    for ordinal in ordinals:
        case = profile['cases'][ordinal-1]; calls = Counter()
        for c in case['conversations']: calls[c['role']] += len(c['turns'])
        entries.append(dict(ordinal_in_full_profile=ordinal, case_id=case['case_id'], case_sha256=digest(case),
            family=case['family'], conversations=len(case['conversations']), initial_target_calls=sum(calls.values()),
            initial_calls_by_role=dict(calls), case=deepcopy(case)))
        total.update(calls);families[case['family']] += 1
    value=dict(schema='backbond-quick-selected-case-pack/v1', pack_id='SCRIPTED-selected48', source_revision='a'*40,
        variant=profile['variant'],profile_version='1.3',full_profile_id=profile['profile_id'],
        full_profile_sha256=profile['profile_sha256'],fixture_sha256=profile['fixture_sha256'],
        cases=entries,case_ids=[c['case_id'] for c in entries],case_count=len(entries),
        conversation_count=sum(c['conversations'] for c in entries),initial_target_calls=sum(total.values()),
        initial_calls_by_role=dict(total),family_counts=dict(families))
    value['pack_sha256']=digest(value);return value


class GlobalBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.env=patch.dict(os.environ,{'ARE_AMAZON_QUICK_STATE_DIR':self.tmp.name});self.env.start();self.addCleanup(self.env.stop)
        self.t=target();self.path=Path(self.tmp.name)/'amazon-quick.sqlite3'
        c=ConversationLedger(self.t);c.close()

    def admit(self,cap=100,reserved=26):
        with connection(self.path) as db: before=ledger_digest(db)
        return admit_global_budget(self.path,baseline_sha256=before,cap=cap,reserved=reserved)

    def test_migration_conserves_all_existing_tables_and_is_one_way(self):
        with connection(self.path) as db:
            db.execute("UPDATE pilots SET calls=7")
            tables=db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            original={n:db.execute('SELECT * FROM '+n).fetchall() for n,_ in tables}
        proof=self.admit();self.assertEqual(proof['charged_or_reserved'],33)
        with connection(self.path) as db:
            for name,sql in tables:
                self.assertEqual(db.execute("SELECT sql FROM sqlite_master WHERE name=?",(name,)).fetchone()[0],sql)
                self.assertEqual(db.execute('SELECT * FROM '+name).fetchall(),original[name])
        for cap,reserved in [(100,26),(101,26),(100,0),(2501,26)]:
            with self.subTest(cap=cap,reserved=reserved),self.assertRaises(ValueError):
                admit_global_budget(self.path,baseline_sha256=proof['baseline_ledger_sha256'],cap=cap,reserved=reserved)
        with connection(self.path) as db:self.assertEqual(checked_budget(db,required=True)['charged_or_reserved'],33)

    def test_wrong_baseline_or_unresolved_submission_cannot_admit_or_modify(self):
        with self.assertRaisesRegex(ValueError,'exact_preserved_ledger'):
            admit_global_budget(self.path,baseline_sha256='f'*64,cap=100,reserved=26)
        with connection(self.path) as db:
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='quick_global_budget'").fetchone())
            db.execute("UPDATE pilots SET inflight=1")
        with self.assertRaisesRegex(ValueError,'unresolved_ledger'):self.admit()
        with connection(self.path) as db:self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='quick_global_budget'").fetchone())

    def test_global_limit_is_atomic_across_batch_and_serial_callers_and_new_pilot_ids(self):
        self.admit(cap=3,reserved=1)
        def spend(i):
            t=replace(self.t,pilot_id='new-'+str(i))
            c=ConversationLedger(t)
            try:
                if i%2:
                    c.authorize('auth-'+str(i),{'employee':t},100)
                    c.acquire('owner-'+str(i))
                    c.reserve_turn('owner-'+str(i),0,'prompt',1,time.time()+30)
                    c.accept_turn('owner-'+str(i),0,row(t,conversation_id=str(i),user_message_id=str(i)))
                    c.release('owner-'+str(i))
                else:
                    with c.case() as owner:
                        c.reserve(owner);c.accept(owner,'serial-'+str(i),'serial-'+str(i))
                return True
            except RunnerError:return False
            finally:c.close()
        with ThreadPoolExecutor(max_workers=5) as pool: accepted=list(pool.map(spend,range(12)))
        self.assertEqual(sum(accepted),2)
        with connection(self.path) as db:self.assertEqual(checked_budget(db,required=True)['charged_or_reserved'],3)

    def test_phase_claims_are_durable_and_confirmation_requires_original_capture_binding(self):
        self.admit();c=ConversationLedger(self.t)
        try:c.authorize('epoch',{'employee':self.t},100)
        finally:c.close()
        kwargs=dict(authorization='epoch',selection_sha256='a'*64,binding='b'*64)
        with self.assertRaisesRegex(ValueError,'original_capture_phase'):
            claim_selected_phase(self.path,**kwargs,phase='confirm',capture_binding='b'*64)
        claim_selected_phase(self.path,**kwargs,phase='capture')
        with self.assertRaisesRegex(ValueError,'already_claimed'):
            claim_selected_phase(self.path,**kwargs,phase='capture')
        with self.assertRaisesRegex(ValueError,'original_capture_phase'):
            claim_selected_phase(self.path,**kwargs,phase='confirm',capture_binding='c'*64)
        claim_selected_phase(self.path,**kwargs,phase='confirm',capture_binding='b'*64)
        with self.assertRaisesRegex(ValueError,'already_claimed'):
            claim_selected_phase(self.path,**kwargs,phase='confirm',capture_binding='b'*64)

    def test_failed_per_pilot_reservation_rolls_back_batch_budget_and_leaves_global_slot(self):
        self.admit(cap=10,reserved=8)
        tiny=replace(self.t,max_calls=1);c=ConversationLedger(tiny)
        try:
            c.authorize('tiny',{'employee':tiny},10);c.acquire('one')
            c.reserve_turn('one',0,'hello',1,time.time()+30);c.accept_turn('one',0,row(tiny));c.release('one')
            c.acquire('two')
            with self.assertRaisesRegex(RunnerError,'call_budget_exhausted'):c.reserve_turn('two',0,'again',1,time.time()+30)
            c.release('two')
            self.assertEqual(c.db.execute('SELECT calls FROM quick_batch_authorizations WHERE id=?',('tiny',)).fetchone(),(1,))
            self.assertEqual(checked_budget(c.db,required=True)['remaining'],1)
        finally:c.close()


class SelectionTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.PreflightTests('test_preflight_is_offline_and_requires_complete_bound_receipts');f.setUp();self.addCleanup(f.doCleanups)
        self.root=f.root;self.scope=f.scope;self.profile=load_profile('hr_finance','1.3');self.pack=manifest(self.profile)
        self.scope.update(profile_id=self.profile['profile_id'],profile_sha256=self.profile['profile_sha256'],max_calls=250)
        for key in ['platform_transport_receipt','spike_receipt']:
            p=Path(self.scope[key]);j=json.loads(p.read_text());j['probe']=fixture_probe(self.profile).metadata;p.write_text(json.dumps(j))
        for identity in self.scope['identities'].values():
            p=Path(identity['readiness_receipt']);j=json.loads(p.read_text());j['probe']=fixture_probe(self.profile).metadata;p.write_text(json.dumps(j))
        path=self.root/'amazon-quick.sqlite3'
        identity=next(iter(self.scope['identities'].values()))
        c=ConversationLedger(load_target(identity['endpoint'],os.environ[identity['token_env']]));c.close()
        with connection(path) as db:baseline=ledger_digest(db)
        admit_global_budget(path,baseline_sha256=baseline,cap=2500,reserved=26)
        self.scope['accounting']=dict(schema='backbond-quick-global-budget/v1',baseline_ledger_sha256=baseline,global_call_ceiling=2500,retained_reserved_calls=26)
        self.scope['selection']=dict(approved=True,pack_sha256=self.pack['pack_sha256'],max_attempts=3,applicability_approval_ref='SCRIPTED://reviewed')
        targets={r:load_target(v['endpoint'],os.environ[v['token_env']]) for r,v in self.scope['identities'].items()}
        c=ConversationLedger(next(iter(targets.values())))
        try:c.authorize(self.scope['authorization_id'],targets,250)
        finally:c.close()

    def test_frozen_48_has_61_calls_and_unchanged_600_case_profile(self):
        before=digest(self.profile)
        self.assertEqual(len(cli.validate_selection(self.pack,self.profile,self.scope)),48)
        self.assertEqual(self.pack['initial_target_calls'],61)
        self.assertEqual(self.pack['initial_calls_by_role'],dict(employee=19,sales_manager=25,hr=17))
        self.assertEqual(len(cli.preflight(self.scope,self.profile,selection=self.pack)[0]),3)
        self.assertEqual(digest(self.profile),before);self.assertEqual(self.profile['case_count'],600)
        self.assertEqual(self.profile['initial_target_calls'],795)

    def test_rehashed_oracle_duplicate_unknown_case_or_call_plan_cannot_change_selection(self):
        for change in ['oracle','duplicate','unknown','budget','profile']:
            m=deepcopy(self.pack);p=deepcopy(self.profile)
            if change=='oracle':m['cases'][0]['case']['expected']='attacker expectation';m['cases'][0]['case_sha256']=digest(m['cases'][0]['case'])
            if change=='duplicate':m['case_ids'][1]=m['case_ids'][0]
            if change=='unknown':m['cases'][0]['ordinal_in_full_profile']=601
            if change=='budget':m['initial_target_calls']=1
            if change=='profile':p['cases'][0]['expected']='changed';p['profile_sha256']=digest({k:v for k,v in p.items() if k!='profile_sha256'})
            m['pack_sha256']=digest({k:v for k,v in m.items() if k!='pack_sha256'})
            with self.subTest(change=change),self.assertRaises(cli.PreflightError):cli.validate_selection(m,p)

    def test_selection_keeps_identity_isolation_concurrency_and_aggregate_guards(self):
        for key in ['spike_receipt','isolation_receipt','platform_transport_receipt']:
            old=self.scope[key];self.scope[key]=str(self.root/'absent')
            with self.subTest(key=key),self.assertRaises(OSError):cli.preflight(self.scope,self.profile,selection=self.pack)
            self.scope[key]=old
        with self.assertRaisesRegex(cli.PreflightError,'selection_requires_assessment'):
            cli.preflight(self.scope,self.profile,purpose='concurrency_spike',selection=self.pack)
        self.scope['max_calls']=100
        with self.assertRaises(cli.PreflightError):cli.preflight(self.scope,self.profile,selection=self.pack)

    def test_missing_or_changed_global_pool_or_exhausted_old_batch_fails_preflight(self):
        old=deepcopy(self.scope['accounting']);self.scope['accounting']['retained_reserved_calls']=0
        with self.assertRaisesRegex(cli.PreflightError,'global_budget_binding_changed'):cli.preflight(self.scope,self.profile,selection=self.pack)
        self.scope['accounting']=old
        with connection(self.root/'amazon-quick.sqlite3') as db:db.execute('UPDATE quick_batch_authorizations SET cap=63,calls=63')
        with self.assertRaisesRegex(cli.PreflightError,'same_immutable_epoch'):cli.preflight(self.scope,self.profile,selection=self.pack)

    def test_spike_budgets_its_exact_44_calls_without_weakening_assessment_budget(self):
        scope=deepcopy(self.scope);scope.pop('selection');scope['approved_operations'].append('concurrency_spike')
        registry=self.root/'targets.json';j=json.loads(registry.read_bytes())
        for t in j['targets']:t['max_calls']=17 if t['pilot_id']=='hr' else 18
        registry.write_text(json.dumps(j))
        self.assertEqual(len(cli.preflight(scope,self.profile,purpose='concurrency_spike')[0]),3)
        with self.assertRaisesRegex(cli.PreflightError,'target_budget_insufficient'):cli.preflight(scope,self.profile)

    def test_capture_confirm_and_saved_regrade_execute_only_selected_canonical_cases(self):
        packpath=self.root/'selection.json';packpath.write_text(json.dumps(self.pack));scopepath=self.root/'scope.json';scopepath.write_text(json.dumps(self.scope))
        submitted=[];profile=self.profile;failure=cli.selected_cases(profile,self.pack)[0]
        class FakeRunner:
            def __init__(self,targets,**kw):self.targets=targets;self.callback=kw['on_result']
            async def run(self,cases):
                for case in cases:
                    submitted.append(case);t=self.targets[case.role];turns=[]
                    for index,prompt in enumerate(case.turns):
                        reply=failure['marker'] if case.case_id.startswith(failure['case_id']+':') and index==len(case.turns)-1 else 'SCRIPTED refusal'
                        turns.append(dict(prompt=prompt,reply=reply,transport=dict(source='aws_chat_logs',native_status_code='success',
                            user_arn_sha256=_sha(t.user_arn),account_id_sha256=_sha(t.account_id),agent_id_sha256=_sha(t.agent_id),
                            prompt_sha256=_sha(prompt),response_sha256=_sha(reply),conversation_sha256=_sha(case.case_id),
                            message_sha256=_sha(case.case_id+str(index)),cited_resource=[],metadata_complete=True)))
                    self.callback(dict(case_id=case.case_id,role=case.role,status='observed',turns=turns))
                return dict(status='capture_complete',results=[])
        output=self.root/'capture'
        args=['capture','--profile-version','1.3','--scope',str(scopepath),'--selection',str(packpath),'--output',str(output),'--live','--confirm-failures']
        with patch.object(cli,'QuickBatchRunner',FakeRunner),redirect_stdout(io.StringIO()):self.assertEqual(cli.main(args),0)
        run=json.loads((output/'run.json').read_bytes());self.assertEqual(len(run['results']),48)
        self.assertEqual(sum(len(c.turns) for c in submitted if ':1:' in c.case_id),61)
        self.assertEqual({c.case_id.split(':')[0] for c in submitted if ':2:' in c.case_id},{failure['case_id']})
        verdict=next(v for v in run['results'] if v['case_id']==failure['case_id'])
        self.assertEqual(verdict['confirmation_status'],'confirmed_3_of_3')
        self.assertIn('Unselected cases were not executed',(output/'report.md').read_text())
        count=len(submitted);args[args.index('--output')+1]=str(self.root/'duplicate-capture')
        with patch.object(cli,'QuickBatchRunner',FakeRunner),redirect_stdout(io.StringIO()):self.assertEqual(cli.main(args),2)
        self.assertEqual(len(submitted),count)
        for use_selection in [True,False]:
            out=self.root/('regrade-'+str(use_selection));args=['regrade','--profile-version','1.3','--scope',str(scopepath),'--run',str(output/'run.json'),'--output',str(out)]
            if use_selection:args+=['--selection',str(packpath)]
            with patch.object(cli,'load_target',side_effect=AssertionError('saved regrade must stay offline')),redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(args),0 if use_selection else 2)
            if use_selection:self.assertEqual(len(json.loads((out/'run.json').read_bytes())['results']),48)
            else:self.assertFalse(out.exists())
        altered=deepcopy(run);extra=deepcopy(altered['captures'][0]);extra['case_id']='unselected-case:1:0';altered['captures'].append(extra)
        altered['run_sha256']=digest({k:v for k,v in altered.items() if k!='run_sha256'})
        altered_path=self.root/'overcapture.json';altered_path.write_text(json.dumps(altered));out=self.root/'overcapture-regrade'
        with redirect_stdout(io.StringIO()):self.assertEqual(cli.main(['regrade','--profile-version','1.3','--scope',str(scopepath),
            '--selection',str(packpath),'--run',str(altered_path),'--output',str(out)]),2)
        self.assertFalse(out.exists())

    def test_local_semantic_review_selects_fresh_confirmations_then_requires_review_of_new_answers(self):
        from tools.quick_operator_review import prepare_review,SCHEMA as REVIEW_SCHEMA
        self.scope.update(allowed_evaluators=['none','operator_local'],operator_review_approved=True,
            operator_review_approval_ref='SCRIPTED://same-private-review-boundary')
        packpath=self.root/'selection.json';packpath.write_text(json.dumps(self.pack))
        scopepath=self.root/'scope.json';scopepath.write_text(json.dumps(self.scope));submitted=[]
        async def scripted_capture(profile,ids,attempt,scope,targets,output,run,deadline):
            for case in cli.selected_cases(profile,self.pack):
                if case['case_id'] not in ids:continue
                for i,conversation in enumerate(case['conversations']):
                    cid=case['case_id']+f':{attempt}:{i}';submitted.append(cid);t=targets[conversation['role']];turns=[]
                    for turn,prompt in enumerate(conversation['turns']):
                        reply='SCRIPTED response for orchestration only'
                        turns.append(dict(prompt=prompt,reply=reply,transport=dict(source='aws_chat_logs',native_status_code='success',
                            user_arn_sha256=_sha(t.user_arn),account_id_sha256=_sha(t.account_id),agent_id_sha256=_sha(t.agent_id),
                            prompt_sha256=_sha(prompt),response_sha256=_sha(reply),conversation_sha256=_sha(cid),message_sha256=_sha(cid+str(turn)),
                            cited_resource=[],metadata_complete=True)))
                    run['captures'].append(dict(case_id=cid,role=conversation['role'],status='observed',attempt=attempt,turns=turns))
            return True
        initial=self.root/'initial'
        args=['capture','--profile-version','1.3','--scope',str(scopepath),'--selection',str(packpath),'--output',str(initial),'--live']
        with patch.object(cli,'capture_cases',scripted_capture),redirect_stdout(io.StringIO()):self.assertEqual(cli.main(args),0)
        raw=(initial/'run.json').read_bytes();run=json.loads(raw)
        failure=next(c for c in cli.selected_cases(self.profile,self.pack) if c['family']=='harmful_content')
        def review(path,source,rawsource):
            packet=prepare_review(self.profile,self.scope,source,rawsource)
            doc={k:packet[k] for k in ['profile_sha256','scope_sha256','source_run_file_sha256','processing_approval_ref']}
            doc.update(schema=REVIEW_SCHEMA,reviewer='SCRIPTED orchestration fixture',reviewer_kind='secondary_AI',decisions=[
                dict(payload_sha256=t['payload_sha256'],verdict='failed',reason='SCRIPTED semantic failure, not a model quality finding.')
                for t in packet['tasks'] if t['case_id']==failure['case_id']])
            path.write_text(json.dumps(doc));return path
        firstreview=review(self.root/'first-review.json',run,raw)
        # Simulate the initial 61 charged calls. Remaining batch room is180,
        # below183; confirmation must budget only the failed case's two attempts.
        with connection(self.root/'amazon-quick.sqlite3') as db:
            for role,calls in self.pack['initial_calls_by_role'].items():db.execute('UPDATE pilots SET calls=calls+? WHERE id=?',(calls,role))
            db.execute('UPDATE quick_batch_authorizations SET calls=calls+61')
        with self.assertRaisesRegex(cli.PreflightError,'aggregate_budget_insufficient'):
            cli.preflight(self.scope,self.profile,selection=self.pack)
        confirmed=self.root/'confirm'
        args=['confirm','--profile-version','1.3','--scope',str(scopepath),'--selection',str(packpath),
            '--run',str(initial/'run.json'),'--operator-review',str(firstreview),'--output',str(confirmed),'--live']
        with patch.object(cli,'capture_cases',scripted_capture),redirect_stdout(io.StringIO()):self.assertEqual(cli.main(args),0)
        self.assertEqual({v.split(':')[0] for v in submitted if ':2:' in v},{failure['case_id']})
        combined_raw=(confirmed/'run.json').read_bytes();combined=json.loads(combined_raw)
        result=next(v for v in combined['results'] if v['case_id']==failure['case_id'])
        self.assertNotEqual(result['confirmation_status'],'confirmed_3_of_3')
        count=len(submitted);args[args.index('--output')+1]=str(self.root/'replayed-confirm')
        with patch.object(cli,'capture_cases',scripted_capture),redirect_stdout(io.StringIO()):self.assertEqual(cli.main(args),2)
        self.assertEqual(len(submitted),count)
        finalreview=review(self.root/'all-attempt-review.json',combined,combined_raw);final=self.root/'final'
        args=['regrade','--profile-version','1.3','--scope',str(scopepath),'--selection',str(packpath),
            '--run',str(confirmed/'run.json'),'--operator-review',str(finalreview),'--output',str(final)]
        with redirect_stdout(io.StringIO()):self.assertEqual(cli.main(args),0)
        result=next(v for v in json.loads((final/'run.json').read_bytes())['results'] if v['case_id']==failure['case_id'])
        self.assertEqual(result['confirmation_status'],'confirmed_3_of_3')


class SourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        from tools import quick_source_checks as sources
        self.sources=sources;self.profile=load_profile('hr_finance','1.3');self.plan=sources.prepare(self.profile,'a'*40)
        self.scope=dict(selection=dict(approved=True,pack_sha256=self.plan['pack_sha256'],max_attempts=1,
            applicability_approval_ref='SCRIPTED://source-review'),source_setup_ref='SCRIPTED://installed-version-review',
            sources={n:dict(source_sha256=_sha(b),resource_id='source:'+n,native_resource_type='dataset' if n.endswith('.csv') else 'document')
                for n,b in self.profile['fixture']['sources'].items()})
        self.bindings={'sources':self.scope['sources'],**{r:dict(user_arn_sha256=_sha(r),account_id_sha256=_sha('account'),
            agent_id_sha256=_sha('agent')) for r in self.profile['fixture']['roles']}}

    def captured(self,case,*,exact=False):
        result=[]
        for i,c in enumerate(case['conversations']):
            turns=[]
            for turn,prompt in enumerate(c['turns']):
                source=case['fixture_dependencies'][i if len(case['fixture_dependencies'])>1 else 0]
                citations=[dict(citedResourceType='document',citedResourceId='source:'+source)] if exact else [dict(citedResourceType='space',citedResourceId='space-only')]
                turns.append(dict(prompt=prompt,reply='SCRIPTED answer',transport={**self.bindings[c['role']],
                    'source':'aws_chat_logs','native_status_code':'success','prompt_sha256':_sha(prompt),
                    'response_sha256':_sha('SCRIPTED answer'),'conversation_sha256':_sha(str(i)),'message_sha256':_sha(str(i)+str(turn)),
                    'metadata_complete':True,'cited_resource':citations}))
            result.append(dict(case_id=case['case_id']+f':1:{i}',role=c['role'],attempt=1,status='observed',turns=turns))
        run=dict(profile_sha256=self.profile['profile_sha256'],scope_sha256=digest(self.scope),bindings=self.bindings,
            selection=cli.selection_binding(self.plan,self.scope),captures=result)
        run['run_sha256']=digest(run);return run

    def test_all100_positive_source_cases_have_140_calls_without_denial_exemptions(self):
        self.assertEqual(self.plan['case_count'],100);self.assertEqual(self.plan['initial_target_calls'],140)
        self.assertEqual(self.plan['family_counts'],dict(prompt_injection_indirect=40,grounded_facts=40,permitted_utility=20))
        self.assertFalse(self.plan['execution_approved']);self.assertEqual(self.profile['case_count'],600)

    def test_space_citation_and_answer_prose_cannot_prove_exact_source_or_chunk(self):
        case=next(c for c in self.plan['cases'] if c['family']=='prompt_injection_indirect')['case']
        run=self.captured(case);audit=self.sources.audit(self.profile,self.scope,self.plan,run)
        rows=[v for v in audit['results'] if v['case_id']==case['case_id']]
        self.assertTrue(all(v['bound_provider_evidence'] for v in rows))
        self.assertTrue(all(not v['native_exact_source_id'] and not v['message_bound_chunk_attestation_present'] for v in rows))
        self.assertTrue(all(v['gap']=='exact_native_source_identity_missing' for v in rows))

    def test_exact_file_identity_never_implies_indexed_version_chunk_or_backend_permissions(self):
        case=next(c for c in self.plan['cases'] if c['family']=='prompt_injection_indirect')['case']
        audit=self.sources.audit(self.profile,self.scope,self.plan,self.captured(case,exact=True))
        rows=[v for v in audit['results'] if v['case_id']==case['case_id']]
        self.assertTrue(all(v['native_exact_source_id'] for v in rows))
        self.assertTrue(all(not v['source_version_verified_by_tool'] for v in rows))
        self.assertTrue(all(not v['message_bound_chunk_attestation_present'] for v in rows))
        self.assertEqual(audit['target_calls'],0);self.assertFalse(any('verdict' in v for v in rows))

    def test_audit_rejects_changed_scope_or_saved_run_without_target_calls(self):
        case=self.plan['cases'][0]['case'];run=self.captured(case)
        run['captures'][0]['turns'][0]['reply']='changed'
        with self.assertRaisesRegex(ValueError,'saved_selected_profile_scope'):self.sources.audit(self.profile,self.scope,self.plan,run)

    def test_matching_id_with_space_unknown_or_container_type_never_becomes_file_evidence(self):
        case=self.plan['cases'][0]['case']
        for kind in ['space','knowledge_base','unknown',None]:
            run=self.captured(case,exact=True)
            for capture in run['captures']:
                for turn in capture['turns']:turn['transport']['cited_resource'][0]['citedResourceType']=kind
            run['run_sha256']=digest({k:v for k,v in run.items() if k!='run_sha256'})
            rows=[v for v in self.sources.audit(self.profile,self.scope,self.plan,run)['results'] if v['case_id']==case['case_id']]
            with self.subTest(kind=kind):self.assertTrue(all(not v['native_exact_source_id'] for v in rows))
        source=case['fixture_dependencies'][0]
        self.scope['sources'][source]['resource_id']='arn:aws:quicksight:us-east-1:111122223333:knowledge-base/container'
        run=self.captured(case,exact=True)
        for capture in run['captures']:
            capture['turns'][-1]['transport']['cited_resource'][0]['citedResourceId']=self.scope['sources'][source]['resource_id']
        run['run_sha256']=digest({k:v for k,v in run.items() if k!='run_sha256'})
        row=next(v for v in self.sources.audit(self.profile,self.scope,self.plan,run)['results'] if v['source_name']==source)
        self.assertFalse(row['native_exact_source_id']);self.assertFalse(row['reviewed_file_or_dataset_lineage'])


if __name__=='__main__':unittest.main()
