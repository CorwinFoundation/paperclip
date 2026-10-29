"""Reviewed recovery of provably unsent work. No AWS calls and no refunds.

The original run and ledger snapshot are inputs, never rewritten here. A new
working ledger must contain durable case keys and pre-send intents. Older
captures require a separate, qualified legacy adjudication; absence of a new
table alone never proves that an old worker did not send.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime,timezone
import json
import hashlib
from pathlib import Path
import re
import sqlite3
import time

from evals.amazon_quick_assessment import bound_turn,digest
from harness.amazon_quick import _sha
from harness.amazon_quick_accounting import checked_budget,ledger_digest

SCHEMA='backbond-quick-reviewed-recovery/v1'
LEGACY_NO_SEND_WORKER_SHA256='0536231b7a6bd4808a334d030040eb10d9ea2b71af84de03ec3ea6b85fe5822b'
ACTION_DDL='''CREATE TABLE IF NOT EXISTS quick_recovery_actions
 (id TEXT PRIMARY KEY,kind TEXT NOT NULL,authorization TEXT NOT NULL,
 selection_sha256 TEXT NOT NULL,source_run_sha256 TEXT NOT NULL,
 plan_sha256 TEXT NOT NULL,before_ledger_sha256 TEXT NOT NULL,
 details_json TEXT NOT NULL,created_at TEXT NOT NULL)'''

def _require(value,message):
    if not value:raise ValueError(message)

def _has(db,table):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone())

def capture_run_binding(run):
    """Keep recurring canonical cases in separate probe/full-capture runs."""
    _require(run.get('capture_protocol')=='durable_scoped_send_intent_v2'
        and isinstance(run.get('capture_run_id'),str)
        and re.fullmatch('[0-9a-f]{32}',run['capture_run_id']),
        'exact_scoped_capture_protocol_required')
    return digest({key:run.get(key) for key in ('capture_protocol','capture_run_id','profile_sha256',
        'scope_sha256','selection','bindings','target_registration_sha256')})

def _policy(scope,now):
    policy=scope.get('recovery_policy',{})
    _require(policy.get('approved') is True and isinstance(policy.get('approval_ref'),str)
        and policy['approval_ref'].strip() and not policy['approval_ref'].startswith('REPLACE'),
        'reviewed_recovery_policy_required')
    _require(type(policy.get('max_recoveries')) is int and 1<=policy['max_recoveries']<=3,
        'bounded_recovery_count_required')
    window=policy.get('runtime_window',{})
    try:
        first=datetime.fromisoformat(window['first_start_utc'].replace('Z','+00:00'))
        last=datetime.fromisoformat(window['absolute_stop_utc'].replace('Z','+00:00'))
        if first.tzinfo is None or last.tzinfo is None:raise ValueError()
        start,stop=first.timestamp(),last.timestamp()
    except (KeyError,TypeError,ValueError):raise ValueError('original_runtime_window_required') from None
    _require(stop==start+14400 and start<=now<stop-420,'original_runtime_deadline_exhausted_or_changed')
    return policy,stop

def recovery_plan(db,profile,scope,selection,run,raw_run,*,targets,now=None):
    """Derive exact remaining conversations from canonical cases and a read-only DB."""
    from tools.quick_assessment import validate_selection,selection_binding
    now=time.time() if now is None else now
    policy,stop=_policy(scope,now)
    cases=validate_selection(selection,profile,scope)
    _require(run.get('status')=='incomplete' and json.loads(raw_run)==run
        and run.get('capture_protocol')=='durable_scoped_send_intent_v2'
        and run.get('profile_sha256')==profile['profile_sha256']
        and run.get('scope_sha256')==digest(scope)
        and run.get('selection')==selection_binding(selection,scope)
        and run.get('run_sha256')==digest({k:v for k,v in run.items() if k!='run_sha256'}),
        'incomplete_bound_durable_capture_required')
    run_binding=capture_run_binding(run)
    _require(all(_has(db,n) for n in ('quick_batch_capture_keys','quick_batch_send_intents','quick_batch_capture_scopes')),
        'durable_capture_and_send_intent_tables_required')
    authorization=scope['authorization_id'];pack=selection['pack_sha256']
    _require(set(targets)==set(scope['identities']),'exact_registered_target_set_required')
    _require(run.get('target_registration_sha256')=={role:digest(asdict(t)) for role,t in targets.items()},
        'complete_original_target_registration_required')
    for role,t in targets.items():
        _require(t.user_arn==scope['identities'][role]['user_arn'] and t.endpoint_url==scope['identities'][role]['endpoint']
            and t.region==scope['region'] and t.expires_at>now and t.expires_at>=datetime.fromisoformat(scope['expires_at'].replace('Z','+00:00')).timestamp()
            and all(run['bindings'][role].get(k)==value for k,value in {
                'user_arn_sha256':_sha(t.user_arn),'account_id_sha256':_sha(t.account_id),
                'agent_id_sha256':_sha(t.agent_id)}.items()),'original_registered_target_bindings_required')
    epoch_binding=_sha(json.dumps(sorted((t.pilot_id,t.endpoint_url,t.user_arn,t.agent_id,t.log_group,t.max_calls,
        t.max_concurrent_conversations) for t in targets.values())))
    capture=db.execute("SELECT binding FROM quick_selected_phases WHERE authorization=? AND selection_sha256=? AND phase='capture'",
        (authorization,pack)).fetchone()
    _require(capture==(run_binding,),'original_capture_claim_required')
    auth=db.execute('SELECT calls,cap,halted FROM quick_batch_authorizations WHERE id=?',(authorization,)).fetchone()
    _require(auth is not None and auth[1]==scope['max_calls'] and auth[2] in (0,1),'same_epoch_authorization_required')
    _require(db.execute('SELECT binding FROM quick_batch_authorizations WHERE id=?',(authorization,)).fetchone()==(epoch_binding,),
        'registered_caps_identity_or_concurrency_changed')
    _require(not db.execute('SELECT 1 FROM leases').fetchone() and not db.execute('SELECT 1 FROM quick_conversation_leases').fetchone(),
        'all_workers_stopped_no_leases_required')
    _require(not any(c.get('attempt')!=1 for c in run['captures']),'initial_attempt_recovery_only')
    by_id={c['case_id']:c for c in run['captures']}
    _require(len(by_id)==len(run['captures']),'duplicate_saved_capture')
    expected={c['case_id']+':1:'+str(i):(v,c) for c in cases for i,v in enumerate(c['conversations'])}
    _require(set(by_id)<=set(expected),'capture_outside_original_selection')
    previous=0
    if _has(db,'quick_recovery_actions'):
        previous=db.execute("SELECT COUNT(*) FROM quick_recovery_actions WHERE kind='resume' AND authorization=? AND selection_sha256=?",
            (authorization,pack)).fetchone()[0]
    _require(previous<policy['max_recoveries'],'recovery_count_exhausted')
    # A missing/corrupt key cannot turn a current run's already-observed owner
    # into apparently absent work. Check scoped owners before selection filtering.
    scoped=db.execute('''SELECT r.owner,c.authorization,k.capture_id FROM quick_batch_capture_scopes r
        LEFT JOIN quick_batch_cases c ON c.owner=r.owner
        LEFT JOIN quick_batch_capture_keys k ON k.owner=r.owner WHERE r.capture_binding=?''',(run_binding,)).fetchall()
    _require(all(auth==authorization and key in expected for _,auth,key in scoped),
        'current_capture_owner_key_required')
    for table in ('quick_batch_capture_keys','quick_batch_capture_scopes','quick_batch_send_intents','quick_batch_observations','quick_batch_submissions'):
        if _has(db,table):
            _require(not db.execute('SELECT 1 FROM '+table+' p LEFT JOIN quick_batch_cases c ON c.owner=p.owner WHERE c.owner IS NULL LIMIT 1').fetchone(),
                'orphan_durable_capture_owner_requires_adjudication')
    claimed_runs={r[0] for r in db.execute("SELECT binding FROM quick_selected_phases WHERE authorization=? AND phase='capture'",(authorization,))}
    owner_rows=db.execute('''SELECT c.owner,c.pilot,c.identity,c.conversation,k.capture_id,k.role,k.prompts_sha256,k.verified_bindings_json,r.capture_binding
        FROM quick_batch_cases c LEFT JOIN quick_batch_capture_keys k ON k.owner=c.owner
        LEFT JOIN quick_batch_capture_scopes r ON r.owner=c.owner
        WHERE c.authorization=?''',(authorization,)).fetchall()
    owners={}
    for owner,pilot,identity,conversation,key,role,prompts_sha,verified,owner_binding in owner_rows:
        # Readiness, probes and full runs may reuse canonical case IDs within
        # one epoch. Only owners bound to this exact capture can be recovered.
        _require(key is not None,'current_epoch_durable_case_key_required')
        if key in expected:
            _require(owner_binding in claimed_runs,'missing_or_unclaimed_capture_owner_binding')
            if owner_binding!=run_binding:continue
            v,_=expected[key]
            target=targets[v['role']]
            _require(pilot==target.pilot_id and identity==_sha(target.account_id+target.region+target.user_arn),
                'durable_owner_pilot_or_identity_changed')
            _require(role==v['role'] and prompts_sha==_sha(json.dumps(v['turns'])),
                'durable_case_key_changed')
            owners.setdefault(key,[]).append((owner,pilot,identity,conversation,json.loads(verified)))
    remaining=[];completed=[];reconciled=[];dispositions=[];blocked=[];calls=Counter()
    for key,(v,case) in expected.items():
        saved=by_id.get(key)
        if not saved or saved.get('status')!='observed':
            recovered=[]
            for owner,pilot,identity,conversation,verified in owners.get(key,[]):
                if set(verified)!={str(i) for i in range(len(v['turns']))} or not _has(db,'quick_batch_observations'):
                    continue
                rows=db.execute('SELECT turn,observation_json FROM quick_batch_observations WHERE owner=? ORDER BY turn',(owner,)).fetchall()
                states=db.execute('SELECT turn,status,answer_hash FROM quick_batch_submissions WHERE owner=? ORDER BY turn',(owner,)).fetchall()
                if [r[0] for r in rows]!=list(range(len(v['turns']))) or len(states)!=len(rows):continue
                turns=[json.loads(r[1]) for r in rows]
                if all(state[:2]==(i,'verified') and state[2]==_sha(turn['reply'])
                       and bound_turn(turn,prompt,v['role'],run['bindings'])
                       for i,(state,turn,prompt) in enumerate(zip(states,turns,v['turns']))):
                    recovered.append({'case_id':key,'role':v['role'],'attempt':1,'status':'observed','turns':turns})
            _require(len(recovered)<=1,'ambiguous_durable_observation')
            if recovered:
                saved=recovered[0];reconciled.append(saved)
        if saved and saved.get('status')=='observed':
            _require(saved.get('role')==v['role'] and len(saved.get('turns',[]))==len(v['turns'])
                and all(bound_turn(t,p,v['role'],run['bindings']) for t,p in zip(saved['turns'],v['turns'])),
                'observed_capture_binding_invalid')
            verified_owners=[owner for owner in owners.get(key,[]) if set(owner[4])=={str(i) for i in range(len(v['turns']))}
                and all(all(owner[4][str(i)].get(field)==turn['transport'].get(field)
                    for field in ('conversation_sha256','message_sha256','system_message_sha256','response_sha256'))
                    for i,turn in enumerate(saved['turns']))]
            _require(len(verified_owners)==1,'observed_capture_durable_native_binding_required')
            completed.append(key);continue
        if saved and (saved.get('turns') or saved.get('attempted_turns',0)!=0):
            blocked.append({'capture_id':key,'reason':'possibly_sent_or_partial_conversation'});continue
        safe=True
        for owner,pilot,identity,conversation,verified in owners.get(key,[]):
            submissions=db.execute('SELECT turn,status,answer_hash,captured_hash,prompt_hash FROM quick_batch_submissions WHERE owner=? ORDER BY turn',(owner,)).fetchall()
            intents=db.execute('SELECT turn FROM quick_batch_send_intents WHERE owner=?',(owner,)).fetchall()
            if intents or verified or conversation is not None or any(x[2] is not None or x[3] is not None for x in submissions):
                safe=False;break
            for turn,status,answer,captured,prompt_sha in submissions:
                if turn!=0 or prompt_sha!=_sha(v['turns'][0]) or status not in {'unknown','submitting','reviewed_not_submitted'}:
                    safe=False;break
                if status!='reviewed_not_submitted':
                    dispositions.append({'owner':owner,'turn':0,'pilot':pilot,'capture_id':key,'status_before':status,
                        'basis':'durable_case_key_and_absent_pre_send_intent','charged_slot_retained':True})
        if not safe:
            blocked.append({'capture_id':key,'reason':'possible_send_or_provider_evidence'});continue
        remaining.append({'capture_id':key,'role':v['role'],'turns':v['turns'],'canonical_case_sha256':digest(case)})
        calls[v['role']]+=len(v['turns'])
    unknown=db.execute('''SELECT s.owner,s.turn FROM quick_batch_submissions s JOIN quick_batch_cases c ON c.owner=s.owner
        WHERE c.authorization=? AND s.status IN ('unknown','submitting','timed_out','mismatch')''',(authorization,)).fetchall()
    reviewed={(v['owner'],v['turn']) for v in dispositions}
    for owner,turn in unknown:
        if (owner,turn) not in reviewed:blocked.append({'owner':owner,'turn':turn,'reason':'unresolved_owner_not_proven_unsent'})
    _require(not blocked,'possibly_sent_work_requires_separate_adjudication')
    _require(remaining,'no_remaining_conversations')
    budget=checked_budget(db,required=True,expected=scope['accounting'],additional=sum(calls.values()))
    _require(auth[0]+sum(calls.values())<=auth[1],'epoch_budget_insufficient')
    pilots=db.execute('''SELECT p.id,p.calls,p.halted,p.inflight FROM pilots p JOIN quick_batch_authorization_pilots a ON a.pilot=p.id
        WHERE a.authorization=? ORDER BY p.id''',(authorization,)).fetchall()
    _require(len(pilots)==len(scope['identities']),'exact_current_pilot_set_required')
    _require({p[0] for p in pilots}=={t.pilot_id for t in targets.values()},'current_pilot_membership_changed')
    pilot_calls={p[0]:p[1] for p in pilots}
    _require(all(pilot_calls[t.pilot_id]+calls[role]<=t.max_calls for role,t in targets.items()),
        'preserved_role_budget_insufficient')
    pending=Counter(x['pilot'] for x in dispositions)
    _require(all(row[3]==pending[row[0]] for row in pilots),'inflight_owner_count_mismatch')
    value={'schema':SCHEMA,'approved':False,'authorization_id':authorization,'selection_sha256':pack,
        'capture_run_binding':run_binding,'capture_run_id':run['capture_run_id'],
        'profile_sha256':profile['profile_sha256'],'scope_sha256':digest(scope),'source_run_file_sha256':hashlib.sha256(raw_run).hexdigest(),
        'before_ledger_sha256':ledger_digest(db),'global_before':budget['charged_or_reserved'],'epoch_before':auth[0],
        'epoch_cap':auth[1],'pilots_before':[list(x) for x in pilots],'runtime_absolute_stop_epoch':stop,
        'recovery_number':previous+1,'completed_capture_ids':completed,'remaining':remaining,
        'reconciled_captures':reconciled,
        'expected_new_calls':sum(calls.values()),'calls_by_role':dict(calls),'no_send_dispositions':dispositions,
        'operator_approval_ref':None,'stopped_runtime_evidence_ref':None,'refund_calls':0}
    value['plan_sha256']=digest({k:v for k,v in value.items() if k not in {'approved','operator_approval_ref','stopped_runtime_evidence_ref'}})
    return value

def apply_recovery(path,profile,scope,selection,run,raw_run,plan,*,targets,source_snapshot,now=None):
    """One audited transaction: dispositions, scoped halt release, and new claim."""
    _require(isinstance(path,Path) and path.is_absolute(),'absolute_working_ledger_required')
    _require(isinstance(source_snapshot,Path) and source_snapshot.is_absolute() and source_snapshot.is_file()
        and source_snapshot.resolve()!=path.resolve(),'separate_immutable_source_ledger_snapshot_required')
    source_sha=hashlib.sha256(source_snapshot.read_bytes()).hexdigest()
    original=sqlite3.connect(source_snapshot.resolve().as_uri()+'?mode=ro',uri=True)
    try:_require(ledger_digest(original)==plan.get('before_ledger_sha256'),'exact_immutable_source_snapshot_required')
    finally:original.close()
    _require(plan.get('approved') is True and all(isinstance(plan.get(k),str) and plan[k].strip()
        and not plan[k].startswith('REPLACE') for k in ['operator_approval_ref','stopped_runtime_evidence_ref']),
        'explicit_operator_recovery_review_required')
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            actual=recovery_plan(db,profile,scope,selection,run,raw_run,targets=targets,now=now)
            ignored={'approved','operator_approval_ref','stopped_runtime_evidence_ref'}
            _require({k:v for k,v in actual.items() if k not in ignored}=={k:v for k,v in plan.items() if k not in ignored},
                'recovery_plan_or_working_ledger_changed')
            db.execute(ACTION_DDL)
            auth=scope['authorization_id'];pack=selection['pack_sha256']
            action_id='resume-'+plan['plan_sha256']
            _require(not db.execute('SELECT 1 FROM quick_recovery_actions WHERE id=?',(action_id,)).fetchone(),
                'recovery_already_claimed_no_replay')
            for row in plan['no_send_dispositions']:
                changed=db.execute("UPDATE quick_batch_submissions SET status='reviewed_not_submitted',reason=? WHERE owner=? AND turn=? AND status=? AND answer_hash IS NULL AND captured_hash IS NULL",
                    ('reviewed_no_send:'+plan['plan_sha256'],row['owner'],row['turn'],row['status_before'])).rowcount
                _require(changed==1,'owner_disposition_changed')
                _require(db.execute('UPDATE pilots SET inflight=inflight-1 WHERE id=? AND inflight>0',(row['pilot'],)).rowcount==1,
                    'scoped_inflight_release_failed')
            # No generic reset: only the exact authorization and its prechecked
            # current pilot set may resume; every counter/cap/binding is retained.
            for pilot,calls,halted,inflight in plan['pilots_before']:
                _require(db.execute('UPDATE pilots SET halted=0 WHERE id=? AND calls=? AND inflight=0 AND halted=?',
                    (pilot,calls,halted)).rowcount==1,'scoped_pilot_state_changed')
            _require(db.execute('UPDATE quick_batch_authorizations SET halted=0 WHERE id=? AND calls=? AND cap=?',
                (auth,plan['epoch_before'],plan['epoch_cap'])).rowcount==1,'scoped_epoch_changed')
            db.execute('INSERT INTO quick_recovery_actions VALUES (?,?,?,?,?,?,?,?,?)',
                (action_id,'resume',auth,pack,plan['source_run_file_sha256'],plan['plan_sha256'],plan['before_ledger_sha256'],
                 json.dumps({'dispositions':plan['no_send_dispositions'],'completed_capture_ids':plan['completed_capture_ids'],
                    'remaining_capture_ids':[x['capture_id'] for x in plan['remaining']],
                    'scope_sha256':digest(scope),
                    'source_ledger_snapshot_file_sha256':source_sha,
                    'operator_approval_ref':plan['operator_approval_ref'],'stopped_runtime_evidence_ref':plan['stopped_runtime_evidence_ref']},sort_keys=True),
                 datetime.now(timezone.utc).isoformat()))
            after=checked_budget(db,required=True,expected=scope['accounting'])
            _require(after['charged_or_reserved']==plan['global_before'],'recovery_must_not_refund_or_spend')
            _require(hashlib.sha256(source_snapshot.read_bytes()).hexdigest()==source_sha,'source_snapshot_changed_during_recovery')
            return {'action_id':action_id,'plan_sha256':plan['plan_sha256'],'ledger_sha256':ledger_digest(db),
                'charged_or_reserved':after['charged_or_reserved'],'new_target_calls':0,'refund_calls':0}
    finally:db.close()


def adjudicate_legacy_no_send(path,profile,scope,selection,raw_run,worker_bytes,native_bytes,stopped_bytes,review,*,source_snapshot):
    """Close proven pre-send reservations on a NEW copy, retaining every charge.

    Only the explicitly qualified legacy worker is supported. Missing logs,
    zero attempted counters or a missing intent table alone never suffice.
    Old authorization/pilot halts and phase claims stay closed.
    """
    from tools.quick_assessment import validate_selection,selection_binding
    sha=lambda b:hashlib.sha256(b).hexdigest()
    _require(source_snapshot.is_absolute() and source_snapshot.is_file() and source_snapshot.resolve()!=path.resolve(),
        'separate_immutable_legacy_snapshot_required')
    source_sha=sha(source_snapshot.read_bytes())
    original=sqlite3.connect(source_snapshot.resolve().as_uri()+'?mode=ro',uri=True)
    try:_require(ledger_digest(original)==review.get('before_ledger_sha256'),'exact_legacy_source_snapshot_required')
    finally:original.close()
    _require(path.is_absolute() and review.get('approved') is True and all(isinstance(review.get(k),str)
        and review[k].strip() and not review[k].startswith('REPLACE') for k in ('review_ref','new_work_authorization_ref')),
        'reviewed_legacy_disposition_required')
    _require(sha(worker_bytes)==LEGACY_NO_SEND_WORKER_SHA256
        and review.get('qualified_legacy_worker_sha256')==LEGACY_NO_SEND_WORKER_SHA256,
        'qualified_legacy_pre_send_control_flow_required')
    _require(all(review.get(k)==sha(b) for k,b in [('source_run_file_sha256',raw_run),
        ('settled_native_file_sha256',native_bytes),('stopped_runtime_file_sha256',stopped_bytes)]),
        'exact_legacy_artifacts_required')
    stopped=json.loads(stopped_bytes);native=json.loads(native_bytes);run=json.loads(raw_run)
    _require(stopped.get('status')=='verified_closed' and stopped.get('worker')=='stopped_standard'
        and stopped.get('temporary_users_remaining')==0 and stopped.get('actual_runtime_role_closed_trust_and_policies_verified') is True,
        'closed_runtime_before_legacy_disposition_required')
    cases=validate_selection(selection,profile,scope)
    _require(run.get('status')=='incomplete' and run.get('capture_protocol') is None
        and run.get('profile_sha256')==profile['profile_sha256'] and run.get('scope_sha256')==digest(scope)
        and run.get('selection')==selection_binding(selection,scope)
        and run.get('run_sha256')==digest({k:v for k,v in run.items() if k!='run_sha256'}),
        'exact_legacy_run_binding_required')
    canonical={c['case_id']+':1:'+str(i):v for c in cases for i,v in enumerate(c['conversations'])}
    captures={c['case_id']:c for c in run['captures']}
    _require(len(captures)==len(run['captures']) and set(captures)<=set(canonical),'exact_legacy_capture_membership_required')
    rows=native.get('records',[])
    _require(isinstance(rows,list) and rows and native.get('target_calls')==0,'settled_native_readback_required')
    # The sealed CloudShell export retains CloudWatch event envelopes. Bind
    # the ORIGINAL bytes above, then validate and unwrap their body locally.
    if all(isinstance(r,dict) and set(r)=={'event_id','body'} for r in rows):
        _require(all(isinstance(r['event_id'],str) and r['event_id'] and isinstance(r['body'],dict) for r in rows)
            and len({r['event_id'] for r in rows})==len(rows),'unique_native_event_envelopes_required')
        rows=[r['body'] for r in rows]
    _require(all(isinstance(r,dict) and r.get('logType')=='CHAT_LOGS' for r in rows),'native_chat_log_records_required')
    observed=[turn for capture in captures.values() if capture.get('status')=='observed' for turn in capture['turns']]
    _require(len(rows)==len(observed) and len({_sha(r['user_message_id']) for r in rows})==len(rows)
        and all(any(_sha(r['user_message_id'])==t['transport']['message_sha256']
            and _sha(r['conversation_id'])==t['transport']['conversation_sha256']
            and _sha(r['user_arn'])==t['transport']['user_arn_sha256']
            and _sha(r['accountId'])==t['transport']['account_id_sha256']
            and _sha(r['agent_id'])==t['transport']['agent_id_sha256']
            and r['user_message']==t['prompt'] and r['system_text_message']==t['reply'] for r in rows) for t in observed),
        'all_observed_native_records_must_rejoin_exactly')
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            _require(ledger_digest(db)==review.get('before_ledger_sha256'),'exact_preserved_legacy_ledger_required')
            before=checked_budget(db,required=True,expected=scope['accounting'])
            _require(not db.execute('SELECT 1 FROM leases').fetchone() and not db.execute('SELECT 1 FROM quick_conversation_leases').fetchone(),
                'all_legacy_workers_stopped_required')
            pending=db.execute('''SELECT c.owner,c.pilot,c.identity,c.conversation,s.turn,s.prompt_hash,s.status,s.answer_hash,s.captured_hash,s.reason
                FROM quick_batch_cases c JOIN quick_batch_submissions s ON s.owner=c.owner
                WHERE c.authorization=? AND s.status IN ('unknown','submitting','timed_out','mismatch')''',(scope['authorization_id'],)).fetchall()
            _require(pending,'legacy_pending_reservations_required');dispositions=[]
            for owner,pilot,identity,conversation,turn,prompt_sha,status,answer,captured,reason in pending:
                _require(status=='unknown' and turn==0 and conversation is None and answer is None and captured is None,
                    'possible_legacy_send_requires_separate_adjudication')
                _require(not _has(db,'quick_batch_send_intents') or not db.execute(
                    'SELECT 1 FROM quick_batch_send_intents WHERE owner=?',(owner,)).fetchone(),
                    'recorded_legacy_send_intent_cannot_be_adjudicated_unsent')
                candidates=[]
                for key,v in canonical.items():
                    role=v['role'];identity_spec=scope['identities'][role]
                    account=re.search(r':([0-9]{12}):user/',identity_spec['user_arn']).group(1)
                    if identity==_sha(account+scope['region']+identity_spec['user_arn']) and prompt_sha==_sha(v['turns'][0]):
                        candidates.append((key,role))
                _require(len(candidates)==1,'unique_legacy_owner_to_canonical_conversation_required')
                key,role=candidates[0];saved=captures.get(key,{})
                _require(saved.get('status')=='could_not_run' and saved.get('role')==role and saved.get('attempt')==1
                    and saved.get('attempted_turns')==0 and not saved.get('turns') and saved.get('reason')==reason,
                    'qualified_legacy_zero_send_capture_required')
                init=saved.get('initialization',[])
                ready_halt=reason=='pilot_halted_operator_review_required' and init==[{'attempt':1,'outcome':'ready'}]
                failed_init=(reason=='browser_harness_unavailable' and 1<=len(init)<=2 and all(
                    i.get('attempt')==n and i.get('outcome')=='sdk_unavailable' and i.get('phase')=='navigation'
                    and i.get('page_crashed') is False and i.get('error_code') in {'net::ERR_CONNECTION_RESET','net::ERR_CONNECTION_CLOSED'}
                    for n,i in enumerate(init,1)))
                _require(ready_halt or failed_init,'qualified_legacy_before_send_failure_required')
                _require(not any(_sha(r['user_arn'])==run['bindings'][role]['user_arn_sha256'] and _sha(r['user_message'])==prompt_sha for r in rows),
                    'legacy_native_submission_observed_no_replay')
                dispositions.append(dict(owner=owner,turn=0,pilot=pilot,capture_id=key,charged_slot_retained=True))
            _require(review.get('owners')==sorted(x['owner'] for x in dispositions),'exact_reviewed_legacy_owner_set_required')
            counts=Counter(x['pilot'] for x in dispositions)
            _require(all(db.execute('SELECT inflight,halted FROM pilots WHERE id=?',(pilot,)).fetchone()==(count,1)
                for pilot,count in counts.items()),'exact_halted_legacy_inflight_required')
            db.execute(ACTION_DDL);review_sha=digest(review)
            for item in dispositions:
                db.execute("UPDATE quick_batch_submissions SET status='reviewed_not_submitted',reason=? WHERE owner=? AND turn=0",
                    ('reviewed_legacy_no_send:'+review_sha,item['owner']))
            for pilot,count in counts.items():db.execute('UPDATE pilots SET inflight=inflight-? WHERE id=?',(count,pilot))
            db.execute('INSERT INTO quick_recovery_actions VALUES (?,?,?,?,?,?,?,?,?)',
                ('legacy-'+review_sha,'legacy_no_send',scope['authorization_id'],selection['pack_sha256'],sha(raw_run),review_sha,
                    review['before_ledger_sha256'],json.dumps(dict(review=review,dispositions=dispositions),sort_keys=True),datetime.now(timezone.utc).isoformat()))
            after=checked_budget(db,required=True,expected=scope['accounting'])
            _require(after==before,'legacy_disposition_must_retain_all_budget_fields')
            _require(sha(source_snapshot.read_bytes())==source_sha,'legacy_source_snapshot_changed')
            return dict(status='legacy_no_send_dispositions_committed',dispositions=dispositions,
                charged_or_reserved=after['charged_or_reserved'],ledger_sha256=ledger_digest(db),target_calls=0,refund_calls=0,old_halts_released=False)
    finally:db.close()
