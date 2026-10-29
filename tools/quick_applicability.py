"""Prepare all 600 applicability decisions before a synthetic baseline run.

No evidence gap or failed answer can become not applicable. Configuration
exclusions require a reviewed source-only instruction contract. This plan does
not determine applicability for a customer whose configuration is unknown.
"""
from __future__ import annotations
from collections import Counter
from evals.amazon_quick_assessment import digest,load_profile
from tools.quick_source_checks import positive_sources
from harness.amazon_quick import _sha

def baseline_configuration(export):
    import re
    agent=export['Agent'];arn=agent['Arn']
    match=re.fullmatch(r'arn:aws:quicksight:([a-z0-9-]+):([0-9]{12}):agent/(.+)',arn)
    if not match or match[3]!=agent['AgentId']:raise ValueError('exact_native_baseline_agent_required')
    return dict(agent_instructions_sha256=_sha(agent['CustomPromptInterface']['CustomInstructions']),
        account_id_sha256=_sha(match[2]),agent_id_sha256=_sha(agent['AgentId']),region=match[1],
        agent_status=agent['AgentStatus'],agent_lifecycle=agent['AgentLifecycle'],actions_sha256=digest(agent['ActionConnectors']))

def prepare(profile,revision,*,source_only_contract=None,baseline_export=None):
    import re
    if profile!=load_profile(profile['variant'],'1.3') or not re.fullmatch(r'[0-9a-f]{40}',revision):
        raise ValueError('canonical_profile_and_revision_required')
    excluded=set()
    configuration=None
    if source_only_contract is not None:
        configuration=baseline_configuration(baseline_export or {})
        if (source_only_contract.get('approved') is not True
            or source_only_contract.get('assessment_subject')!='sandbox_baseline'
            or source_only_contract.get('agent_instructions_sha256')!=_sha(profile['fixture']['agent_instructions'])
            or configuration['agent_instructions_sha256']!=source_only_contract['agent_instructions_sha256']
            or source_only_contract.get('native_configuration_sha256')!=digest(configuration)
            or configuration['agent_status']!='ACTIVE' or configuration['agent_lifecycle']!='PUBLISHED'
            or configuration['actions_sha256']!=digest([])
            or not isinstance(source_only_contract.get('review_ref'),str)
            or not source_only_contract['review_ref'].strip() or source_only_contract['review_ref'].startswith('REPLACE')
            or source_only_contract.get('policy')!='only_connected_sources_no_general_knowledge'):
            raise ValueError('reviewed_exact_source_only_configuration_contract_required')
        # Frozen canonical source-free utility controls. This exclusion is
        # based on configuration before responses, never on an observed fail.
        excluded={c['case_id'] for c in profile['cases']
            if c['family']=='permitted_utility' and not c['fixture_dependencies']}
        if len(excluded)!=40:raise ValueError('reviewed_source_free_utility_set_changed')
    inventory=[];entries=[];calls=Counter();families=Counter()
    for ordinal,case in enumerate(profile['cases'],1):
        applicable=case['case_id'] not in excluded
        requirements=['native_provider_response','all_material_turns','semantic_review_if_not_deterministic']
        if positive_sources(profile,case):requirements+=['exact_typed_source_identity','installed_source_version_review']
        if case['check']=='retrieved_instruction':requirements+=['provider_message_bound_attack_snippet','clean_control']
        if case['check']=='fairness_pair':requirements+=['rubric_adequacy_review_abstain_if_unspecified']
        inventory.append(dict(ordinal=ordinal,case_id=case['case_id'],canonical_case_sha256=digest(case),
            family=case['family'],applicable=applicable,disposition='execute' if applicable else 'configuration_not_applicable',
            reason='canonical_case_in_scope' if applicable else 'source_free_general_utility_conflicts_with_reviewed_source_only_instructions',
            evidence_requirements=requirements,evidence_gap_is_not_exclusion=True))
        if not applicable:continue
        plan=Counter()
        for v in case['conversations']:plan[v['role']]+=len(v['turns'])
        entries.append(dict(ordinal_in_full_profile=ordinal,case_id=case['case_id'],case_sha256=digest(case),
            family=case['family'],conversations=len(case['conversations']),initial_target_calls=sum(plan.values()),
            initial_calls_by_role=dict(plan),case=case))
        calls.update(plan);families[case['family']]+=1
    pack=dict(schema='backbond-quick-selected-case-pack/v1',pack_id='amazon-quick-v13-all-applicable-'+profile['variant'],
        source_revision=revision,variant=profile['variant'],profile_version='1.3',full_profile_id=profile['profile_id'],
        full_profile_sha256=profile['profile_sha256'],fixture_sha256=profile['fixture_sha256'],
        cases=entries,case_ids=[c['case_id'] for c in entries],case_count=len(entries),
        conversation_count=sum(c['conversations'] for c in entries),initial_target_calls=sum(calls.values()),
        initial_calls_by_role=dict(calls),family_counts=dict(families))
    pack['source_qualification_required']=True
    if configuration is not None:pack['configuration_applicability']=configuration
    pack['pack_sha256']=digest(pack)
    result=dict(schema='backbond-quick-applicability-plan/v1',profile_sha256=profile['profile_sha256'],
        source_only_configuration_contract=source_only_contract,inventory=inventory,selection=pack,
        full_cases=600,applicable_cases=len(entries),configuration_not_applicable_cases=len(excluded),
        source_evidence_exclusions=0,execution_approved=False,backbond_score='N/A')
    result['plan_sha256']=digest(result)
    return result
