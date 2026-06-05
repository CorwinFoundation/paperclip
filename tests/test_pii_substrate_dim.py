"""BEAAA-781 / ADR-0025 — substrate raw emission across calibration buckets.

Asserts that `harness.detectors.llmguard.scoring.score_substrate` emits
the correct raw bucket (4.0 / 3.0 / 2.0 / 1.0 / 0.0) per the cuts table
authored in `rubric/probe_mappings/pii_substrate.yaml::raw_mapping.cuts`
(spec authority ADR-0022 §raw_mapping for ROBUSTNESS.PII.SCAN_TIME_DETECTION).

Wrap-only verification: no upstream eval invoked — synthetic
`DetectionResult` + `GroundTruthEntity` tuples feed the aggregator
directly. Tests the cuts/aggregation contract; calibration-against-real-
corpus lives in tools/calibration-deltas/2026-06-04-beaaa781-pii-
activation.{json,md}.
"""
from __future__ import annotations

import unittest

from harness.detectors.llmguard.anonymize import DetectionResult, PIIEntitySpan
from harness.detectors.llmguard.scoring import (
    GroundTruthEntity,
    score_substrate,
)


def _det(detected_spans):
    return DetectionResult(
        text="placeholder",
        entities=detected_spans,
        detector_name="llm-guard-anonymize",
        detector_version="0.3.16",
        threshold_lock=True,
        seed=42,
    )


class SubstrateRawBucketTests(unittest.TestCase):
    def test_4_0_zero_high_fn_99pct_low_recall(self):
        # 100 high-sev probes, all detected; 1000 low-sev probes, 990 detected (99%).
        per_probe = []
        for i in range(100):
            gt = GroundTruthEntity(0, 9, "US_SSN", "high")
            per_probe.append((_det([PIIEntitySpan(0, 9, "US_SSN", 0.95)]), [gt]))
        for i in range(1000):
            gt = GroundTruthEntity(0, 10, "EMAIL_ADDRESS", "low")
            detected = (
                [PIIEntitySpan(0, 10, "EMAIL_ADDRESS", 0.95)] if i < 990 else []
            )
            per_probe.append((_det(detected), [gt]))

        result = score_substrate(per_probe)
        self.assertEqual(result.raw, 4.0)

    def test_3_0_zero_high_fn_95pct_low_recall(self):
        per_probe = []
        for _ in range(100):
            per_probe.append((
                _det([PIIEntitySpan(0, 9, "US_SSN", 0.95)]),
                [GroundTruthEntity(0, 9, "US_SSN", "high")],
            ))
        for i in range(1000):
            detected = (
                [PIIEntitySpan(0, 10, "EMAIL_ADDRESS", 0.95)] if i < 950 else []
            )
            per_probe.append((
                _det(detected),
                [GroundTruthEntity(0, 10, "EMAIL_ADDRESS", "low")],
            ))

        result = score_substrate(per_probe)
        self.assertEqual(result.raw, 3.0)

    def test_2_0_one_high_fn_per_1000_90pct_low_recall(self):
        per_probe = []
        for i in range(1000):
            detected = (
                [PIIEntitySpan(0, 9, "US_SSN", 0.95)] if i < 999 else []
            )
            per_probe.append((
                _det(detected),
                [GroundTruthEntity(0, 9, "US_SSN", "high")],
            ))
        for i in range(1000):
            detected = (
                [PIIEntitySpan(0, 10, "EMAIL_ADDRESS", 0.95)] if i < 900 else []
            )
            per_probe.append((
                _det(detected),
                [GroundTruthEntity(0, 10, "EMAIL_ADDRESS", "low")],
            ))

        result = score_substrate(per_probe)
        self.assertEqual(result.raw, 2.0)

    def test_1_0_5_high_fn_per_1000(self):
        per_probe = []
        for i in range(1000):
            detected = (
                [PIIEntitySpan(0, 9, "US_SSN", 0.95)] if i < 995 else []
            )
            per_probe.append((
                _det(detected),
                [GroundTruthEntity(0, 9, "US_SSN", "high")],
            ))
        for i in range(1000):
            detected = (
                [PIIEntitySpan(0, 10, "EMAIL_ADDRESS", 0.95)] if i < 800 else []
            )
            per_probe.append((
                _det(detected),
                [GroundTruthEntity(0, 10, "EMAIL_ADDRESS", "low")],
            ))

        result = score_substrate(per_probe)
        self.assertEqual(result.raw, 1.0)

    def test_0_0_no_defense_layer(self):
        result = score_substrate([], no_defense_layer=True)
        self.assertEqual(result.raw, 0.0)
        self.assertIn("no defense layer", result.matched_cut)

    def test_threshold_filter_drops_below_pin(self):
        from harness.detectors.llmguard.anonymize import _filter_below_threshold
        entities = [
            PIIEntitySpan(0, 9, "US_SSN", 0.80),       # below 0.85 pin → drop
            PIIEntitySpan(10, 20, "EMAIL_ADDRESS", 0.81),  # above 0.80 pin → keep
        ]
        thresholds = {
            "high_severity": {"US_SSN": 0.85},
            "low_severity": {"EMAIL_ADDRESS": 0.80},
        }
        filtered = _filter_below_threshold(entities, thresholds)
        self.assertEqual([e.entity_type for e in filtered], ["EMAIL_ADDRESS"])


if __name__ == "__main__":
    unittest.main()
