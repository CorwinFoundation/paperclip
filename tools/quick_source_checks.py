"""Prepare canonical positive-source cases and audit saved evidence, offline.

Space/display-name citations never become file/version/chunk evidence here.
No source IDs are inferred from answer prose and no grader guard is bypassed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re

from evals.amazon_quick_assessment import bound_turn, digest, exposure_matches, load_profile, structured_row_denial
from harness.amazon_quick import _sha
from evals.quick_source_evidence import source_binding_valid,exact_native_source,indexed_version_valid,source_observed
from tools.internal_agent_pilot import read_json, save_run
from tools.quick_assessment import selection_binding, selected_cases, validate_selection


def positive_sources(profile, case):
    return [source for source in case['fixture_dependencies']
        if all(c['role'] in profile['fixture']['source_readers'][source] for c in case['conversations'])
        and not structured_row_denial(case, source, profile)]


def prepare(profile, revision):
    if profile != load_profile(profile['variant'], '1.3') or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('canonical_v13_and_reviewed_revision_required')
    entries, calls, families = [], Counter(), Counter()
    for ordinal, case in enumerate(profile['cases'], 1):
        if not positive_sources(profile, case):
            continue
        plan = Counter()
        for c in case['conversations']:
            plan[c['role']] += len(c['turns'])
        entries.append({'ordinal_in_full_profile':ordinal, 'case_id':case['case_id'], 'case_sha256':digest(case),
            'family':case['family'], 'conversations':len(case['conversations']), 'initial_target_calls':sum(plan.values()),
            'initial_calls_by_role':dict(plan), 'case':case,
            'applicability_gates':['fresh_index_and_version_review','exact_native_source_identity',
                                   'message_bound_attack_chunk_for_indirect_cases']})
        calls.update(plan);families[case['family']] += 1
    result = {'schema':'backbond-quick-selected-case-pack/v1', 'pack_id':'amazon-quick-v13-positive-source-'+profile['variant'],
        'source_revision':revision, 'variant':profile['variant'], 'profile_version':'1.3',
        'full_profile_id':profile['profile_id'], 'full_profile_sha256':profile['profile_sha256'],
        'fixture_sha256':profile['fixture_sha256'], 'preparation_only':True, 'execution_approved':False,
        'not_a_scan_result':True, 'selection_method':'Every canonical case requiring positive source identity; trusted denied-row exemptions excluded.',
        'cases':entries, 'case_ids':[v['case_id'] for v in entries], 'case_count':len(entries),
        'conversation_count':sum(v['conversations'] for v in entries), 'initial_target_calls':sum(calls.values()),
        'initial_calls_by_role':dict(calls), 'family_counts':dict(families), 'backbond_score':'N/A'}
    result['pack_sha256'] = digest(result)
    validate_selection(result, profile)
    return result


def audit(profile, scope, selection, run, *, exposures=()):
    validate_selection(selection, profile, scope)
    if (run.get('profile_sha256') != profile['profile_sha256'] or run.get('scope_sha256') != digest(scope)
            or run.get('run_sha256') != digest({k:v for k,v in run.items() if k!='run_sha256'})
            or run.get('selection') != selection_binding(selection, scope)
            or run.get('bindings',{}).get('sources') != scope['sources']):
        raise ValueError('saved_selected_profile_scope_binding_required')
    captures = run.get('captures', [])
    allowed = {c['case_id']+f':{a}:{i}' for c in selected_cases(profile,selection)
        for a in range(1,scope['selection']['max_attempts']+1) for i in range(len(c['conversations']))}
    if (len({c.get('case_id') for c in captures}) != len(captures)
            or any(c.get('case_id') not in allowed or type(c.get('attempt')) is not int
                or c['case_id'].rsplit(':',2)[1] != str(c['attempt']) for c in captures)):
        raise ValueError('captures_outside_selected_attempts')
    by_id = {c['case_id']:c for c in captures};results=[]
    for case in selected_cases(profile, selection):
        for source in positive_sources(profile,case):
            for attempt in sorted({c['attempt'] for c in captures} | {1}):
                index = 1 if 'clean-' in source and len(case['conversations'])>1 else 0
                expected = case['conversations'][index]
                capture = by_id.get(case['case_id']+f':{attempt}:{index}',{})
                turns = capture.get('turns',[])
                bound = (capture.get('status')=='observed' and capture.get('role')==expected['role']
                    and len(turns)==len(expected['turns'])
                    and all(bound_turn(t,p,expected['role'],run['bindings']) for t,p in zip(turns,expected['turns'])))
                native = turns[-1].get('transport',{}) if bound else {}
                citations = native.get('cited_resource',[])
                if not isinstance(citations,list):citations=[]
                reviewed_type = scope['sources'][source].get('native_resource_type')
                typed_lineage=source_binding_valid(source,scope['sources'][source],_sha(profile['fixture']['sources'][source]))
                exact=exact_native_source(source,scope['sources'][source],_sha(profile['fixture']['sources'][source]),native)
                identity_observed=bool(bound and source_observed(source,scope['sources'][source],_sha(profile['fixture']['sources'][source]),turns[-1]))
                version_reviewed=indexed_version_valid(scope['sources'][source],_sha(profile['fixture']['sources'][source]))
                kinds = Counter(str(v.get('citedResourceType','unknown')) for v in citations if isinstance(v,dict))
                chunk = (case['check']=='retrieved_instruction' and source==case['fixture_dependencies'][0]
                    and bound and exposure_matches(case,capture,source,profile,exposures,run['bindings']))
                results.append({'case_id':case['case_id'],'attempt':attempt,'source_name':source,
                    'bound_provider_evidence':bool(bound),'configured_source_hash_matches':
                        scope['sources'][source].get('source_sha256')==_sha(profile['fixture']['sources'][source]),
                    'native_exact_source_id':bool(exact),'native_citation_types':dict(kinds),
                    'reviewed_native_resource_type':reviewed_type,'reviewed_file_or_dataset_lineage':typed_lineage,
                    'provider_source_identity_observed':identity_observed,
                    'source_version_verified_by_tool':False,'indexed_version_review_present':version_reviewed,
                    'source_version_review_ref':scope['source_setup_ref'],
                    'attack_chunk_required':case['check']=='retrieved_instruction' and source==case['fixture_dependencies'][0],
                    'message_bound_chunk_attestation_present':bool(chunk),
                    'gap':'capture_unavailable_or_unbound' if not bound else 'reviewed_source_type_lineage_missing'
                        if not typed_lineage else None if identity_observed else 'exact_native_source_identity_missing'})
    counts=Counter(v['gap'] or 'native_identity_observed' for v in results)
    return {'schema':'backbond-quick-source-evidence-audit/v1','profile_sha256':profile['profile_sha256'],
        'scope_sha256':digest(scope),'pack_sha256':selection['pack_sha256'],'source_run_sha256':run['run_sha256'],
        'results':results,'counts':dict(counts),'target_calls':0,'external_judge_calls':0,'backbond_score':'N/A',
        'limitation':'Read-only evidence inventory. Native identity is separate from indexed-version proof, attack-chunk exposure and backend ACL/RLS enforcement. This audit grants no pass or evidence exemption.'}


def qualify(profile,probe_scope,probe_selection,probe_run,full_selection):
    """Derive an observability gate from actual bound probe observations.

    Covers each required source identity and poisoned span, plus all roles.
    This is collector qualification, not a claim that the agent passed cases.
    """
    validate_selection(full_selection,profile)
    validate_selection(probe_selection,profile,probe_scope)
    if probe_selection['initial_target_calls']>50 or probe_scope['selection']['max_attempts']!=1:
        raise ValueError('bounded_single_attempt_source_probes_required')
    audited=audit(profile,probe_scope,probe_selection,probe_run)
    start=datetime.fromisoformat(probe_scope['qualification_not_before'].replace('Z','+00:00')).timestamp()*1000
    end=datetime.fromisoformat(probe_scope['expires_at'].replace('Z','+00:00')).timestamp()*1000
    expected_conversations=probe_selection['conversation_count']
    if (probe_run.get('status')!='capture_complete' or len(probe_run.get('captures',[]))!=expected_conversations
        or any(c.get('attempt')!=1 or c.get('status')!='observed' or any(type(t.get('transport',{}).get('event_timestamp')) is not int
            or not start<=t['transport']['event_timestamp']<end for t in c.get('turns',[])) for c in probe_run.get('captures',[]))):
        raise ValueError('fresh_complete_single_attempt_source_probe_capture_required')
    full_cases=selected_cases(profile,full_selection)
    required={s for c in full_cases for s in positive_sources(profile,c)}
    poisoned={c['fixture_dependencies'][0] for c in full_cases if c['check']=='retrieved_instruction'}
    required_roles=set(profile['fixture']['roles']);observed=set();exposed=set();roles=set()
    for row in audited['results']:
        if row['bound_provider_evidence'] and row['provider_source_identity_observed'] and row['indexed_version_review_present']:
            observed.add(row['source_name'])
            if row['message_bound_chunk_attestation_present']:exposed.add(row['source_name'])
    by_id={c['case_id']:c for c in probe_run['captures']}
    conversations=[];messages=[];systems=[]
    for c in selected_cases(profile,probe_selection):
        for i,v in enumerate(c['conversations']):
            saved=by_id.get(c['case_id']+':1:'+str(i),{})
            if (saved.get('status')=='observed' and saved.get('role')==v['role']
                and len(saved.get('turns',[]))==len(v['turns'])
                and all(bound_turn(t,p,v['role'],probe_run['bindings']) for t,p in zip(saved['turns'],v['turns']))):
                roles.add(v['role']);conversations.append(saved['turns'][0]['transport']['conversation_sha256'])
                messages.extend(t['transport']['message_sha256'] for t in saved['turns'])
                systems.extend(t['transport'].get('system_message_sha256') for t in saved['turns'])
            else:raise ValueError('every_probe_requires_exact_provider_binding')
    if (len(set(conversations))!=len(conversations) or len(set(messages))!=len(messages)
        or len(set(systems))!=len(systems) or any(not isinstance(v,str) or not re.fullmatch(r'[0-9a-f]{64}',v) for v in systems)):
        raise ValueError('fresh_unique_probe_conversation_and_message_ids_required')
    result=dict(schema='backbond-quick-source-qualification/v1',profile_sha256=profile['profile_sha256'],
        full_selection_sha256=full_selection['pack_sha256'],probe_selection_sha256=probe_selection['pack_sha256'],
        probe_run_sha256=probe_run['run_sha256'],sources_binding_sha256=digest(probe_run['bindings']['sources']),
        required_sources=sorted(required),observed_sources=sorted(observed),required_attack_sources=sorted(poisoned),
        observed_attack_sources=sorted(exposed),roles=sorted(roles),
        status='source_observability_qualified' if required<=observed and poisoned<=exposed and roles==required_roles else 'source_observability_incomplete',
        target_calls=0,judge_calls=0,backbond_score='N/A')
    return result


def prepare_probe(profile,revision):
    """Canonical small coverage set; its capture is separately approved."""
    full=prepare(profile,revision);chosen=[];seen=set();roles=set()
    for entry in full['cases']:
        case=entry['case'];sources=set(positive_sources(profile,case));case_roles={v['role'] for v in case['conversations']}
        if sources-seen or case_roles-roles:
            chosen.append(deepcopy(entry));seen.update(sources);roles.update(case_roles)
    calls=Counter();families=Counter()
    for entry in chosen:calls.update(entry['initial_calls_by_role']);families[entry['family']]+=1
    value={**full,'pack_id':'amazon-quick-v13-source-probes-'+profile['variant'],'cases':chosen,
        'case_ids':[v['case_id'] for v in chosen],'case_count':len(chosen),
        'conversation_count':sum(v['conversations'] for v in chosen),'initial_target_calls':sum(calls.values()),
        'initial_calls_by_role':dict(calls),'family_counts':dict(families),'source_qualification_required':False}
    value.pop('pack_sha256');value['pack_sha256']=digest(value)
    if value['initial_target_calls']>50:raise ValueError('source_probe_budget_exceeded')
    validate_selection(value,profile)
    return value


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['prepare','prepare-probe','audit','qualify'])
    parser.add_argument('--variant',choices=['hr_finance','healthcare'],default='hr_finance')
    parser.add_argument('--revision')
    parser.add_argument('--selection',type=Path);parser.add_argument('--scope',type=Path);parser.add_argument('--run',type=Path)
    parser.add_argument('--full-selection',type=Path)
    parser.add_argument('--exposure-evidence',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    profile=load_profile(args.variant,'1.3')
    if args.operation in {'prepare','prepare-probe'}:
        result=(prepare if args.operation=='prepare' else prepare_probe)(profile,args.revision)
    else:
        scope=read_json(args.scope);selection=read_json(args.selection,limit=16*1024*1024);run=read_json(args.run,limit=256*1024*1024)
        if args.operation=='qualify':
            qualification=qualify(profile,scope,selection,run,read_json(args.full_selection,limit=16*1024*1024))
            result=dict(schema='backbond-quick-source-qualification-bundle/v1',scope=scope,selection=selection,run=run,
                qualification=qualification,source_run_file_sha256=__import__('hashlib').sha256(args.run.read_bytes()).hexdigest())
        else:result=audit(profile,scope,selection,run,exposures=read_json(args.exposure_evidence) if args.exposure_evidence else [])
    with args.output.open('xb') as stream:stream.write((json.dumps(result,indent=2,ensure_ascii=False)+'\n').encode())
    status=result['qualification']['status'] if args.operation=='qualify' else 'offline_source_plan_prepared' if args.operation in {'prepare','prepare-probe'} else 'saved_source_evidence_audited'
    print(json.dumps({'status':status,
        'target_calls':0,'external_judge_calls':0,'backbond_score':'N/A'}))
    return 2 if status=='source_observability_incomplete' else 0


if __name__=='__main__':raise SystemExit(main())
