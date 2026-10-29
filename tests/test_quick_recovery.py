"""Recovery cannot replay a possible send, refund a slot, or alter scope/caps."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace,asdict
from datetime import datetime,timezone
import json,os,sqlite3,time,unittest
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from evals.amazon_quick_assessment import digest
from harness.amazon_quick import load_target,knowledge_evidence
from harness.amazon_quick_accounting import claim_selected_phase,ledger_digest,checked_budget
from harness.amazon_quick_batch import BatchCase,ConversationLedger
from harness.amazon_quick_recovery import recovery_plan,apply_recovery,capture_run_binding
from tests.test_amazon_quick_batch import row
from tests import test_quick_selected_assessment as selected_fixtures
from tools.quick_assessment import preflight,selection_binding,validate_selection
from tools import quick_assessment as cli

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        f=selected_fixtures.SelectionTests('test_frozen_48_has_61_calls_and_unchanged_600_case_profile');f.setUp();self.addCleanup(f.doCleanups)
        self.scope=deepcopy(f.scope);self.profile=f.profile
        self.selection=selected_fixtures.manifest(self.profile,[4,21,351])
        self.scope['selection']['pack_sha256']=self.selection['pack_sha256']
        self.path=f.root/'amazon-quick.sqlite3'
        self.now=time.time();iso=lambda epoch:datetime.fromtimestamp(epoch,timezone.utc).isoformat()
        self.scope['recovery_policy']={'approved':True,'approval_ref':'SCRIPTED://recovery-review','max_recoveries':1,
            'runtime_window':{'first_start_utc':iso(self.now-60),'absolute_stop_utc':iso(self.now-60+14400)}}
        self.targets,self.bindings=preflight(self.scope,self.profile,selection=self.selection)
        self.saved_run={'schema':'backbond-quick-assessment-run/v1.3','status':'incomplete',
            'capture_protocol':'durable_scoped_send_intent_v2','capture_run_id':'a'*32,
            'capture_metrics':[],
            'target_registration_sha256':{role:digest(asdict(t)) for role,t in self.targets.items()},
            'profile_sha256':self.profile['profile_sha256'],'scope_sha256':digest(self.scope),
            'selection':selection_binding(self.selection,self.scope),'bindings':self.bindings,'captures':[]}
        self.capture_binding=capture_run_binding(self.saved_run)
        self.expected=[BatchCase(c['case_id']+':1:'+str(i),v['role'],tuple(v['turns']))
            for c in validate_selection(self.selection,self.profile,self.scope) for i,v in enumerate(c['conversations'])]
        claim_selected_phase(self.path,authorization=self.scope['authorization_id'],selection_sha256=self.selection['pack_sha256'],
            phase='capture',binding=self.capture_binding)
        first=self.expected[0];t=self.targets[first.role];ledger=ConversationLedger(t)
        try:
            ledger.authorize(self.scope['authorization_id'],self.targets,self.scope['max_calls'])
            ledger.acquire('observed-owner',first,capture_binding=self.capture_binding);turns=[]
            for i,prompt in enumerate(first.turns):
                ledger.reserve_turn('observed-owner',i,prompt,1,time.time()+30);ledger.mark_send_intent('observed-owner',i)
                native=row(t,prompt,conversation_id='observed-c',user_message_id='m'+str(i),system_message_id='s'+str(i))
                ledger.accept_turn('observed-owner',i,native)
                turns.append({'prompt':prompt,'reply':native['system_text_message'],'transport':knowledge_evidence(native,t),
                    'browser_response_sha256':None})
            ledger.release('observed-owner')
        finally:ledger.close()
        second=self.expected[1];self.pending=second;t=self.targets[second.role];ledger=ConversationLedger(t)
        try:
            ledger.authorize(self.scope['authorization_id'],self.targets,self.scope['max_calls'])
            ledger.acquire('unsent-owner',second,capture_binding=self.capture_binding);ledger.reserve_turn('unsent-owner',0,second.turns[0],1,time.time()+30)
            ledger.uncertain('unsent-owner',0,'browser_harness_unavailable');ledger.release('unsent-owner');ledger.halt_authorization()
        finally:ledger.close()
        self.saved_run['captures']=[{'case_id':first.case_id,'role':first.role,'attempt':1,'status':'observed','turns':turns},
                {'case_id':second.case_id,'role':second.role,'attempt':1,'status':'could_not_run','turns':[],
                 'attempted_turns':0,'reason':'browser_harness_unavailable'}]
        self.raw=self.seal(self.saved_run)

    def seal(self,run):
        run['run_sha256']=digest({k:v for k,v in run.items() if k!='run_sha256'})
        return (json.dumps(run,sort_keys=True)+'\n').encode()

    def plan(self,**changes):
        db=sqlite3.connect(self.path)
        try:return recovery_plan(db,self.profile,self.scope,self.selection,self.saved_run,self.raw,targets=changes.get('targets',self.targets),now=changes.get('now',self.now))
        finally:db.close()

    def approved(self):
        self.original_snapshot=self.path.parent/('original-'+str(time.time_ns())+'.sqlite3')
        source=sqlite3.connect(self.path);copy=sqlite3.connect(self.original_snapshot)
        source.backup(copy);copy.close();source.close()
        p=self.plan();p.update(approved=True,operator_approval_ref='SCRIPTED://operator-disposition',
            stopped_runtime_evidence_ref='SCRIPTED://stopped-export-lock')
        return p

    def apply(self,p):
        return apply_recovery(self.path,self.profile,self.scope,self.selection,self.saved_run,self.raw,p,targets=self.targets,
            source_snapshot=self.original_snapshot,now=self.now)

    def snapshot(self):
        db=sqlite3.connect(self.path)
        try:return ledger_digest(db),checked_budget(db,required=True)
        finally:db.close()

    def test_exact_remaining_plan_and_apply_preserve_all_charges_observed_rows_and_claim(self):
        p=self.approved();before,budget=self.snapshot()
        self.assertEqual(p['completed_capture_ids'],[self.expected[0].case_id])
        self.assertEqual([x['capture_id'] for x in p['remaining']],[x.case_id for x in self.expected[1:]])
        self.assertEqual(p['expected_new_calls'],sum(len(c.turns) for c in self.expected[1:]))
        self.assertEqual(len(p['no_send_dispositions']),1)
        result=self.apply(p);self.assertEqual(result['charged_or_reserved'],budget['charged_or_reserved'])
        db=sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute("SELECT status FROM quick_batch_submissions WHERE owner='unsent-owner'").fetchone(),('reviewed_not_submitted',))
            self.assertEqual(db.execute("SELECT status FROM quick_batch_submissions WHERE owner='observed-owner' LIMIT 1").fetchone(),('verified',))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM quick_recovery_actions').fetchone(),(1,))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM quick_selected_phases WHERE phase='capture'").fetchone(),(1,))
            self.assertFalse(db.execute('SELECT 1 FROM pilots WHERE halted!=0 OR inflight!=0').fetchone())
        finally:db.close()
        self.assertEqual(self.raw,self.seal(self.saved_run));self.assertNotEqual(before,result['ledger_sha256'])
        with self.assertRaises(ValueError):self.apply(p)

    def test_no_send_proof_cannot_be_replaced_with_absent_logs_or_zero_attempted_flag(self):
        db=sqlite3.connect(self.path)
        with db:db.execute("INSERT INTO quick_batch_send_intents VALUES ('unsent-owner',0,?)",(self.now,))
        db.close();before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'possibly_sent'):self.plan()
        self.assertEqual(before,self.snapshot())

    def test_legacy_missing_capture_protocol_or_durable_key_is_not_recovery_authority(self):
        self.saved_run.pop('capture_protocol');self.raw=self.seal(self.saved_run)
        with self.assertRaisesRegex(ValueError,'durable_capture'):self.plan()

    def test_missing_owner_key_or_changed_prompt_binding_is_rejected(self):
        db=sqlite3.connect(self.path)
        with db:db.execute("DELETE FROM quick_batch_capture_keys WHERE owner='unsent-owner'")
        db.close()
        with self.assertRaisesRegex(ValueError,'current_capture_owner_key_required'):self.plan()

    def test_membership_changes_refunds_and_observed_replay_cannot_be_approved(self):
        original=self.approved()
        for alter in ['drop','replay','refund','calls']:
            p=deepcopy(original)
            if alter=='drop':p['remaining'].pop()
            elif alter=='replay':p['remaining'][0]['capture_id']=self.expected[0].case_id
            elif alter=='refund':p['refund_calls']=1
            else:p['epoch_before']-=1
            before=self.snapshot()
            with self.subTest(alter=alter),self.assertRaisesRegex(ValueError,'plan_or_working_ledger'):self.apply(p)
            self.assertEqual(before,self.snapshot())

    def test_role_caps_concurrency_identity_and_runtime_deadline_are_immutable(self):
        role=next(iter(self.targets));targets=dict(self.targets)
        for change in [{'max_calls':self.targets[role].max_calls+1},{'max_concurrent_conversations':1},
                       {'user_arn':self.targets[role].user_arn+'-other'},{'region':'us-west-2'},
                       {'timeout_seconds':self.targets[role].timeout_seconds+1},{'expires_at':self.now-1}]:
            targets[role]=replace(self.targets[role],**change)
            with self.subTest(change=change),self.assertRaises(ValueError):self.plan(targets=targets)
        with self.assertRaisesRegex(ValueError,'deadline'):self.plan(now=self.now+14400)

    def test_owner_identity_mutation_or_observed_native_mismatch_cannot_skip_work(self):
        db=sqlite3.connect(self.path)
        with db:db.execute("UPDATE quick_batch_cases SET identity='different' WHERE owner='unsent-owner'")
        db.close()
        with self.assertRaisesRegex(ValueError,'owner_pilot_or_identity'):self.plan()

    def test_partial_transaction_failure_rolls_back_disposition_unhalt_and_new_table(self):
        p=self.approved();db=sqlite3.connect(self.path)
        with db:db.execute("CREATE TRIGGER recovery_fail BEFORE UPDATE OF halted ON pilots WHEN NEW.halted=0 BEGIN SELECT RAISE(ABORT,'SCRIPTED rollback');END")
        db.close();before=self.snapshot()
        # The trigger was added after planning. Replan against that exact schema.
        p=self.approved()
        with self.assertRaises(sqlite3.IntegrityError):self.apply(p)
        self.assertEqual(before,self.snapshot())

    def test_concurrent_claims_commit_once_and_preserve_global_budget(self):
        p=self.approved();before=self.snapshot()[1]
        def attempt(_):
            try:self.apply(p);return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:accepted=list(pool.map(attempt,range(2)))
        self.assertEqual(sum(accepted),1);self.assertEqual(before,self.snapshot()[1])

    def test_accept_commit_before_checkpoint_is_reconciled_without_resend(self):
        completed=self.saved_run['captures'].pop(0);self.raw=self.seal(self.saved_run)
        p=self.approved()
        self.assertEqual(p['completed_capture_ids'],[completed['case_id']])
        self.assertEqual(p['reconciled_captures'],[completed])
        self.assertNotIn(completed['case_id'],[r['capture_id'] for r in p['remaining']])
        before=self.snapshot()[1];self.apply(p);self.assertEqual(before,self.snapshot()[1])

    def test_prior_probe_with_same_case_id_cannot_supply_a_missing_full_capture(self):
        # A prior probe is verified, while the current full run has never sent
        # this canonical case. Preserve the probe, but schedule fresh full work.
        prior=self.saved_run['captures'].pop(0)
        self.move_owner_to_prior_probe('observed-owner')
        self.raw=self.seal(self.saved_run);before=self.snapshot()
        plan=self.plan()
        self.assertEqual(plan['completed_capture_ids'],[])
        self.assertEqual(plan['reconciled_captures'],[])
        self.assertIn(prior['case_id'],[v['capture_id'] for v in plan['remaining']])
        self.assertEqual(before,self.snapshot())

    def test_saved_observation_from_another_capture_run_is_rejected(self):
        self.move_owner_to_prior_probe('observed-owner');before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'observed_capture_durable_native_binding_required'):self.plan()
        self.assertEqual(before,self.snapshot())

    def test_other_phase_unknown_owner_still_blocks_global_recovery(self):
        self.move_owner_to_prior_probe('unsent-owner');before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'possibly_sent_work_requires_separate_adjudication'):self.plan()
        self.assertEqual(before,self.snapshot())

    def move_owner_to_prior_probe(self,owner):
        db=sqlite3.connect(self.path)
        with db:
            db.execute('UPDATE quick_batch_capture_scopes SET capture_binding=? WHERE owner=?',('b'*64,owner))
            db.execute('INSERT OR IGNORE INTO quick_selected_phases VALUES (?,?,?,?,?)',
                (self.scope['authorization_id'],'e'*64,'capture','b'*64,'SCRIPTED-prior-probe'))
        db.close()

    def test_missing_owner_scope_and_missing_checkpoint_cannot_replay_observed_work(self):
        self.saved_run['captures'].pop(0);self.raw=self.seal(self.saved_run)
        db=sqlite3.connect(self.path)
        with db:db.execute("DELETE FROM quick_batch_capture_scopes WHERE owner='observed-owner'")
        db.close();before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'missing_or_unclaimed_capture_owner_binding'):self.plan()
        self.assertEqual(before,self.snapshot())

    def test_missing_or_foreign_current_owner_key_without_checkpoint_cannot_replay(self):
        self.saved_run['captures'].pop(0);self.raw=self.seal(self.saved_run)
        db=sqlite3.connect(self.path)
        try:
            original=db.execute("SELECT * FROM quick_batch_capture_keys WHERE owner='observed-owner'").fetchone()
            for mutation in ('missing','foreign'):
                with db:
                    db.execute("DELETE FROM quick_batch_capture_keys WHERE owner='observed-owner'")
                    if mutation=='foreign':db.execute('INSERT INTO quick_batch_capture_keys VALUES (?,?,?,?,?)',
                        (original[0],'outside-original-selection',*original[2:]))
                before=self.snapshot()
                with self.subTest(mutation=mutation),self.assertRaisesRegex(ValueError,'current_capture_owner_key_required'):self.plan()
                self.assertEqual(before,self.snapshot())
        finally:db.close()

    def test_orphan_native_owner_without_checkpoint_requires_separate_adjudication(self):
        self.saved_run['captures'].pop(0);self.raw=self.seal(self.saved_run)
        db=sqlite3.connect(self.path)
        with db:db.execute("DELETE FROM quick_batch_cases WHERE owner='observed-owner'")
        db.close();before=self.snapshot()
        with self.assertRaises(ValueError):self.plan()
        self.assertEqual(before,self.snapshot())

    def test_missing_nonce_unscoped_protocol_and_changed_nonce_cannot_recover(self):
        for mutation in ('missing_nonce','old_protocol','different_nonce'):
            run=deepcopy(self.saved_run)
            if mutation=='missing_nonce':run.pop('capture_run_id')
            elif mutation=='old_protocol':run['capture_protocol']='durable_send_intent_v1'
            else:run['capture_run_id']='c'*32
            raw=self.seal(run);before=self.snapshot();db=sqlite3.connect(self.path)
            try:
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                    recovery_plan(db,self.profile,self.scope,self.selection,run,raw,targets=self.targets,now=self.now)
            finally:db.close()
            self.assertEqual(before,self.snapshot())

    def test_changed_nonce_and_dropped_checkpoints_cannot_authorize_replaying_the_capture(self):
        run=deepcopy(self.saved_run);run['capture_run_id']='c'*32;run['captures']=[]
        raw=self.seal(run);before=self.snapshot();db=sqlite3.connect(self.path)
        try:
            with self.assertRaisesRegex(ValueError,'original_capture_claim_required'):
                recovery_plan(db,self.profile,self.scope,self.selection,run,raw,targets=self.targets,now=self.now)
        finally:db.close()
        self.assertEqual(before,self.snapshot())

    def cli_files(self):
        root=self.path.parent
        values={'scope':self.scope,'selection':self.selection,'run':self.saved_run,'recovery-plan':self.approved()}
        paths={}
        for key,value in values.items():
            paths[key]=root/(key+'.json');paths[key].write_text(json.dumps(value),encoding='utf-8')
        paths['run'].write_bytes(self.raw)
        paths['source-ledger-snapshot']=self.original_snapshot
        return paths

    def test_cli_invalid_required_receipt_never_unhalts_or_consumes_recovery(self):
        paths=self.cli_files();before=self.snapshot()
        Path(self.scope['platform_transport_receipt']).write_text('{}')
        args=['resume','--profile-version','1.3','--live','--output',str(self.path.parent/'invalid-cli')]
        for key,path in paths.items():args+=['--'+key,str(path)]
        with patch.object(cli,'QuickBatchRunner',side_effect=AssertionError('no network')),redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(args),2)
        self.assertEqual(before,self.snapshot())

    def test_cli_recovery_preserves_completed_and_filters_exact_remaining(self):
        paths=self.cli_files();p=self.plan();submitted=[]
        class FakeRunner:
            def __init__(runner,targets,**kw):
                self.assertEqual(kw['capture_binding'],self.capture_binding)
                runner.callback=kw['on_result']
            async def run(self,cases):
                for case in cases:
                    submitted.append(case.case_id)
                    self.callback(dict(case_id=case.case_id,role=case.role,status='could_not_run',reason='SCRIPTED stop',turns=[],attempted_turns=0))
                return dict(status='incomplete',results=[])
        output=self.path.parent/'valid-cli'
        args=['resume','--profile-version','1.3','--live','--output',str(output)]
        for key,path in paths.items():args+=['--'+key,str(path)]
        with patch.object(cli,'QuickBatchRunner',FakeRunner),redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(args),1)
        self.assertEqual(submitted,[r['capture_id'] for r in p['remaining']])
        saved=json.loads((output/'run.json').read_bytes())
        self.assertEqual(saved['captures'][0],self.saved_run['captures'][0])
        self.assertEqual(saved['recovery_lineage'][0]['preserved_observed_capture_ids'],p['completed_capture_ids'])

if __name__=='__main__':unittest.main()
