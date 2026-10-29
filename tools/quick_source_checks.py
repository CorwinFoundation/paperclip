"""Prepare canonical positive-source cases and audit saved evidence, offline.

Space/display-name citations never become file/version/chunk evidence here.
No source IDs are inferred from answer prose and no grader guard is bypassed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re

from evals.amazon_quick_assessment import bound_turn, digest, exposure_matches, load_profile, structured_row_denial
from harness.amazon_quick import _sha
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
                resource = scope['sources'][source]['resource_id']
                reviewed_type = scope['sources'][source].get('native_resource_type')
                accepted_types = {'document':{'document','file'},'dataset':{'dataset','data_set'}}.get(reviewed_type,set())
                typed_lineage = (bool(accepted_types) and (reviewed_type=='dataset')==source.endswith('.csv')
                    and not any(part in resource for part in (':space/',':knowledge-base/',':knowledge_base/')))
                exact = typed_lineage and any(isinstance(v,dict) and v.get('citedResourceId')==resource
                    and isinstance(v.get('citedResourceType'),str)
                    and v['citedResourceType'].casefold() in accepted_types for v in citations)
                kinds = Counter(str(v.get('citedResourceType','unknown')) for v in citations if isinstance(v,dict))
                chunk = (case['check']=='retrieved_instruction' and source==case['fixture_dependencies'][0]
                    and bound and exposure_matches(case,capture,source,profile,exposures))
                results.append({'case_id':case['case_id'],'attempt':attempt,'source_name':source,
                    'bound_provider_evidence':bool(bound),'configured_source_hash_matches':
                        scope['sources'][source].get('source_sha256')==_sha(profile['fixture']['sources'][source]),
                    'native_exact_source_id':bool(exact),'native_citation_types':dict(kinds),
                    'reviewed_native_resource_type':reviewed_type,'reviewed_file_or_dataset_lineage':typed_lineage,
                    'source_version_verified_by_tool':False,
                    'source_version_review_ref':scope['source_setup_ref'],
                    'attack_chunk_required':case['check']=='retrieved_instruction' and source==case['fixture_dependencies'][0],
                    'message_bound_chunk_attestation_present':bool(chunk),
                    'gap':'capture_unavailable_or_unbound' if not bound else 'reviewed_source_type_lineage_missing'
                        if not typed_lineage else None if exact else 'exact_native_source_identity_missing'})
    counts=Counter(v['gap'] or 'native_identity_observed' for v in results)
    return {'schema':'backbond-quick-source-evidence-audit/v1','profile_sha256':profile['profile_sha256'],
        'scope_sha256':digest(scope),'pack_sha256':selection['pack_sha256'],'source_run_sha256':run['run_sha256'],
        'results':results,'counts':dict(counts),'target_calls':0,'external_judge_calls':0,'backbond_score':'N/A',
        'limitation':'Read-only evidence inventory. Native identity is separate from indexed-version proof, attack-chunk exposure and backend ACL/RLS enforcement. This audit grants no pass or evidence exemption.'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['prepare','audit'])
    parser.add_argument('--variant',choices=['hr_finance','healthcare'],default='hr_finance')
    parser.add_argument('--revision')
    parser.add_argument('--selection',type=Path);parser.add_argument('--scope',type=Path);parser.add_argument('--run',type=Path)
    parser.add_argument('--exposure-evidence',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    profile=load_profile(args.variant,'1.3')
    result=(prepare(profile,args.revision) if args.operation=='prepare' else
        audit(profile,read_json(args.scope),read_json(args.selection,limit=16*1024*1024),
            read_json(args.run,limit=256*1024*1024),
            exposures=read_json(args.exposure_evidence) if args.exposure_evidence else []))
    with args.output.open('xb') as stream:stream.write((json.dumps(result,indent=2,ensure_ascii=False)+'\n').encode())
    print(json.dumps({'status':'offline_source_plan_prepared' if args.operation=='prepare' else 'saved_source_evidence_audited',
        'target_calls':0,'external_judge_calls':0,'backbond_score':'N/A'}))
    return 0


if __name__=='__main__':raise SystemExit(main())
