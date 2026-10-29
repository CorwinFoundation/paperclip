"""Fabricated offline provider observations test evidence gates, not AWS quality."""
from copy import deepcopy
import unittest
from unittest.mock import Mock
from evals.amazon_quick_assessment import load_profile,grade
from evals.quick_source_evidence import SCHEMA,attack_exposure,safe_source_url,source_observed
from harness.amazon_quick import _sha
from tools.qualify_quick_grading import scripted_captures

class SourceProofTests(unittest.TestCase):
    def setUp(self):
        self.profile=load_profile('hr_finance','1.3')
        self.case=next(c for c in self.profile['cases'] if c['check']=='retrieved_instruction')
        self.bindings,self.captures=scripted_captures(self.profile,self.case,['Desk hours are 08:30-17:30, Sunday-Thursday.']*2)
        self.name=self.case['fixture_dependencies'][0];self.body=self.profile['fixture']['sources'][self.name]
        self.binding=self.bindings['sources'][self.name];self.turn=self.captures[0]['turns'][-1]
        self.binding['ui_capture_spec_sha256']=_sha('SCRIPTED reviewed UI config')
        self.record=dict(schema=SCHEMA,source='quick_provider_citation_dialog',truncated=False,
            capture_spec_sha256=self.binding['ui_capture_spec_sha256'],
            **{k:self.turn['transport'][k] for k in ('conversation_sha256','message_sha256','system_message_sha256',
                'response_sha256','prompt_sha256','user_arn_sha256','account_id_sha256','agent_id_sha256')},
            citations=[dict(resource_id=self.binding['resource_id'],resource_type='document',source_url=None,
                attributed_snippet=self.body,snippet_kind='response_citation')])
        self.turn['provider_source_observation']=self.record

    def exposed(self):return attack_exposure(self.case,self.turn,self.name,self.body,self.binding)

    def test_exact_provider_message_source_and_complete_attack_span_are_required(self):
        self.assertTrue(self.exposed())
        self.assertTrue(source_observed(self.name,self.binding,_sha(self.body),self.turn))
        for field in ['conversation_sha256','message_sha256','system_message_sha256','response_sha256',
                      'prompt_sha256','user_arn_sha256','account_id_sha256','agent_id_sha256','capture_spec_sha256']:
            original=self.record[field];self.record[field]='f'*64
            with self.subTest(field=field):self.assertFalse(self.exposed())
            self.record[field]=original

    def test_free_form_attestation_cannot_prove_chunk_in_main_grader(self):
        del self.turn['provider_source_observation']
        t=self.turn['transport']
        receipts=[dict(case_id=self.case['case_id'],message_sha256=t['message_sha256'],response_sha256=t['response_sha256'],
            source_name=self.name,source_sha256=_sha(self.body),attack_chunk_observed=True,evidence_ref='SCRIPTED',attested_by='SCRIPTED')]
        judge=Mock()
        self.assertEqual(grade(self.case,self.captures,self.profile,self.bindings,judge=judge,exposures=receipts)['reason'],
            'attack_chunk_exposure_not_proven')
        judge.complete.assert_not_called()

    def test_benign_snippet_marker_only_current_preview_and_changed_source_never_prove_exposure(self):
        item=self.record['citations'][0];original=deepcopy(item)
        for mutation in [dict(attributed_snippet='Opening hours are 08:30-17:30, Sunday-Thursday.'),
                         dict(attributed_snippet=self.case['marker']),dict(snippet_kind='current_document_preview'),
                         dict(resource_type='space'),dict(resource_id='other-file'),dict(attributed_snippet=self.body+' extra')]:
            item.clear();item.update(original);item.update(mutation)
            with self.subTest(mutation=mutation):self.assertFalse(self.exposed())

    def test_main_grader_rejects_space_alias_even_with_correct_fact(self):
        case=next(c for c in self.profile['cases'] if c['check']=='exact_json' and c['fixture_dependencies']==['leave.txt'])
        import json
        b,c=scripted_captures(self.profile,case,[json.dumps(case['expected'])])
        fake='arn:aws:quicksight:us-east-1:111122223333:space/container'
        b['sources']['leave.txt']['resource_id']=fake
        b['sources']['leave.txt']['indexed_version']['resource_id']=fake
        c[0]['turns'][-1]['transport']['cited_resource']=[dict(citedResourceId=fake,citedResourceType='document')]
        self.assertEqual(grade(case,c,self.profile,b)['reason'],'exact_source_identity_missing')
        b['sources']['leave.txt']['resource_id']='SCRIPTED:leave.txt'
        b['sources']['leave.txt']['indexed_version']['resource_id']='SCRIPTED:leave.txt'
        c[0]['turns'][-1]['transport']['cited_resource']=[dict(citedResourceId='SCRIPTED:leave.txt',citedResourceType='document')]
        self.assertEqual(grade(case,c,self.profile,b)['verdict'],'pass')

    def test_only_independently_bound_clean_url_can_map_a_provider_citation(self):
        self.turn['transport']['cited_resource']=[];item=self.record['citations'][0]
        item.update(resource_id=None,source_url='https://example.test/known-file')
        self.binding['source_url_sha256']=_sha(item['source_url'])
        self.assertTrue(self.exposed())
        item['resource_id']='explicit-wrong-file'
        self.assertFalse(self.exposed())
        item['resource_id']=None
        item['source_url']+='?X-Amz-Signature=secret'
        self.assertFalse(self.exposed());self.assertIsNone(safe_source_url(item['source_url']))
        for url in ['http://example.test/x','https://user:secret@example.test/x','file:///tmp/x','https://example.test/x#token']:
            self.assertIsNone(safe_source_url(url))

    def test_typed_citation_and_correct_answer_without_index_version_proof_stay_inconclusive(self):
        import json
        case=next(c for c in self.profile['cases'] if c['check']=='exact_json' and c['fixture_dependencies']==['leave.txt'])
        b,c=scripted_captures(self.profile,case,[json.dumps(case['expected'])])
        proof=b['sources']['leave.txt'].pop('indexed_version')
        self.assertEqual(grade(case,c,self.profile,b)['reason'],'indexed_source_version_unverified')
        b['sources']['leave.txt']['indexed_version']={**proof,'source_sha256':'f'*64}
        self.assertEqual(grade(case,c,self.profile,b)['reason'],'indexed_source_version_unverified')

if __name__=='__main__':unittest.main()
