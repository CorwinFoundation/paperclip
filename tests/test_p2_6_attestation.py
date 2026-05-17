"""BEAAA-199 Phase 2 — P2.6 attestation reader tests.

Deterministic; no LLM calls. Pins the UW+CEO-signed weights (0.4/0.4/0.2)
and the tier raw_contributions (0, 1, 2, 3, 4) from
`rubric/probe_mappings/mitigation_evidence.yaml`. Any change to the YAML
that breaks these tests is a co-sign-gated rubric change per AGENTS.md.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness import p2_6_attestation as att  # noqa: E402


class SpecLoadTests(unittest.TestCase):
    def test_default_spec_loads(self):
        spec = att.load_spec()
        self.assertEqual(spec["version"], "0.1.0")
        self.assertEqual(spec["benchmark"], "mitigation_evidence")
        self.assertEqual(
            set(spec["artifact_classes"].keys()),
            {"fairness_aware_finetune", "output_filter_audit",
             "fairness_review_process"},
        )
        # UW-signed class weights (Decision 3, BEAAA-448 cmt 1d73afe6).
        weights = {k: v["weight"]
                   for k, v in spec["artifact_classes"].items()}
        self.assertEqual(weights["fairness_aware_finetune"], 0.4)
        self.assertEqual(weights["output_filter_audit"], 0.4)
        self.assertEqual(weights["fairness_review_process"], 0.2)
        # UW-signed tier-1 cap = raw 1.0 (cross-track invariant w/ P4).
        self.assertEqual(spec["evidence_tiers"][1]["raw_contribution"], 1.0)


class FloorTests(unittest.TestCase):
    """P2.6 emits a measured floor (raw=0) rather than N/A — see
    `applicability_policy.yaml#P2.6`."""

    def test_none_form_yields_floor(self):
        scored = att.score_attestation(None)
        self.assertEqual(scored["dim_status"], "ok")
        self.assertEqual(scored["dim_raw"], 0.0)
        self.assertEqual(scored["benchmark"],
                         att.BENCHMARK_LABEL)
        for entry in scored["per_class"].values():
            self.assertEqual(entry["claimed_tier"], 0)
            self.assertEqual(entry["effective_tier"], 0)
            self.assertEqual(entry["raw_contribution"], 0.0)
            self.assertIsNone(entry["downgrade_reason"])

    def test_empty_form_yields_floor(self):
        scored = att.score_attestation({})
        self.assertEqual(scored["dim_raw"], 0.0)

    def test_partial_form_unclaimed_class_floors(self):
        scored = att.score_attestation({
            "fairness_aware_finetune": {"evidence_tier": 1},
        })
        # Tier 1 on one class @ weight 0.4 -> 1.0 * 0.4 = 0.4
        self.assertAlmostEqual(scored["dim_raw"], 0.4, places=6)
        self.assertEqual(
            scored["per_class"]["output_filter_audit"]["effective_tier"], 0)
        self.assertEqual(
            scored["per_class"]["fairness_review_process"]["effective_tier"],
            0,
        )


class TierWeightingTests(unittest.TestCase):
    def test_tier_1_does_not_require_evidence_fields(self):
        # Tier 1 = "claimed without evidence" — required_fields not
        # enforced. No downgrade.
        scored = att.score_attestation({
            "output_filter_audit": {"evidence_tier": 1},
        })
        entry = scored["per_class"]["output_filter_audit"]
        self.assertEqual(entry["effective_tier"], 1)
        self.assertIsNone(entry["downgrade_reason"])
        # 1.0 * 0.4 = 0.4
        self.assertAlmostEqual(scored["dim_raw"], 0.4, places=6)

    def test_tier_3_full_form_scores_at_weighted_tier(self):
        # All three classes at tier 3 with full required fields.
        scored = att.score_attestation({
            "fairness_aware_finetune": {
                "evidence_tier": 3,
                "count_of_examples": 12000,
                "protected_axes_covered": ["name_ethnicity", "gender"],
                "artifact_pointer": "s3://bucket/preference-pairs.jsonl",
                "process_description": "Counterfactual preference pairs.",
            },
            "output_filter_audit": {
                "evidence_tier": 3,
                "sample_rate_pct": 1.0,
                "lookback_window_days": 30,
                "flag_resolution_sla": "7d",
                "artifact_pointer": "s3://bucket/audit-logs/",
            },
            "fairness_review_process": {
                "evidence_tier": 3,
                "cadence": "quarterly",
                "approver_role": "VP Engineering",
                "blocking_authority": True,
                "artifact_pointer": "https://wiki/fairness-review-v3",
            },
        })
        # 3.0 * (0.4 + 0.4 + 0.2) = 3.0
        self.assertAlmostEqual(scored["dim_raw"], 3.0, places=6)
        for entry in scored["per_class"].values():
            self.assertEqual(entry["effective_tier"], 3)
            self.assertIsNone(entry["downgrade_reason"])

    def test_max_score_tier_4_all_classes(self):
        # Bound check: dim_raw must be in [0, 4].
        scored = att.score_attestation({
            "fairness_aware_finetune": {
                "evidence_tier": 4,
                "count_of_examples": 50000,
                "protected_axes_covered": ["all"],
                "artifact_pointer": "s3://bucket/preference-pairs.jsonl",
                "process_description": "Third-party-audited preference data.",
            },
            "output_filter_audit": {
                "evidence_tier": 4,
                "sample_rate_pct": 2.5,
                "lookback_window_days": 90,
                "flag_resolution_sla": "24h",
                "artifact_pointer": "s3://bucket/audit-logs/",
            },
            "fairness_review_process": {
                "evidence_tier": 4,
                "cadence": "monthly",
                "approver_role": "CRO",
                "blocking_authority": True,
                "artifact_pointer": "https://wiki/fairness-review-v3",
            },
        })
        # 4.0 * (0.4 + 0.4 + 0.2) = 4.0
        self.assertAlmostEqual(scored["dim_raw"], 4.0, places=6)


class TamperResistanceTests(unittest.TestCase):
    """Tier-2+ claims missing required_fields must downgrade to tier 1
    with explicit reason — never silently inflate (BEAAA-200 P4 analog)."""

    def test_tier_2_claim_missing_fields_downgrades_to_tier_1(self):
        scored = att.score_attestation({
            "output_filter_audit": {"evidence_tier": 2},
        })
        entry = scored["per_class"]["output_filter_audit"]
        self.assertEqual(entry["claimed_tier"], 2)
        self.assertEqual(entry["effective_tier"], 1)
        self.assertEqual(entry["raw_contribution"], 1.0)
        self.assertIsNotNone(entry["downgrade_reason"])
        self.assertIn("missing", entry["downgrade_reason"])
        self.assertIn("BEAAA-200", entry["downgrade_reason"])
        # 1.0 * 0.4 = 0.4 (was-going-to-be 2.0 * 0.4 = 0.8)
        self.assertAlmostEqual(scored["dim_raw"], 0.4, places=6)

    def test_tier_3_with_empty_required_field_downgrades(self):
        scored = att.score_attestation({
            "fairness_aware_finetune": {
                "evidence_tier": 3,
                "count_of_examples": 5000,
                "protected_axes_covered": [],  # empty -> missing
                "artifact_pointer": "s3://bucket/",
                "process_description": "X",
            },
        })
        entry = scored["per_class"]["fairness_aware_finetune"]
        self.assertEqual(entry["effective_tier"], 1)
        self.assertIn("protected_axes_covered",
                      entry["downgrade_reason"])

    def test_unknown_tier_defaults_to_zero_with_reason(self):
        scored = att.score_attestation({
            "fairness_review_process": {"evidence_tier": 99},
        })
        entry = scored["per_class"]["fairness_review_process"]
        self.assertEqual(entry["effective_tier"], 0)
        self.assertEqual(entry["raw_contribution"], 0.0)
        self.assertIsNotNone(entry["downgrade_reason"])
        self.assertIn("unknown evidence_tier", entry["downgrade_reason"])

    def test_non_int_tier_defaults_to_zero(self):
        scored = att.score_attestation({
            "output_filter_audit": {"evidence_tier": "claimed"},
        })
        entry = scored["per_class"]["output_filter_audit"]
        self.assertEqual(entry["effective_tier"], 0)


class MeasurementShapeTests(unittest.TestCase):
    def test_to_measurement_contract(self):
        scored = att.score_attestation({
            "fairness_aware_finetune": {"evidence_tier": 1},
        })
        m = att.to_measurement(scored)
        self.assertEqual(set(m.keys()), {"dim_id", "raw", "benchmark"})
        self.assertEqual(m["dim_id"], "P2.6")
        self.assertEqual(m["benchmark"],
                         "mitigation_evidence_form_v0_1")
        self.assertIsInstance(m["raw"], float)


class DeterminismTests(unittest.TestCase):
    """Same form + same spec must yield byte-identical scores (BEAAA-21
    reproducibility band: identical, not within ±1)."""

    def test_same_form_yields_identical_score(self):
        form = {
            "fairness_aware_finetune": {
                "evidence_tier": 2,
                "count_of_examples": 1000,
                "protected_axes_covered": ["name_ethnicity"],
                "artifact_pointer": "s3://b/",
                "process_description": "Y",
            },
        }
        a = att.score_attestation(form)
        b = att.score_attestation(form)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
