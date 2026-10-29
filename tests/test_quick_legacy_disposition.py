"""SCRIPTED legacy control-flow fixtures cannot refund or reopen an old run."""
import hashlib,json,sqlite3,unittest
from unittest.mock import patch
from harness import amazon_quick_recovery as recovery
from tests import test_quick_recovery as fixtures
from tests.test_amazon_quick_batch import row

class LegacyDispositionTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.RecoveryTests('test_exact_remaining_plan_and_apply_preserve_all_charges_observed_rows_and_claim')
        self.f.setUp();self.addCleanup(self.f.doCleanups);f=self.f
        f.approved()
        f.saved_run.pop('capture_protocol');f.saved_run['captures'][1]['initialization']=[
            dict(attempt=1,outcome='sdk_unavailable',phase='navigation',page_crashed=False,error_code='net::ERR_CONNECTION_RESET')]
        self.raw=f.seal(f.saved_run)
        native=[row(f.targets[f.expected[0].role],prompt,conversation_id='observed-c',user_message_id='m'+str(i),system_message_id='s'+str(i))
            for i,prompt in enumerate(f.expected[0].turns)]
        self.native=json.dumps(dict(schema='backbond-quick-settled-native-source/v1',
            records=[dict(event_id='SCRIPTED-event-'+str(i),body=r) for i,r in enumerate(native)],target_calls=0)).encode()
        self.stopped=json.dumps(dict(status='verified_closed',worker='stopped_standard',temporary_users_remaining=0,
            actual_runtime_role_closed_trust_and_policies_verified=True)).encode()
        self.worker=b'SCRIPTED qualified legacy flow only';sha=lambda b:hashlib.sha256(b).hexdigest()
        self.review=dict(approved=True,review_ref='SCRIPTED://operator-legacy-review',new_work_authorization_ref='SCRIPTED://new-work',
            qualified_legacy_worker_sha256=sha(self.worker),source_run_file_sha256=sha(self.raw),
            settled_native_file_sha256=sha(self.native),stopped_runtime_file_sha256=sha(self.stopped),
            before_ledger_sha256=f.snapshot()[0],owners=['unsent-owner'])

    def apply(self):
        f=self.f
        with patch.object(recovery,'LEGACY_NO_SEND_WORKER_SHA256',hashlib.sha256(self.worker).hexdigest()):
            return recovery.adjudicate_legacy_no_send(f.path,f.profile,f.scope,f.selection,self.raw,self.worker,self.native,
                self.stopped,self.review,source_snapshot=f.original_snapshot)

    def test_only_reviewed_pre_send_owner_is_dispositioned_with_charges_and_halts_retained(self):
        before=self.f.snapshot()[1];result=self.apply()
        self.assertEqual(result['charged_or_reserved'],before['charged_or_reserved']);self.assertFalse(result['old_halts_released'])
        self.assertEqual(before,self.f.snapshot()[1])
        db=sqlite3.connect(self.f.path)
        self.assertEqual(db.execute("SELECT status FROM quick_batch_submissions WHERE owner='unsent-owner'").fetchone(),('reviewed_not_submitted',))
        self.assertEqual(db.execute('SELECT halted FROM quick_batch_authorizations').fetchone(),(1,));db.close()
        with self.assertRaises(ValueError):self.apply()

    def test_approval_worker_artifact_owner_or_stopped_proof_mismatch_never_changes_ledger(self):
        original=dict(self.review)
        for mutation in [dict(approved=False),dict(qualified_legacy_worker_sha256='f'*64),dict(owners=[]),
                         dict(stopped_runtime_file_sha256='f'*64),dict(before_ledger_sha256='f'*64)]:
            self.review={**original,**mutation};before=self.f.snapshot()
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.apply()
            self.assertEqual(before,self.f.snapshot())

    def test_zero_attempted_flag_cannot_override_recorded_send_intent(self):
        db=sqlite3.connect(self.f.path)
        with db:db.execute("INSERT INTO quick_batch_send_intents VALUES ('unsent-owner',0,1)")
        db.close()
        source=sqlite3.connect(self.f.path);copy=sqlite3.connect(self.f.original_snapshot);source.backup(copy);copy.close();source.close()
        self.review['before_ledger_sha256']=self.f.snapshot()[0];before=self.f.snapshot()
        with self.assertRaisesRegex(ValueError,'send_intent'):self.apply()
        self.assertEqual(before,self.f.snapshot())

if __name__=='__main__':unittest.main()
