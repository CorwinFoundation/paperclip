"""The old call ceiling is immutable unless an exact reviewed extension commits."""
from copy import deepcopy
from pathlib import Path
import json,os,sqlite3,tempfile,unittest
from unittest.mock import patch
from harness.amazon_quick_accounting import admit_global_budget,checked_budget,extend_global_budget,ledger_digest
from harness.amazon_quick_batch import ConversationLedger
from tests.test_amazon_quick_batch import target

class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=patch.dict(os.environ,{'ARE_AMAZON_QUICK_STATE_DIR':self.tmp.name});p.start();self.addCleanup(p.stop)
        c=ConversationLedger(target());c.close();self.path=Path(self.tmp.name)/'amazon-quick.sqlite3'
        db=sqlite3.connect(self.path)
        with db:db.execute('UPDATE pilots SET calls=1626,halted=1')
        baseline=ledger_digest(db);db.close()
        admit_global_budget(self.path,baseline_sha256=baseline,cap=2500,reserved=26)
        db=sqlite3.connect(self.path);before=ledger_digest(db);db.close()
        self.review=dict(approved=True,approval_ref='SCRIPTED://human4200within200',new_global_call_ceiling=4200,
            budget_usd=200,before_ledger_sha256=before,original_accounting=dict(schema='backbond-quick-global-budget/v1',
                baseline_ledger_sha256=baseline,global_call_ceiling=2500,retained_reserved_calls=26))

    def snapshot(self):
        db=sqlite3.connect(self.path)
        try:return ledger_digest(db),checked_budget(db,required=True)
        finally:db.close()

    def test_exact_extension_preserves_all_charges_reserve_halts_and_baseline(self):
        before=self.snapshot();after=extend_global_budget(self.path,self.review)
        self.assertEqual(after['charged_or_reserved'],1652);self.assertEqual(after['remaining'],2548)
        self.assertEqual(after['retained_reserved_calls'],26)
        db=sqlite3.connect(self.path)
        self.assertEqual(db.execute('SELECT calls,halted FROM pilots').fetchall(),[(1626,1)]);db.close()
        with self.assertRaises(ValueError):extend_global_budget(self.path,self.review)

    def test_no_approval_changed_snapshot_or_reduced_reserve_cannot_raise_cap(self):
        for mutation in [dict(approved=False),dict(approval_ref='REPLACE'),dict(budget_usd=201),
                         dict(new_global_call_ceiling=5001),dict(before_ledger_sha256='f'*64),
                         dict(original_accounting={**self.review['original_accounting'],'retained_reserved_calls':0})]:
            review={**deepcopy(self.review),**mutation};before=self.snapshot()
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):extend_global_budget(self.path,review)
            self.assertEqual(before,self.snapshot())

    def test_direct_cap_edit_without_reviewed_extension_fails_closed(self):
        db=sqlite3.connect(self.path)
        with db:db.execute('UPDATE quick_global_budget SET cap=4200')
        db.close()
        with self.assertRaisesRegex(ValueError,'reviewed_global_cap_extension'):self.snapshot()

if __name__=='__main__':unittest.main()
