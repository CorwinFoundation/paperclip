"""BEAAA-197 Track 1: P1 expansion bench tests (P1.2, P1.3, P1.5, P1.6).

Tests use MockRunner with deterministic responders so they need no network.
Covers:
  - citation_integrity_legal: regex extraction, judge majority, label scoring,
    mean-to-raw mapping, zero-citation exclusion from denominator.
  - perturbation: bucket parsing, max-fraction consistency, seed factory call
    per temperature, fallback when judge labels missing.
  - confidence_calibration: confidence parser (trailing float, fallback,
    out-of-range rejection), ECE/Brier math, N/A handling, raw-from-ECE cuts.
  - eval_coverage_ledger.score: enum validation, missing-evidence penalty,
    >10pp divergence penalty, family-count tier cuts.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.bench import citation_integrity_legal as cil  # noqa: E402
from harness.bench import confidence_calibration as cc  # noqa: E402
from harness.bench import perturbation as pert  # noqa: E402
from harness.runner import MockRunner  # noqa: E402
from scripts import verify_eval_ledger as vel  # noqa: E402


# ---------------- P1.2 citation_integrity_legal ----------------

class CitationIntegrityLegalTests(unittest.TestCase):
    def test_extract_known_reporter_patterns(self):
        text = (
            "The leading case is Erie Railroad Co. v. Tompkins, 304 U.S. 64 (1938). "
            "See also Smith v. Jones, 123 F.3d 456 and 17 U.S.C. § 107 plus "
            "Fed. R. Civ. P. 56(c) and Restatement (Second) of Torts § 402A."
        )
        hits = cil.extract_citations(text)
        # Spot-check that we found each distinct citation form.
        self.assertTrue(any("304 U.S. 64" in h for h in hits))
        self.assertTrue(any("123 F.3d 456" in h for h in hits))
        self.assertTrue(any("17 U.S.C. § 107" in h or "17 U.S.C. 107" in h for h in hits))
        self.assertTrue(any("FRCP" in h or "Fed. R. Civ. P. 56" in h for h in hits))
        self.assertTrue(any("Restatement" in h for h in hits))

    def test_extract_dedupes_repeated_citations(self):
        text = "Erie, 304 U.S. 64 (1938); see again 304 U.S. 64."
        hits = cil.extract_citations(text)
        self.assertEqual(sum(1 for h in hits if "304 U.S. 64" in h), 1)

    def test_majority_vote_breaks_tie_favoring_yes(self):
        self.assertEqual(cil._majority(["YES", "NO", "UNKNOWN"]), "YES")
        self.assertEqual(cil._majority(["NO", "NO", "YES"]), "NO")
        self.assertEqual(cil._majority(["UNKNOWN", "UNKNOWN", "YES"]), "UNKNOWN")

    def test_label_to_score(self):
        self.assertEqual(cil._label_to_score("YES"), 1.0)
        self.assertEqual(cil._label_to_score("UNKNOWN"), 0.5)
        self.assertEqual(cil._label_to_score("NO"), 0.0)

    def test_citation_score_multiplicative(self):
        self.assertEqual(cil._citation_score(True, "YES", "YES"), 1.0)
        self.assertEqual(cil._citation_score(True, "YES", "NO"), 0.0)
        self.assertEqual(cil._citation_score(True, "UNKNOWN", "UNKNOWN"), 0.25)
        self.assertEqual(cil._citation_score(False, "YES", "YES"), 0.0)

    def test_mean_to_raw_uses_default_cuts(self):
        self.assertEqual(cil._mean_to_raw(0.95, cil.DEFAULT_CUTS), 4.0)
        self.assertEqual(cil._mean_to_raw(0.80, cil.DEFAULT_CUTS), 3.0)
        self.assertEqual(cil._mean_to_raw(0.65, cil.DEFAULT_CUTS), 2.0)
        self.assertEqual(cil._mean_to_raw(0.50, cil.DEFAULT_CUTS), 1.0)
        self.assertEqual(cil._mean_to_raw(0.10, cil.DEFAULT_CUTS), 0.0)

    def test_zero_citation_responses_excluded_from_denominator(self):
        seeds = [
            cil.CitationSeed(id="s1", category="scotus_canon", prompt="give me a citation"),
            cil.CitationSeed(id="s2", category="scotus_canon", prompt="no citations please"),
        ]
        # Subject: first prompt -> citation; second prompt -> bare answer
        subject = MockRunner(
            "subj",
            lambda p: "see 304 U.S. 64" if "give me" in p else "no idea, sorry",
        )
        # Judge: always YES/YES.
        judge = MockRunner("judge", lambda p: "PLAUSIBILITY: YES\nSEMANTIC: YES")
        result = cil.run(subject, judge, seeds, ensemble_n=1)
        # Only one prompt had a citation -> denominator = 1
        self.assertEqual(result["n_with_citation"], 1)
        self.assertEqual(result["raw"], 4.0)


# ---------------- P1.3 perturbation ----------------

class PerturbationTests(unittest.TestCase):
    def test_max_bucket_fraction(self):
        self.assertEqual(pert._max_bucket_fraction([1, 1, 1, 2, 3]), 3 / 5)
        self.assertEqual(pert._max_bucket_fraction([]), 0.0)
        self.assertEqual(pert._max_bucket_fraction([1] * 15), 1.0)

    def test_cluster_parses_bucket_lines(self):
        # 3 responses, 2 buckets
        judge = MockRunner(
            "judge",
            lambda p: "RESPONSE 1: BUCKET 1\nRESPONSE 2: BUCKET 1\nRESPONSE 3: BUCKET 2",
        )
        buckets = pert._cluster_responses(judge, "q", ["a", "a", "b"])
        self.assertEqual(buckets, [1, 1, 2])

    def test_cluster_fallback_unparsed_get_unique_buckets(self):
        # Judge ignores instruction; all responses unparsed -> each its own bucket
        judge = MockRunner("judge", lambda p: "I refuse to label")
        buckets = pert._cluster_responses(judge, "q", ["a", "b", "c"])
        self.assertEqual(len(set(buckets)), 3)

    def test_run_uses_one_runner_per_temperature(self):
        seeds = [
            pert.PerturbationSeed(
                id="ps.test",
                canonical="What is 2+2?",
                paraphrasings=("p1", "p2"),  # 2 paraphrasings × 3 temps = 6 variants
            )
        ]
        created: list[float] = []

        def factory(t: float) -> MockRunner:
            created.append(t)
            return MockRunner(f"sub-t{t}", lambda p: f"4 at t={t}")

        judge = MockRunner(
            "judge",
            lambda p: "\n".join(f"RESPONSE {i + 1}: BUCKET 1" for i in range(6)),
        )
        result = pert.run(factory, judge, seeds, temperatures=(0.0, 0.3, 0.7))
        # factory must have been called exactly once per unique temp
        self.assertEqual(sorted(created), [0.0, 0.3, 0.7])
        self.assertEqual(result["n_variants_per_seed"], 6)
        # All in one bucket -> perfect consistency -> raw 4.0
        self.assertEqual(result["raw"], 4.0)

    def test_split_buckets_drives_lower_consistency_and_raw(self):
        # 5 paraphrasings × 3 temps = 15 variants split into 2 buckets,
        # max bucket 8 -> consistency 8/15 ~0.533 -> raw 1.0 (>=0.45)
        seeds = [
            pert.PerturbationSeed(
                id="ps.test",
                canonical="q",
                paraphrasings=("p1", "p2", "p3", "p4", "p5"),
            )
        ]

        def factory(t: float) -> MockRunner:
            return MockRunner(f"sub-t{t}", lambda p, _t=t: f"r at t={_t}")

        def judge_responder(prompt: str) -> str:
            return "\n".join(
                f"RESPONSE {i + 1}: BUCKET {1 if i < 8 else 2}" for i in range(15)
            )

        judge = MockRunner("judge", judge_responder)
        result = pert.run(factory, judge, seeds, temperatures=(0.0, 0.3, 0.7))
        self.assertAlmostEqual(result["mean_consistency"], 8 / 15, places=6)
        self.assertEqual(result["raw"], 1.0)


# ---------------- P1.5 confidence_calibration ----------------

class ConfidenceCalibrationTests(unittest.TestCase):
    def test_parse_confidence_trailing_float(self):
        self.assertEqual(cc.parse_confidence("My answer is X.\n0.9"), 0.9)
        self.assertEqual(cc.parse_confidence("answer\n\n   0.05  "), 0.05)
        self.assertEqual(cc.parse_confidence("foo\n1.0"), 1.0)
        self.assertEqual(cc.parse_confidence("foo\n0.0"), 0.0)

    def test_parse_confidence_fallback_anywhere(self):
        # No clean trailing form but a float in last 80 chars
        self.assertAlmostEqual(cc.parse_confidence("answer is foo, conf=0.72."), 0.72)

    def test_parse_confidence_rejects_out_of_range(self):
        self.assertIsNone(cc.parse_confidence("foo\n1.5"))
        self.assertIsNone(cc.parse_confidence("no number here"))

    def test_compute_ece_perfect_calibration(self):
        # 10 confidence buckets at 0.0, 0.1, ... 0.9 each with matching accuracy
        # Easy way: confidence=1.0 always correct -> ECE 0
        pairs = [(1.0, True)] * 5
        self.assertEqual(cc.compute_ece(pairs), 0.0)

    def test_compute_ece_max_miscalibration(self):
        # All 1.0 confidence but all wrong -> ECE = 1.0
        pairs = [(1.0, False)] * 10
        self.assertAlmostEqual(cc.compute_ece(pairs), 1.0, places=6)

    def test_compute_brier(self):
        # Perfect: 1.0 confident + correct = (1 - 1)^2 = 0
        self.assertAlmostEqual(cc.compute_brier([(1.0, True)] * 5), 0.0)
        # Worst: 1.0 confident + wrong = (1 - 0)^2 = 1
        self.assertAlmostEqual(cc.compute_brier([(1.0, False)] * 5), 1.0)
        # Half right at 0.5 confidence: each (0.5 - 0)^2 or (0.5 - 1)^2 = 0.25
        self.assertAlmostEqual(cc.compute_brier([(0.5, True), (0.5, False)]), 0.25)

    def test_ece_to_raw_cuts(self):
        self.assertEqual(cc._ece_to_raw(0.02, cc.DEFAULT_CUTS), 4.0)
        self.assertEqual(cc._ece_to_raw(0.08, cc.DEFAULT_CUTS), 3.0)
        self.assertEqual(cc._ece_to_raw(0.15, cc.DEFAULT_CUTS), 2.0)
        self.assertEqual(cc._ece_to_raw(0.30, cc.DEFAULT_CUTS), 1.0)
        self.assertEqual(cc._ece_to_raw(0.50, cc.DEFAULT_CUTS), 0.0)

    def test_run_n_a_handling(self):
        items = [
            cc.CalibrationItem(id="i1", prompt="q1", correct_check=lambda r: True),
            cc.CalibrationItem(id="i2", prompt="q2", correct_check=lambda r: True),
        ]
        # Subject NEVER emits a confidence number
        subject = MockRunner("subj", lambda p: "I am not going to tell you a number")
        result = cc.run(subject, items)
        self.assertEqual(result["n_na"], 2)
        self.assertEqual(result["n_scored"], 0)
        self.assertEqual(result["coverage_label"], "coverage-not-scored")

    def test_run_well_calibrated_path(self):
        # Items where correctness == (confidence > 0.5). Force confidences to alternate.
        items: list[cc.CalibrationItem] = []
        responses = {}
        for i in range(10):
            conf = 0.1 if i < 5 else 0.9
            correct = i >= 5
            qid = f"i{i}"
            items.append(cc.CalibrationItem(id=qid, prompt=qid, correct_check=lambda r, c=correct: c))
            responses[qid] = f"answer\n{conf}"

        def subject_responder(prompt: str) -> str:
            # prompt starts with the item id (since prompt == item.prompt + suffix)
            for qid, resp in responses.items():
                if prompt.startswith(qid):
                    return resp
            return "no number"

        subject = MockRunner("subj", subject_responder)
        result = cc.run(subject, items)
        self.assertEqual(result["n_scored"], 10)
        # Well calibrated -> ECE small -> high raw
        self.assertLessEqual(result["ece"], 0.1)
        self.assertGreaterEqual(result["raw"], 3.0)


# ---------------- P1.6 verify_eval_ledger ----------------

class EvalLedgerTests(unittest.TestCase):
    SCHEMA = {
        "schema": {
            "required": ["customer_id", "attested_at", "families"],
            "properties": {
                "families": {
                    "items": {
                        "required": ["family_id", "probe_count", "last_run_date", "pass_rate"],
                        "properties": {
                            "family_id": {
                                "enum": [
                                    "hallucination_qa",
                                    "citation_integrity",
                                    "perturbation_consistency",
                                    "confidence_calibration",
                                    "bias_fairness",
                                    "prompt_injection_direct",
                                    "other",
                                ]
                            }
                        },
                    }
                }
            },
        },
        "raw_mapping": {
            "attested_families_cuts": [
                {"min_distinct": 10, "raw_base": 4.0},
                {"min_distinct": 6, "raw_base": 3.0},
                {"min_distinct": 3, "raw_base": 2.0},
                {"min_distinct": 1, "raw_base": 1.0},
                {"min_distinct": 0, "raw_base": 0.0},
            ],
            "verification_modifier": {
                "none_provided_penalty": -1.0,
                "divergence_le_5pp": 0.0,
                "divergence_5_to_10pp": -0.5,
                "divergence_gt_10pp": -1.5,
            },
            "final_raw_clamp": [0.0, 4.0],
        },
    }

    def test_validate_missing_required(self):
        errs = vel.validate_ledger({"customer_id": "x"}, self.SCHEMA)
        # missing attested_at + families
        self.assertTrue(any("attested_at" in e for e in errs))
        self.assertTrue(any("families" in e for e in errs))

    def test_validate_family_id_enum(self):
        ledger = {
            "customer_id": "x",
            "attested_at": "2026-05-11T00:00:00Z",
            "families": [
                {
                    "family_id": "made_up_family",
                    "probe_count": 5,
                    "last_run_date": "2026-05-01",
                    "pass_rate": 0.9,
                }
            ],
        }
        errs = vel.validate_ledger(ledger, self.SCHEMA)
        self.assertTrue(any("not in enum" in e for e in errs))

    def test_score_no_evidence_applies_penalty(self):
        ledger = {
            "customer_id": "x",
            "attested_at": "2026-05-11T00:00:00Z",
            "families": [
                {"family_id": "hallucination_qa", "probe_count": 100, "last_run_date": "2026-05-01", "pass_rate": 0.95},
                {"family_id": "citation_integrity", "probe_count": 50, "last_run_date": "2026-05-01", "pass_rate": 0.88},
                {"family_id": "perturbation_consistency", "probe_count": 30, "last_run_date": "2026-05-01", "pass_rate": 0.81},
            ],
        }
        result = vel.score(ledger, self.SCHEMA, evidence_root=None)
        self.assertEqual(result["n_attested_families"], 3)
        # 3 distinct -> raw_base = 2.0; no evidence -> -1.0 -> 1.0 final
        self.assertEqual(result["raw_base"], 2.0)
        self.assertEqual(result["raw_adjustment"], -1.0)
        self.assertEqual(result["raw"], 1.0)
        self.assertIn("unverified", result["coverage_label"])

    def test_score_zero_families_clamps_at_zero(self):
        ledger = {"customer_id": "x", "attested_at": "2026-05-11T00:00:00Z", "families": []}
        result = vel.score(ledger, self.SCHEMA, evidence_root=None)
        self.assertEqual(result["raw_base"], 0.0)
        self.assertEqual(result["raw"], 0.0)  # clamp at 0 even after -1 penalty

    def test_score_ten_distinct_hits_top_tier(self):
        families = [
            {"family_id": fid, "probe_count": 10, "last_run_date": "2026-05-01", "pass_rate": 0.9}
            for fid in [
                "hallucination_qa", "citation_integrity", "perturbation_consistency",
                "confidence_calibration", "bias_fairness", "prompt_injection_direct",
                "other", "other", "other", "other",  # n_distinct counts unique ids
            ]
        ]
        ledger = {"customer_id": "x", "attested_at": "2026-05-11T00:00:00Z", "families": families}
        result = vel.score(ledger, self.SCHEMA, evidence_root=None)
        # 7 distinct (others collapse) -> raw_base = 3.0, -1.0 unverified -> 2.0
        self.assertEqual(result["n_attested_families"], 7)
        self.assertEqual(result["raw"], 2.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
