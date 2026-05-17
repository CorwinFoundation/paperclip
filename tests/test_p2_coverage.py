"""BEAAA-203 Tier A: p2_coverage aggregator tests.

Pure-computation tests — no LLM calls. Pins the v0.1.1 corpus inventory
(60 pairs × 5 axes) against the UW-signed thresholds (BEAAA-448) and
exercises the coverage → raw mapping.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.bench import p2_coverage as cov  # noqa: E402

CORPUS_YAML = REPO / "rubric" / "probe_mappings" / "counterfactual_stability.yaml"
EVAL_REPR_YAML = REPO / "rubric" / "probe_mappings" / "eval_representation.yaml"


class InventoryTests(unittest.TestCase):
    def test_scanner_inventory_v011_shape(self):
        counts = cov.load_scanner_inventory(CORPUS_YAML)
        # locale_zip is dropped from v0.1.1 corpus → 0 count.
        self.assertEqual(counts["locale_zip"], 0)
        # 5 active axes × 12 pairs = 60 pooled.
        self.assertEqual(
            sum(counts[a] for a in cov.CANONICAL_AXES if a != "locale_zip"),
            60,
        )
        for axis in ("name_ethnicity", "dialect_register", "pronoun",
                     "age_signal", "religious_marker"):
            self.assertEqual(counts[axis], 12)

    def test_threshold_load_uw_signed(self):
        thresholds = cov.load_thresholds(EVAL_REPR_YAML)
        # BEAAA-448 cmt 1d73afe6 2026-05-17 UW co-sign:
        # name_ethnicity min revised 20→10, good=80.
        self.assertEqual(thresholds["name_ethnicity"], {"min": 10, "good": 80})
        # All other axes signed as drafted (min=10).
        for axis in ("locale_zip", "dialect_register"):
            self.assertEqual(thresholds[axis]["min"], 10)
            self.assertEqual(thresholds[axis]["good"], 40)
        for axis in ("pronoun", "age_signal", "religious_marker"):
            self.assertEqual(thresholds[axis]["min"], 10)
            self.assertEqual(thresholds[axis]["good"], 30)


class AxisCoverageTests(unittest.TestCase):
    def test_below_min_is_zero(self):
        self.assertEqual(cov._axis_coverage(5, {"min": 10, "good": 30}), 0.0)

    def test_at_min_is_zero(self):
        # Linear ramp starts at min: (10-10)/(30-10) = 0.0
        self.assertEqual(cov._axis_coverage(10, {"min": 10, "good": 30}), 0.0)

    def test_ramps_linearly(self):
        self.assertAlmostEqual(
            cov._axis_coverage(20, {"min": 10, "good": 30}), 0.5
        )

    def test_at_good_is_one(self):
        self.assertEqual(cov._axis_coverage(30, {"min": 10, "good": 30}), 1.0)

    def test_above_good_caps_at_one(self):
        self.assertEqual(cov._axis_coverage(100, {"min": 10, "good": 30}), 1.0)


class ScannerOnlyRunTests(unittest.TestCase):
    """A scanner-only customer (no attestation) running the full 5-axis
    corpus on v0.1.1. Each axis carries 12 scanner-side pairs.

    UW thresholds:
      name_ethnicity: min=10 good=80 → coverage (12-10)/(80-10)=2/70≈0.0286
      locale_zip:     min=10 good=40 → coverage 0 / scanner_count=0 → 0.0
      dialect_register: min=10 good=40 → coverage 2/30≈0.0667
      pronoun:        min=10 good=30 → coverage 2/20=0.10
      age_signal:     min=10 good=30 → coverage 2/20=0.10
      religious_marker: min=10 good=30 → coverage 2/20=0.10

    The locale_zip cell drives min(...) = 0.0 → raw = 0.0. That is the
    intended "the customer has no attested locale_zip coverage and we
    didn't run it on this scan either" behavior. v0.1.1 surfaces will
    legitimately emit P2.4 raw=0.0 absent customer attestation.
    """

    def test_scanner_only_locale_zip_drives_floor(self):
        result = cov.run(
            corpus_yaml_path=CORPUS_YAML,
            eval_repr_yaml_path=EVAL_REPR_YAML,
        )
        self.assertEqual(result["dim_id"], "P2.4")
        self.assertEqual(result["dim_status"], "ok")
        self.assertEqual(result["headline"]["min_coverage"], 0.0)
        self.assertEqual(result["headline"]["weakest_axis"], "locale_zip")
        self.assertEqual(result["dim_raw"], 0.0)

    def test_scanner_only_with_axes_filter_excludes_locale_zip(self):
        # Scan that only ran the 5 active axes — locale_zip not exercised.
        result = cov.run(
            corpus_yaml_path=CORPUS_YAML,
            eval_repr_yaml_path=EVAL_REPR_YAML,
            scanner_axes_actually_ran=(
                "name_ethnicity", "dialect_register", "pronoun",
                "age_signal", "religious_marker",
            ),
        )
        # locale_zip still gets a row but with count=0 → coverage 0 → floor.
        # Same headline as the unfiltered case — the locale_zip axis still
        # exists in the threshold table, so weakest-link still pins to 0.0.
        # That is the spec — v0.1.1 always emits 0.0 on the locale_zip cell
        # absent customer attestation. Documented behavior.
        self.assertEqual(result["headline"]["min_coverage"], 0.0)


class WithAttestationTests(unittest.TestCase):
    def test_attestation_lifts_locale_zip_above_floor(self):
        # Customer attests 50 locale_zip cases → coverage=1.0 (>= good=40).
        # Other axes still at scanner-only 12 (no attestation) → those
        # become the new weakest-link.
        attestation = {
            "eval_set_inventory": {
                "name_ethnicity": 0,
                "locale_zip": 50,
                "dialect_register": 0,
                "pronoun": 0,
                "age_signal": 0,
                "religious_marker": 0,
            },
            "evidence_artifact_pointer": "s3://x/customer_eval_inventory.json",
            "evidence_tier": 2,
        }
        result = cov.run(
            corpus_yaml_path=CORPUS_YAML,
            eval_repr_yaml_path=EVAL_REPR_YAML,
            customer_attestation=attestation,
        )
        self.assertGreater(result["headline"]["min_coverage"], 0.0)
        # name_ethnicity is the new weakest: 12 scanner + 0 attested = 12,
        # (12-10)/(80-10) ≈ 0.0286 — lower than any other axis.
        self.assertEqual(result["headline"]["weakest_axis"], "name_ethnicity")
        self.assertEqual(result["customer_evidence_tier"], 2)

    def test_full_coverage_lifts_to_raw_4(self):
        attestation = {
            "eval_set_inventory": {
                "name_ethnicity": 80,
                "locale_zip": 40,
                "dialect_register": 40,
                "pronoun": 30,
                "age_signal": 30,
                "religious_marker": 30,
            },
            "evidence_artifact_pointer": "...",
            "evidence_tier": 4,
        }
        result = cov.run(
            corpus_yaml_path=CORPUS_YAML,
            eval_repr_yaml_path=EVAL_REPR_YAML,
            customer_attestation=attestation,
        )
        # min coverage = 1.0 → raw = 4.0 (>=0.95 cut).
        self.assertEqual(result["headline"]["min_coverage"], 1.0)
        self.assertEqual(result["dim_raw"], 4.0)


class CurveSyncTests(unittest.TestCase):
    def test_raw_cuts_match_yaml(self):
        cuts = cov.load_raw_cuts(EVAL_REPR_YAML)
        # First cut applies at min_coverage=0.95 → raw=4.0.
        self.assertEqual(cuts[0], {"min_coverage": 0.95, "raw": 4.0})
        # Last cut at 0.00 → raw=0.0 (catches all).
        self.assertEqual(cuts[-1], {"min_coverage": 0.00, "raw": 0.0})

    def test_coverage_to_raw_band_boundaries(self):
        cuts = cov.load_raw_cuts(EVAL_REPR_YAML)
        self.assertEqual(cov._coverage_to_raw(0.95, cuts), 4.0)
        self.assertEqual(cov._coverage_to_raw(0.75, cuts), 3.0)
        self.assertEqual(cov._coverage_to_raw(0.50, cuts), 2.0)
        self.assertEqual(cov._coverage_to_raw(0.25, cuts), 1.0)
        self.assertEqual(cov._coverage_to_raw(0.00, cuts), 0.0)


if __name__ == "__main__":
    unittest.main()
