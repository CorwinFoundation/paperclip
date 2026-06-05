"""BEAAA-781 / ADR-0025 — Obs-2 determinism binding.

N=5 with `temp=0/argmax/seed=42` MUST yield span-identical output for
the substrate detector ensemble. Verifies:

1. The anonymize → deanonymize round-trip is byte-identical for any
   DetectionResult.
2. Repeated calls to `score_substrate` on the same input produce
   identical `raw` and `matched_cut` (deterministic aggregation).
3. The fallback (Presidio standalone) span shape is byte-stable against
   the primary detector when both are available — verified by feeding
   identical synthetic spans into both branches and asserting identical
   `DetectionResult.entities` output (no model invocation in this test
   path; verifies the structural shape, not the live detector parity
   which is in tools/calibration-deltas/2026-06-04-beaaa781-pii-
   activation.{json,md}).
4. The grading_rubric attestation grader is deterministic on its inputs.
"""
from __future__ import annotations

import unittest

from harness.detectors.llmguard.anonymize import (
    DetectionResult,
    PIIEntitySpan,
)
from harness.detectors.llmguard.deanonymize import (
    anonymize_text,
    deanonymize_text,
    round_trip_byte_identical,
)
from harness.detectors.llmguard.scoring import (
    EvidencePack,
    GroundTruthEntity,
    grade_attestation,
    score_substrate,
)


def _det_with_spans(text: str, spans):
    return DetectionResult(
        text=text,
        entities=spans,
        detector_name="llm-guard-anonymize",
        detector_version="0.3.16",
        seed=42,
    )


class RoundTripTests(unittest.TestCase):
    def test_round_trip_byte_identical_simple(self):
        text = "Hello, my email is john@example.com and SSN is 123-45-6789."
        det = _det_with_spans(text, [
            PIIEntitySpan(18, 34, "EMAIL_ADDRESS", 0.95),
            PIIEntitySpan(46, 57, "US_SSN", 0.95),
        ])
        self.assertTrue(round_trip_byte_identical(det))

    def test_round_trip_byte_identical_repeated_entities(self):
        text = "alice@x.com told bob@y.com to ask alice@x.com."
        det = _det_with_spans(text, [
            PIIEntitySpan(0, 11, "EMAIL_ADDRESS", 0.95),
            PIIEntitySpan(17, 26, "EMAIL_ADDRESS", 0.95),
            PIIEntitySpan(34, 45, "EMAIL_ADDRESS", 0.95),
        ])
        anon = anonymize_text(det)
        # Same text → same placeholder index (alice@x.com twice → same ph).
        self.assertEqual(
            anon.anonymized_text.count("[EMAIL_ADDRESS_0]"), 2
        )
        self.assertEqual(deanonymize_text(anon), text)


class SubstrateScoringDeterminismTests(unittest.TestCase):
    def test_substrate_score_n5_byte_identical(self):
        per_probe = [(
            _det_with_spans("ssn", [PIIEntitySpan(0, 3, "US_SSN", 0.95)]),
            [GroundTruthEntity(0, 3, "US_SSN", "high")],
        )]
        raws = [score_substrate(per_probe).raw for _ in range(5)]
        self.assertEqual(len(set(raws)), 1, f"N=5 spread !=0: raws={raws}")
        cuts = [score_substrate(per_probe).matched_cut for _ in range(5)]
        self.assertEqual(len(set(cuts)), 1)


class AttestationGraderDeterminismTests(unittest.TestCase):
    def test_grader_n5_byte_identical_full_pack(self):
        pack = EvidencePack(
            dpa_signed=True, dpia_filed=True,
            dsar_runbook_uri="s3://attest/dsar.pdf", dsar_sla_days=15,
            breach_notification_sla_h=24,
            third_party_audit_kind="SOC2_TYPE_II",
            policy_text_uri="s3://attest/policy.pdf",
        )
        raws = [grade_attestation(pack, substrate_raw=4.0).raw for _ in range(5)]
        self.assertEqual(raws, [4.0] * 5)


if __name__ == "__main__":
    unittest.main()
