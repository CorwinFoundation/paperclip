"""The full-run gate derives coverage from SCRIPTED bounded source probes."""
from copy import deepcopy
from datetime import datetime,timezone
import unittest
import json,tempfile,io
from pathlib import Path
from contextlib import redirect_stdout
from evals.amazon_quick_assessment import load_profile,digest
from evals.quick_source_evidence import SCHEMA
from harness.amazon_quick import _sha
from tools.qualify_quick_grading import scripted_captures
from tools.quick_applicability import prepare
from tools.quick_assessment import selection_binding
from tools.quick_source_checks import prepare_probe,qualify
from tools import quick_source_checks as cli

class SourceQualificationTests(unittest.TestCase):
    def setUp(self):
        self.profile=load_profile('hr_finance','1.3');self.full=prepare(self.profile,'a'*40)['selection']
        self.probe=prepare_probe(self.profile,'a'*40);captures=[];bindings=None
        for entry in self.probe['cases']:
            case=entry['case'];bindings,parts=scripted_captures(self.profile,case,['SCRIPTED native answer']*len(case['conversations']))
            for spec in bindings['sources'].values():spec['ui_capture_spec_sha256']=_sha('SCRIPTED capture spec')
            for i,part in enumerate(parts):
                cid=case['case_id']+':1:'+str(i);part.update(case_id=cid,attempt=1)
                for j,turn in enumerate(part['turns']):
                    t=turn['transport'];t.update(conversation_sha256=_sha(cid),message_sha256=_sha(cid+str(j)),
                        system_message_sha256=_sha(cid+str(j)+'system'),event_timestamp=1790812802000)
                    source=case['fixture_dependencies'][min(i,len(case['fixture_dependencies'])-1)]
                    turn['provider_source_observation']=dict(schema=SCHEMA,source='quick_provider_citation_dialog',truncated=False,
                        capture_spec_sha256=_sha('SCRIPTED capture spec'),**{k:t[k] for k in ('conversation_sha256','message_sha256',
                            'system_message_sha256','response_sha256','prompt_sha256','user_arn_sha256','account_id_sha256','agent_id_sha256')},
                        citations=[dict(resource_id=bindings['sources'][source]['resource_id'],resource_type='document',source_url=None,
                            attributed_snippet=self.profile['fixture']['sources'][source],snippet_kind='response_citation')])
                captures.append(part)
        self.scope=dict(selection=dict(approved=True,pack_sha256=self.probe['pack_sha256'],max_attempts=1,
            applicability_approval_ref='SCRIPTED://probe-review'),sources=bindings['sources'],source_setup_ref='SCRIPTED://index-review',
            qualification_not_before='2026-10-01T00:00:00Z',expires_at='2026-10-02T00:00:00Z')
        self.run=dict(status='capture_complete',profile_sha256=self.profile['profile_sha256'],scope_sha256=digest(self.scope),
            selection=selection_binding(self.probe,self.scope),bindings=bindings,captures=captures)
        self.seal()

    def seal(self):self.run['run_sha256']=digest({k:v for k,v in self.run.items() if k!='run_sha256'})
    def qualify(self):return qualify(self.profile,self.scope,self.probe,self.run,self.full)

    def test_canonical_probe_covers_required_sources_and_all_roles_under50calls(self):
        self.assertEqual(self.probe['case_count'],16);self.assertEqual(self.probe['initial_target_calls'],28)
        result=self.qualify();self.assertEqual(result['status'],'source_observability_qualified')
        self.assertEqual(len(result['required_sources']),24);self.assertEqual(len(result['required_attack_sources']),10)
        self.assertEqual(result['target_calls'],0);self.assertTrue(self.full['source_qualification_required'])

    def test_missing_attack_snippet_cannot_be_approved_into_full_run(self):
        for capture in self.run['captures']:
            for turn in capture['turns']:turn.pop('provider_source_observation',None)
        self.seal();self.assertEqual(self.qualify()['status'],'source_observability_incomplete')

    def test_old_or_replayed_native_records_cannot_qualify_new_window(self):
        turn=self.run['captures'][0]['turns'][0];turn['transport']['event_timestamp']=1;self.seal()
        with self.assertRaisesRegex(ValueError,'fresh_complete'):self.qualify()
        turn['transport']['event_timestamp']=1790812802000
        self.run['captures'][1]['turns'][0]['transport']['conversation_sha256']=turn['transport']['conversation_sha256']
        self.seal()
        with self.assertRaisesRegex(ValueError,'fresh_unique'):self.qualify()

    def test_cli_writes_private_proof_bundle_from_exact_probe_inputs_without_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);args=['qualify','--output',str(root/'bundle.json')]
            for name,value in [('scope',self.scope),('selection',self.probe),('run',self.run),('full-selection',self.full)]:
                path=root/(name+'.json');path.write_text(json.dumps(value),encoding='utf-8');args+=['--'+name,str(path)]
            with redirect_stdout(io.StringIO()):self.assertEqual(cli.main(args),0)
            bundle=json.loads((root/'bundle.json').read_bytes())
            self.assertEqual(bundle['qualification']['status'],'source_observability_qualified')
            self.assertEqual(bundle['run'],self.run);self.assertEqual(bundle['qualification']['target_calls'],0)

if __name__=='__main__':unittest.main()
