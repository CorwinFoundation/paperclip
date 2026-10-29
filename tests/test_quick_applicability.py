"""Applicability covers every canonical case and cannot hide source gaps."""
import unittest
from evals.amazon_quick_assessment import load_profile,digest
from harness.amazon_quick import _sha
from tools.quick_applicability import prepare,baseline_configuration
from tools.quick_assessment import validate_selection

class ApplicabilityTests(unittest.TestCase):
    def test_default_has_all600_and795_calls_with_no_implicit_exclusions(self):
        p=load_profile('hr_finance','1.3');plan=prepare(p,'a'*40)
        self.assertEqual(plan['applicable_cases'],600);self.assertEqual(plan['selection']['initial_target_calls'],795)
        self.assertEqual(len(plan['inventory']),600);self.assertEqual(plan['configuration_not_applicable_cases'],0)
        self.assertEqual(len(validate_selection(plan['selection'],p)),600)

    def test_reviewed_source_only_contract_excludes_only40_source_free_utility_cases(self):
        p=load_profile('hr_finance','1.3')
        export={'Agent':dict(AgentId='SCRIPTED',Arn='arn:aws:quicksight:us-east-1:111122223333:agent/SCRIPTED',
            CustomPromptInterface=dict(CustomInstructions=p['fixture']['agent_instructions']),AgentStatus='ACTIVE',
            AgentLifecycle='PUBLISHED',ActionConnectors=[])}
        contract=dict(approved=True,assessment_subject='sandbox_baseline',agent_instructions_sha256=_sha(p['fixture']['agent_instructions']),
            native_configuration_sha256=digest(baseline_configuration(export)),
            review_ref='SCRIPTED://instructions-scope-review',policy='only_connected_sources_no_general_knowledge')
        plan=prepare(p,'a'*40,source_only_contract=contract,baseline_export=export)
        self.assertEqual(plan['applicable_cases'],560);self.assertEqual(plan['selection']['initial_target_calls'],755)
        self.assertEqual(plan['source_evidence_exclusions'],0)
        self.assertEqual(sum(r['applicable'] and 'exact_typed_source_identity' in r['evidence_requirements'] for r in plan['inventory']),100)
        self.assertEqual(sum(r['applicable'] and 'rubric_adequacy_review_abstain_if_unspecified' in r['evidence_requirements'] for r in plan['inventory']),12)
        self.assertEqual(len(validate_selection(plan['selection'],p)),560)
        for field,value in [('approved',False),('agent_instructions_sha256','f'*64),('native_configuration_sha256','f'*64)]:
            changed={**contract,field:value}
            with self.subTest(field=field),self.assertRaises(ValueError):prepare(p,'a'*40,source_only_contract=changed,baseline_export=export)

if __name__=='__main__':unittest.main()
