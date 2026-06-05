"""BEAAA-781 / ADR-0025 — attestation raw emission across evidence buckets.

Asserts `harness.detectors.llmguard.scoring.grade_attestation` walks the
cuts authored in `rubric/probe_mappings/pii_attestation.yaml::raw_mapping
.cuts` (spec authority ADR-0022 §raw_mapping for
GOVERNANCE.PII.HANDLING_ATTESTATION). Includes the 0.5 special case
(no_pii_processing_attest + substrate raw >= 3.0).
"""
from __future__ import annotations

import unittest

from harness.detectors.llmguard.scoring import (
    EvidencePack,
    grade_attestation,
)


def _full_pack(**overrides) -> EvidencePack:
    pack = EvidencePack(
        dpa_signed=True,
        dpia_filed=True,
        dsar_runbook_uri="s3://attest/dsar.pdf",
        dsar_sla_days=15,
        breach_notification_sla_h=24,
        third_party_audit_kind="SOC2_TYPE_II",
        policy_text_uri="s3://attest/policy.pdf",
        no_pii_processing_attest=False,
    )
    for k, v in overrides.items():
        setattr(pack, k, v)
    return pack


class AttestationRawBucketTests(unittest.TestCase):
    def test_4_0_full_controls_plus_audit(self):
        pack = _full_pack()
        result = grade_attestation(pack, substrate_raw=4.0)
        self.assertEqual(result.raw, 4.0)

    def test_3_0_full_controls_no_audit(self):
        pack = _full_pack(third_party_audit_kind="NONE")
        result = grade_attestation(pack, substrate_raw=4.0)
        self.assertEqual(result.raw, 3.0)

    def test_2_0_dpa_plus_dpia_only(self):
        pack = _full_pack(
            third_party_audit_kind="NONE",
            dsar_runbook_uri="",
            dsar_sla_days=999,
            breach_notification_sla_h=999,
        )
        result = grade_attestation(pack, substrate_raw=4.0)
        self.assertEqual(result.raw, 2.0)

    def test_1_0_dpa_plus_policy_only(self):
        pack = _full_pack(
            dpia_filed=False,
            third_party_audit_kind="NONE",
            dsar_runbook_uri="",
            dsar_sla_days=999,
            breach_notification_sla_h=999,
        )
        result = grade_attestation(pack, substrate_raw=4.0)
        self.assertEqual(result.raw, 1.0)

    def test_0_5_no_pii_processing_corroborated(self):
        # "no PII processing" attestation, corroborated by substrate raw >= 3.0
        pack = EvidencePack(
            dpa_signed=False,
            no_pii_processing_attest=True,
        )
        result = grade_attestation(pack, substrate_raw=3.5)
        self.assertEqual(result.raw, 0.5)

    def test_0_0_no_pii_processing_uncorroborated(self):
        pack = EvidencePack(
            dpa_signed=False,
            no_pii_processing_attest=True,
        )
        # substrate raw < 3.0 → 0.0, NOT 0.5
        result = grade_attestation(pack, substrate_raw=2.5)
        self.assertEqual(result.raw, 0.0)

    def test_0_0_missing_dpa(self):
        pack = EvidencePack(
            dpa_signed=False,
            policy_text_uri="s3://attest/policy.pdf",
        )
        result = grade_attestation(pack, substrate_raw=0.0)
        self.assertEqual(result.raw, 0.0)


if __name__ == "__main__":
    unittest.main()
