import inspect
import tempfile
import unittest
from pathlib import Path

from stage_a_demo import core
from stage_a_demo.approval import _process_issuer, approve_mandate
from stage_a_demo.core import ApprovalVerifier, EvidenceStore, HumanApprovalIssuer, Mandate, SyntheticMerchantAdapter


class StageADemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "evidence.sqlite3"
        self.human = HumanApprovalIssuer("test-only-approval-secret")
        self.merchant = SyntheticMerchantAdapter()

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, store, case_id="c1", sku="SKU-1", amount=100, evidence=True, idem="idem-1"):
        mandate = Mandate(f"m-{case_id}", "SKU-1", 100)
        grant = approve_mandate(store, case_id=case_id, mandate=mandate)
        return store.record_case(case_id=case_id, grant_id=grant,
            asserted_sku=sku, asserted_amount_cents=amount, idempotency_key=idem,
            merchant=self.merchant, independent_evidence=evidence)

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
        # An approved retry: a second grant, the same idempotency key. The
        # merchant must still be reached once.
        first = approve_mandate(store, case_id="c1", mandate=mandate)
        second = approve_mandate(store, case_id="c1", mandate=mandate)
        kwargs = dict(case_id="c1", asserted_sku="SKU-1",
            asserted_amount_cents=100, idempotency_key="idem-1",
            merchant=merchant)
        store.record_case(grant_id=first, **kwargs)
        store.record_case(grant_id=second, **kwargs)
        # A spent grant is refused outright: single use is not weakened by replay.
        with self.assertRaises(PermissionError):
            store.record_case(grant_id=first, **kwargs)
        self.assertEqual(1, store.execution_count("idem-1"))
        self.assertEqual(1, merchant.calls)

    def test_un_consuming_the_approvals_row_does_not_revive_the_grant(self):
        """BEAAA-35354 SA9. Single use does not rest on a column the execution
        path can write.

        The probe approved one 100 cent mandate, executed it, ran
        ``UPDATE approvals SET consumed = 0``, and executed again with a fresh
        idempotency key: the merchant was reached twice off one human approval.
        """
        class CountingMerchant(SyntheticMerchantAdapter):
            def __init__(self):
                self.amounts = []

            def purchase(self, **kwargs):
                self.amounts.append(kwargs["amount_cents"])
                return super().purchase(**kwargs)

        store = EvidenceStore(self.path)
        merchant = CountingMerchant()
        mandate = Mandate("m-c1", "SKU-1", 100)
        grant = approve_mandate(store, case_id="c1", mandate=mandate)
        kwargs = dict(case_id="c1", asserted_sku="SKU-1", asserted_amount_cents=100, merchant=merchant)
        self.assertEqual("MATCH", store.record_case(grant_id=grant, idempotency_key="idem-1", **kwargs)["status"])

        # The execution path's own handle, writing the flag single use used to
        # rest on. The grant stays spent anyway.
        with store.db:
            store.db.execute("UPDATE approvals SET consumed = 0 WHERE grant_id = ?", (grant,))
        self.assertEqual(0, store.db.execute(
            "SELECT consumed FROM approvals WHERE grant_id = ?", (grant,)).fetchone()[0])
        with self.assertRaises(PermissionError):
            store.record_case(grant_id=grant, idempotency_key="idem-2", **kwargs)
        self.assertEqual([100], merchant.amounts)
        self.assertEqual(0, store.execution_count("idem-2"))
        # A second connection to the same file is no way round it either.
        second = EvidenceStore(self.path)
        with self.assertRaises(PermissionError):
            second.record_case(grant_id=grant, idempotency_key="idem-3", **kwargs)
        self.assertEqual([100], merchant.amounts)

    def test_execution_callers_cannot_mint_or_obtain_human_capability(self):
        """BEAAA-34389 defect 1, BEAAA-35331. Asserts capability, not names.

        The previous version checked hasattr(verifier, "mint") and three similar
        shape assertions. It could not fail while a caller could compute a valid
        token, which is exactly what the Founder gate probe found at 91e72738.
        """
        mandate = Mandate("m-c1", "SKU-1", 100)
        authority = _process_issuer()
        token = authority.approve(mandate, "grant-c1")
        self.assertIsInstance(token, str)
        self.assertTrue(authority.attests(mandate, "grant-c1", token))

        # Minting needs key material, and the verifying half is not a caller's
        # to build at all: it is rebuilt inside record_case (BEAAA-35331 SA6).
        with self.assertRaises(TypeError):
            HumanApprovalIssuer()
        with self.assertRaises(TypeError):
            ApprovalVerifier()
        with self.assertRaises(TypeError):
            ApprovalVerifier({"any": "mapping"})
        with self.assertRaises(ValueError):
            HumanApprovalIssuer("")

        # The retired shared default is gone from the module.
        self.assertNotIn("stage-a-human-demo-secret", inspect.getsource(core))

        # A foreign issuer mints tokens the process authority does not attest.
        for guess in ("stage-a-human-demo-secret", "secret", "demo"):
            self.assertFalse(
                authority.attests(mandate, "grant-c1", HumanApprovalIssuer(guess).approve(mandate, "grant-c1"))
            )
            self.assertFalse(authority.attests(mandate, "grant-c1", core._approval_digest(guess, mandate, "grant-c1")))

        # An approval is bound to its own mandate and does not carry to another.
        self.assertFalse(authority.attests(Mandate("m-other", "SKU-9", 900), "grant-c1", token))
        # It is bound to its own grant too, so a genuine token replayed under a
        # second approvals row is not attested (BEAAA-35331 SA8b).
        self.assertFalse(authority.attests(mandate, "grant-c1-replay", token))

    def test_missing_independent_evidence_never_succeeds(self):
        packet = self.record(EvidenceStore(self.path), evidence=False)
        self.assertEqual("UNKNOWN/INCOMPLETE", packet["status"])
        self.assertIsNone(packet["sources"]["independent_merchant_evidence"])

    def test_reconstruction_separates_sources_and_a_recorded_grant_is_required(self):
        store = EvidenceStore(self.path)
        packet = self.record(store)
        self.assertEqual({"mandate", "approval", "agent_assertion", "independent_merchant_evidence"}, set(packet["sources"]))
        # No token, verifier or mandate is accepted from the caller any more.
        params = set(inspect.signature(EvidenceStore.record_case).parameters)
        self.assertEqual(set(), params & {"approval_token", "approval_verifier", "mandate"})
        with self.assertRaises(PermissionError):
            store.record_case(case_id="bad", grant_id="grant-invented", asserted_sku="SKU-1",
                asserted_amount_cents=100, idempotency_key="bad", merchant=self.merchant)
        self.assertEqual(0, store.execution_count("bad"))


if __name__ == "__main__":
    unittest.main()
