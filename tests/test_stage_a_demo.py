import tempfile
import unittest
from pathlib import Path

from stage_a_demo.core import ApprovalVerifier, EvidenceStore, HumanApprovalIssuer, Mandate, SyntheticMerchantAdapter


class StageADemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "evidence.sqlite3"
        self.human = HumanApprovalIssuer()
        self.merchant = SyntheticMerchantAdapter()

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, store, case_id="c1", sku="SKU-1", amount=100, evidence=True, idem="idem-1"):
        mandate = Mandate(f"m-{case_id}", "SKU-1", 100)
        token, verifier = self.human.approve(mandate)
        return store.record_case(case_id=case_id, mandate=mandate, approval_token=token,
            asserted_sku=sku, asserted_amount_cents=amount, idempotency_key=idem,
            approval_verifier=verifier, merchant=self.merchant, independent_evidence=evidence)

    def test_correct_purchase_and_private_receipt_persist(self):
        store = EvidenceStore(self.path)
        packet = self.record(store)
        self.assertEqual("MATCH", packet["status"])
        self.assertEqual("DEMO", packet["mode"])
        self.assertTrue(__import__("json").loads(packet["execution"]["receipt_json"])["private"])
        store.close()
        reopened = EvidenceStore(self.path)
        self.assertEqual("MATCH", reopened.packet("c1")["status"])

    def test_mismatch_is_reviewable_with_sources(self):
        packet = self.record(EvidenceStore(self.path), sku="WRONG", amount=101)
        self.assertEqual("MISMATCH", packet["status"])
        self.assertEqual({"amount_cents", "sku"}, {m["field"] for m in packet["reviewable_mismatches"]})
        self.assertEqual("SKU-1", packet["sources"]["mandate"]["sku"])

    def test_duplicate_retry_has_one_execution(self):
        class CountingMerchant(SyntheticMerchantAdapter):
            def __init__(self):
                self.calls = 0

            def purchase(self, **kwargs):
                self.calls += 1
                return super().purchase(**kwargs)

        store = EvidenceStore(self.path)
        merchant = CountingMerchant()
        mandate = Mandate("m-c1", "SKU-1", 100)
        token, verifier = self.human.approve(mandate)
        kwargs = dict(case_id="c1", mandate=mandate, approval_token=token, asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="idem-1", approval_verifier=verifier,
            merchant=merchant)
        store.record_case(**kwargs)
        store.record_case(**kwargs)
        self.assertEqual(1, store.execution_count("idem-1"))
        self.assertEqual(1, merchant.calls)

    def test_execution_callers_cannot_mint_or_obtain_human_capability(self):
        mandate = Mandate("m-c1", "SKU-1", 100)
        token, verifier = self.human.approve(mandate)
        self.assertIsInstance(verifier, ApprovalVerifier)
        self.assertFalse(hasattr(verifier, "mint"))
        self.assertFalse(hasattr(verifier, "approve"))
        self.assertFalse(hasattr(self.merchant, "approval_issuer"))
        self.assertTrue(verifier.verify(mandate, token))

    def test_missing_independent_evidence_never_succeeds(self):
        packet = self.record(EvidenceStore(self.path), evidence=False)
        self.assertEqual("UNKNOWN/INCOMPLETE", packet["status"])
        self.assertIsNone(packet["sources"]["independent_merchant_evidence"])

    def test_reconstruction_separates_sources_and_token_is_required(self):
        store = EvidenceStore(self.path)
        packet = self.record(store)
        self.assertEqual({"mandate", "approval", "agent_assertion", "independent_merchant_evidence"}, set(packet["sources"]))
        mandate = Mandate("m-bad", "SKU-1", 100)
        with self.assertRaises(PermissionError):
            store.record_case(case_id="bad", mandate=mandate, approval_token="agent-minted",
                asserted_sku="SKU-1", asserted_amount_cents=100, idempotency_key="bad",
                approval_verifier=ApprovalVerifier(), merchant=self.merchant)


if __name__ == "__main__":
    unittest.main()
