"""BEAAA-209 Path B P1.6 eval-coverage-breadth aggregator tests."""
from __future__ import annotations

import unittest
from pathlib import Path

from harness.bench import eval_coverage_aggregator as agg
from rubric.scoring import Rubric

_REPO = Path(__file__).resolve().parent.parent
_RUBRIC_PATH = _REPO / "rubric" / "rubric.yaml"


def _load_rubric() -> Rubric:
    return Rubric.load(_RUBRIC_PATH)


class WeightSourceTests(unittest.TestCase):
    """Drift mitigation per BEAAA-209 Risks: weights MUST come from the
    same source as the rubric (no hardcoded constants)."""

    def test_in_scope_excludes_p1_6(self):
        rubric = _load_rubric()
        in_scope = agg.derive_in_scope_dims(rubric)
        self.assertNotIn("P1.6", in_scope)
        # v1.0 rubric: 21 active dims; in-scope = 20.
        self.assertEqual(len(in_scope), len(rubric.active_dim_ids) - 1)

    def test_weights_sum_to_pillars_minus_p1_6_share(self):
        """Per-dim weight = pillar_weight / count(active_dims_in_pillar).
        P1.6's share is dropped (not redistributed), mirroring the
        rubric.scoring divisor. So total = sum(pillar_weights) minus
        P1.6's would-have-been share in its own pillar.
        """
        rubric = _load_rubric()
        weights = agg.dim_weights_from_rubric(rubric)
        # P1.6 itself is excluded from the weights map.
        self.assertNotIn("P1.6", weights)
        p1_dim_count = len(rubric.pillars["P1"]["dim_ids"])  # 6
        p1_per_dim = float(rubric.pillars["P1"]["weight"]) / float(p1_dim_count)
        expected_total = sum(float(p["weight"]) for p in rubric.pillars.values()) - p1_per_dim
        self.assertAlmostEqual(sum(weights.values()), expected_total, places=6)

    def test_weights_equal_within_pillar(self):
        rubric = _load_rubric()
        weights = agg.dim_weights_from_rubric(rubric)
        for pid, pdata in rubric.pillars.items():
            sibling_ws = [weights[d] for d in pdata["dim_ids"] if d != "P1.6"]
            if not sibling_ws:
                continue
            for w in sibling_ws[1:]:
                self.assertAlmostEqual(w, sibling_ws[0], places=6)


class ClassifierTests(unittest.TestCase):

    def test_missing_dim_classified_missing(self):
        rubric = _load_rubric()
        in_scope = agg.derive_in_scope_dims(rubric)
        classified = agg.classify_eval_outputs({}, in_scope)
        for did in in_scope:
            self.assertEqual(classified[did], "missing")

    def test_measured_raw_in_range(self):
        in_scope = ["P1.1", "P1.2"]
        eo = {
            "P1.1": {"raw": 3.5, "evidence_uri": "s3://x"},
            "P1.2": {"raw": 0.0, "evidence_uri": "s3://y"},
        }
        self.assertEqual(
            agg.classify_eval_outputs(eo, in_scope),
            {"P1.1": "measured", "P1.2": "measured"},
        )

    def test_explicit_coverage_label_wins(self):
        in_scope = ["P1.5"]
        eo = {"P1.5": {"raw": 2.0, "coverage_label": "coverage-not-scored"}}
        self.assertEqual(
            agg.classify_eval_outputs(eo, in_scope),
            {"P1.5": "coverage-not-scored"},
        )

    def test_dim_status_inconclusive(self):
        in_scope = ["P2.3"]
        eo = {"P2.3": {"dim_status": "inconclusive"}}
        self.assertEqual(
            agg.classify_eval_outputs(eo, in_scope),
            {"P2.3": "inconclusive"},
        )

    def test_decline_to_attest_via_na(self):
        in_scope = ["P4.5"]
        eo = {"P4.5": {"applicable": False, "raw": None}}
        self.assertEqual(
            agg.classify_eval_outputs(eo, in_scope),
            {"P4.5": "decline-to-attest"},
        )

    def test_raw_out_of_range_falls_through_to_inconclusive(self):
        in_scope = ["P1.1"]
        eo = {"P1.1": {"raw": 99}}
        self.assertEqual(
            agg.classify_eval_outputs(eo, in_scope),
            {"P1.1": "inconclusive"},
        )


class CoverageMathTests(unittest.TestCase):

    def test_all_missing_yields_zero(self):
        rubric = _load_rubric()
        result = agg.run(rubric=rubric, dim_status={})
        self.assertEqual(result["coverage_breadth_pct"], 0.0)
        self.assertEqual(result["dim_raw"], 0.0)
        self.assertEqual(result["covered_dim_ids"], [])
        self.assertEqual(len(result["uncovered_dim_ids"]),
                         len(agg.derive_in_scope_dims(rubric)))

    def test_all_measured_yields_100_pct_and_raw_4(self):
        rubric = _load_rubric()
        in_scope = agg.derive_in_scope_dims(rubric)
        status = {d: "measured" for d in in_scope}
        result = agg.run(rubric=rubric, dim_status=status)
        self.assertAlmostEqual(result["coverage_breadth_pct"], 100.0, places=6)
        self.assertEqual(result["dim_raw"], 4.0)
        self.assertEqual(set(result["covered_dim_ids"]), set(in_scope))

    def test_p1_only_measured(self):
        """v1.0 weights: 25 per pillar. P1 has 6 dims; P1.6 excluded, so
        in-scope P1 = 5 dims. If all 5 measured and rest uncovered:
        covered_weight = 5 * (25/6); total_weight = 100 - (25/6).
        coverage_pct = 100 * (5*25/6) / (100 - 25/6).
        """
        rubric = _load_rubric()
        p1_in_scope = [d for d in rubric.pillars["P1"]["dim_ids"] if d != "P1.6"]
        status: dict[str, str] = {d: "measured" for d in p1_in_scope}
        result = agg.run(rubric=rubric, dim_status=status)
        per_dim = 25.0 / 6.0
        expected_pct = 100.0 * (5 * per_dim) / (100.0 - per_dim)
        self.assertAlmostEqual(
            result["coverage_breadth_pct"], expected_pct, places=4,
        )
        # ≈ 21.7% → raw 1.0 (cut at 20).
        self.assertEqual(result["dim_raw"], 1.0)

    def test_raw_mapping_cut_boundaries(self):
        """Each cut wins at exactly the boundary."""
        # Build a minimal fake rubric-equivalent by using dim_status on a
        # synthetic in_scope set with uniform weights — we drive the math
        # directly via the public function using rubric weights but
        # selecting subsets that land precisely on each boundary.
        rubric = _load_rubric()
        in_scope = agg.derive_in_scope_dims(rubric)
        weights = agg.dim_weights_from_rubric(rubric)
        total = sum(weights[d] for d in in_scope)
        # Build a set of measured dims whose weight sum lands ≥ each cut.
        for target_pct, expected_raw in [
            (80.0, 4.0), (60.0, 3.0), (40.0, 2.0), (20.0, 1.0), (0.0, 0.0),
        ]:
            need = target_pct / 100.0 * total
            measured: set[str] = set()
            running = 0.0
            for d in in_scope:
                if running >= need:
                    break
                measured.add(d)
                running += weights[d]
            status = {d: ("measured" if d in measured else "missing") for d in in_scope}
            r = agg.run(rubric=rubric, dim_status=status)
            self.assertGreaterEqual(
                r["coverage_breadth_pct"], target_pct - 1e-6,
                msg=f"target {target_pct}: got {r['coverage_breadth_pct']}",
            )
            self.assertEqual(
                r["dim_raw"], expected_raw,
                msg=f"target {target_pct}: pct {r['coverage_breadth_pct']} → raw {r['dim_raw']}",
            )

    def test_inconclusive_is_not_covered(self):
        rubric = _load_rubric()
        in_scope = agg.derive_in_scope_dims(rubric)
        # All measured except P1.1 inconclusive — still > 80%, but the
        # covered set excludes P1.1.
        status = {d: "measured" for d in in_scope}
        status["P1.1"] = "inconclusive"
        result = agg.run(rubric=rubric, dim_status=status)
        self.assertNotIn("P1.1", result["covered_dim_ids"])
        self.assertIn("P1.1", result["uncovered_dim_ids"])

    def test_decline_to_attest_is_not_covered(self):
        rubric = _load_rubric()
        in_scope = agg.derive_in_scope_dims(rubric)
        status = {d: "measured" for d in in_scope}
        status["P4.4"] = "decline-to-attest"
        result = agg.run(rubric=rubric, dim_status=status)
        self.assertNotIn("P4.4", result["covered_dim_ids"])


class ReproducibilityTests(unittest.TestCase):

    def test_byte_stable_repeated_runs(self):
        rubric = _load_rubric()
        in_scope = agg.derive_in_scope_dims(rubric)
        status = {d: "measured" for i, d in enumerate(in_scope) if i % 2}
        for d in in_scope:
            status.setdefault(d, "missing")
        r1 = agg.run(rubric=rubric, dim_status=status)
        r2 = agg.run(rubric=rubric, dim_status=status)
        self.assertEqual(r1, r2)

    def test_evidence_path_b_default(self):
        rubric = _load_rubric()
        in_scope = agg.derive_in_scope_dims(rubric)
        status = {d: "missing" for d in in_scope}
        r = agg.run(rubric=rubric, dim_status=status)
        self.assertEqual(r["evidence_path"], "B")
        self.assertEqual(r["algorithm_version"], "0.1.0")
        self.assertEqual(r["rubric_version"], rubric.version)

    def test_in_scope_set_circularity_guard(self):
        rubric = _load_rubric()
        with self.assertRaises(agg.AggregatorError):
            agg.run(rubric=rubric, dim_status={}, in_scope_dims=["P1.1", "P1.6"])

    def test_explicit_label_overrides_eval_outputs_heuristic(self):
        rubric = _load_rubric()
        in_scope = agg.derive_in_scope_dims(rubric)
        eo = {d: {"raw": 2.0} for d in in_scope}  # heuristic → all measured
        # Override one dim with an explicit inconclusive label.
        status_override = {"P3.1": "inconclusive"}
        result = agg.run(
            rubric=rubric,
            eval_outputs=eo,
            dim_status=status_override,
        )
        self.assertIn("P3.1", result["uncovered_dim_ids"])
        self.assertNotIn("P3.1", result["covered_dim_ids"])


class LabelGateTests(unittest.TestCase):
    """Spec doc §Report-card label gate contract."""

    def test_label_score_when_path_b_and_above_80(self):
        self.assertEqual(agg.label_for(85.0, "B"), "ARE Score")
        self.assertEqual(agg.label_for(80.0, "B"), "ARE Score")

    def test_label_spot_scan_when_below_80(self):
        self.assertEqual(agg.label_for(79.99, "B"), "ARE Spot-Scan")

    def test_label_spot_scan_when_path_c_regardless(self):
        # Per spec: Path C scans always render as Spot-Scan regardless of
        # ledger size (attestation evidence is governance, not measurement).
        self.assertEqual(agg.label_for(99.0, "C"), "ARE Spot-Scan")
        self.assertEqual(agg.label_for(100.0, "C"), "ARE Spot-Scan")


if __name__ == "__main__":
    unittest.main()
