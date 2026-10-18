"""Independent regression checks. Temporary stores and mocked Scanner only."""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT=Path(__file__).parent
sys.path.insert(0,str(ROOT/'server'))
from qa_service import Code, IntakeError, IntakeStore, safe_provenance, translate_callback
from qa_runtime import ScanQueue, DispatchNotSent, DispatchUnknown
from quota_policy import quota_counted_sql

class Readiness(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=IntakeStore(str(Path(self.temp.name)/'store.sqlite'))
        self.store.put_code(Code('TEST','test','Test','standard',1,1,'2099-01-01'))
    def tearDown(self):
        self.store.db.close();self.temp.cleanup()
    def reserve(self,email='a@example.test',**kw):
        return self.store.reserve(code='TEST',email=email,agent_label='Agent',
            endpoint_url='https://example.test/private?not-to-store=CANARY',transport='rest',**kw)['scan_id']
    def counted(self):
        return self.store.db.execute('SELECT count(*) FROM scans s WHERE '+quota_counted_sql('s')).fetchone()[0]
    def test_refused_dispatch_releases_email_scan_and_daily_allowance(self):
        queue=ScanQueue(self.store,lambda _:(_ for _ in ()).throw(DispatchNotSent()))
        first=self.reserve()
        with self.assertRaises(DispatchNotSent):queue.submit(first,{})
        self.assertEqual(self.store.scan(first)['status'],'launch_failed')
        self.assertEqual(self.counted(),0)
        self.assertEqual(self.store.scans_since('test','2000'),0)
        self.assertTrue(self.reserve(email='b@example.test'))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM scans').fetchone()[0],2)
    def test_unknown_dispatch_stays_counted(self):
        queue=ScanQueue(self.store,lambda _:(_ for _ in ()).throw(TimeoutError()))
        first=self.reserve()
        with self.assertRaises(DispatchUnknown):queue.submit(first,{})
        self.assertEqual(self.counted(),1)
        with self.assertRaises(IntakeError):self.reserve()
        self.assertEqual(self.store.scans_since('test','2000'),1)
    def test_launch_failed_with_remote_identity_is_not_refunded(self):
        first=self.reserve();self.store.bind_scanner(first,'remote-id');self.store.launch_failed(first)
        self.assertEqual(self.counted(),1)
        with self.assertRaises(IntakeError):self.reserve()
    def test_original_terminal_failure_daily_cap_is_preserved(self):
        first=self.reserve();self.store.finish(first,{'tests':[],'meta':{'outcome':'failed'}})
        self.assertEqual(self.counted(),0)
        self.assertEqual(self.store.scans_since('test','2000'),1)
    def test_same_email_race_after_refund_admits_once(self):
        first=self.reserve();self.store.launch_failed(first)
        barrier=threading.Barrier(6);results=[]
        def run():
            barrier.wait()
            try:self.reserve();results.append('ok')
            except IntakeError:results.append('denied')
        threads=[threading.Thread(target=run) for _ in range(6)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(results.count('ok'),1)
        self.assertEqual(results.count('denied'),5)
    def test_provenance_survives_dispatch_callback_report_and_duplicate(self):
        queue=ScanQueue(self.store,lambda _:{'scan_id':'remote-id','provenance':{
            'scanner_version':'a'*40,'scanner_image_digest':'sha256:'+'b'*64,
            'corpus_snapshot_hash':'sha256:'+'c'*64,'seed':42}})
        first=self.reserve(provenance={'seed':42,'model_versions':{'target_requested':'model-alias'},
            'credential_secret':'DO_NOT_STORE','system_prompt':'DO_NOT_STORE','endpoint_url':'DO_NOT_STORE'})
        queue.submit(first,{'credential_secret':'EPHEMERAL'})
        rows=[{'test_id':'case-1','family':'f','prompt':'test','reply':'answer','verdict':'pass','pack_id':'starter-100@4'}]
        callback=translate_callback({'status':'complete','result_rows':rows,'corpus_version':'corpus-v1','scanner_version':'client-label'})
        queue.callback('remote-id',callback)
        token=self.store.scan(first)['report_token'];report=self.store.report(token)
        self.assertEqual(report['scanner_version'],'a'*40)
        self.assertEqual(report['scanner_client_version'],'client-label')
        self.assertEqual(report['corpus_version'],'corpus-v1')
        self.assertEqual(report['seed'],42)
        self.assertEqual(report['model_versions'],{'target_requested':'model-alias'})
        self.assertNotIn('DO_NOT_STORE',json.dumps(dict(self.store.scan(first))))
        self.assertNotIn('not-to-store',json.dumps(dict(self.store.scan(first))))
        self.store.finish(first,{'tests':[],'meta':{'provenance':{'seed':99}}})
        self.assertEqual(self.store.report(token)['seed'],42)
    def test_legacy_report_does_not_invent_metadata(self):
        first=self.reserve();token=self.store.finish(first,[]);report=self.store.report(token)
        self.assertNotIn('scanner_version',report);self.assertNotIn('seed',report)
    def test_legacy_completed_scan_with_null_outcome_still_counts(self):
        first=self.reserve();self.store.finish(first,[])
        self.store.db.execute('UPDATE scans SET outcome=NULL WHERE id=?',(first,))
        self.assertEqual(self.counted(),1)
        with self.assertRaises(IntakeError):self.reserve()
    def test_safe_metadata_rejects_arbitrary_nested_fields(self):
        self.assertEqual(safe_provenance({'seed':True,'auth':{'value':'secret'},
            'model_versions':{'target_requested':'alias','credential':'secret'}}),
            {'model_versions':{'target_requested':'alias'}})

class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        os.environ['QA_DB_PATH']=str(Path(cls.tmp.name)/'api.sqlite')
        import app
        cls.app=app
    @classmethod
    def tearDownClass(cls):cls.app.store.db.close();cls.tmp.cleanup()
    def test_scanner_acceptance_records_release_and_accepted_seed(self):
        client=MagicMock();client.__enter__.return_value=client
        identity=MagicMock(status_code=200)
        identity.json.return_value={'commit_sha':'a'*40,'image_digest':'sha256:'+'b'*64}
        accepted=MagicMock(status_code=202)
        accepted.json.return_value={'scan_id':'remote','seed':42,'corpus_snapshot_hash':'sha256:'+'c'*64}
        client.get.return_value=identity;client.post.return_value=accepted
        with patch.object(self.app.httpx,'Client',return_value=client):value=self.app._scanner_start({'credential_secret':'PRIVATE'})
        self.assertEqual(value['provenance']['scanner_version'],'a'*40)
        self.assertEqual(value['provenance']['seed'],42)
        self.assertNotIn('PRIVATE',json.dumps(value))
    def test_active_progress_filters_evidence_fields(self):
        row={'scanner_id':'remote','status':'running','partner':'p'}
        client=MagicMock();client.__enter__.return_value=client
        client.get.return_value.status_code=200
        client.get.return_value.json.return_value={'state':'running','processed_count':17,
            'accepted_pack_total':100,'result_rows':[{'prompt':'PRIVATE'}],'credential_secret':'PRIVATE'}
        with patch.object(self.app.store,'scan',return_value=row),patch.object(self.app.httpx,'Client',return_value=client):
            value=self.app._engine_progress('local')
        self.assertEqual(value['processed_count'],17);self.assertNotIn('PRIVATE',json.dumps(value))
    def test_finished_progress_retains_counts(self):
        row={'status':'finished','outcome':'complete','pack_version':'starter-100@4',
             'tests_json':json.dumps([{'verdict':'pass'},{'verdict':'fail'}]),'finished_at':'2026-10-18T10:00:00Z'}
        with patch.object(self.app.store,'scan',return_value=row):value=self.app._engine_progress('local')
        self.assertEqual(value['state'],'finished');self.assertEqual(value['processed_count'],2)
        self.assertEqual(value['accepted_pack_total'],100);self.assertEqual(value['usable_count'],2)

if __name__=='__main__':unittest.main(verbosity=2)
