"""BEAAA-130 rec #3 — sensitivity panel tests."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from report_card.render import render
from report_card.sensitivity import (
    PERTURBATION_PCT,
    REPRODUCIBILITY_BAND,
    compute_sensitivity,
    render_sensitivity_block,
)
from rubric.scoring import Rubric, score

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
FIXTURE = REPO / "tests" / "fixtures" / "synthetic_eval_outputs.json"


def _baseline():
    rubric = Rubric.load(RUBRIC_PATH)
    eo = json.loads(FIXTURE.read_text())
    out = score(
        rubric,
        eo,
        snapshot_hash="sha256:sens",
        forensic_bundle_uri="https://bundles.local/sens",
        scored_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )
    return out, rubric


def _flat_score(raw: float = 2.0):
    """Score with every dim raw set to the same value -> all pillar normalized equal."""
    rubric = Rubric.load(RUBRIC_PATH)
    eo = json.loads(FIXTURE.read_text())
    for did in list(eo.keys()):
        eo[did] = {"raw": raw, "evidence_uri": f"https://eval.local/{did}"}
    return score(
        rubric,
        eo,
        snapshot_hash="sha256:flat",
        forensic_bundle_uri="https://bundles.local/flat",
        scored_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
    ), rubric


class SensitivityComputeTests(unittest.TestCase):
    def test_eight_perturbations_for_four_pillars(self):
        out, _ = _baseline()
        sens = compute_sensitivity(out)
        self.assertEqual(len(sens["perturbations"]), 8)
        self.assertEqual(sens["summary"]["n_total"], 8)

    def test_alt_weights_sum_to_100(self):
        out, _ = _baseline()
        sens = compute_sensitivity(out)
        for p in sens["perturbations"]:
            total = sum(p["alt_weights"].values())
            self.assertAlmostEqual(total, 100.0, places=1)

    def test_perturbation_pct_constant(self):
        out, _ = _baseline()
        sens = compute_sensitivity(out)
        self.assertEqual(sens["perturbation_pct"], PERTURBATION_PCT)
        signs = sorted({p["delta_pct"] for p in sens["perturbations"]})
        self.assertEqual(signs, [-PERTURBATION_PCT, PERTURBATION_PCT])

    def test_target_pillar_weight_actually_perturbed(self):
        out, _ = _baseline()
        sens = compute_sensitivity(out)
        for p in sens["perturbations"]:
            target = p["pillar"]
            base_w = out["pillars"][target]["weight"]
            new_w = p["alt_weights"][target]
            expected = base_w * (1 + p["delta_pct"] / 100.0)
            self.assertAlmostEqual(new_w, expected, places=2)

    def test_other_pillars_rescaled_to_match_total(self):
        out, _ = _baseline()
        sens = compute_sensitivity(out)
        for p in sens["perturbations"]:
            target = p["pillar"]
            other_total = sum(w for pid, w in p["alt_weights"].items() if pid != target)
            self.assertAlmostEqual(other_total, 100 - p["alt_weights"][target], places=1)

    def test_flat_normalized_means_zero_bmi_delta(self):
        """If every pillar normalized score is identical, weight reshuffles
        cannot move BMI — the weighted average is invariant to weight."""
        out, _ = _flat_score(raw=2.0)
        sens = compute_sensitivity(out)
        for p in sens["perturbations"]:
            self.assertEqual(p["bmi_delta"], 0)
            self.assertFalse(p["letter_shift"])

    def test_summary_worst_delta_is_max_abs(self):
        out, _ = _baseline()
        sens = compute_sensitivity(out)
        deltas = [p["bmi_delta"] for p in sens["perturbations"]]
        worst = max(deltas, key=abs)
        self.assertEqual(sens["summary"]["worst_bmi_delta"], worst)

    def test_summary_counts_consistent(self):
        out, _ = _baseline()
        sens = compute_sensitivity(out)
        n_outside = sum(1 for p in sens["perturbations"] if abs(p["bmi_delta"]) > REPRODUCIBILITY_BAND)
        n_letter = sum(1 for p in sens["perturbations"] if p["letter_shift"])
        n_modifier_only = sum(
            1 for p in sens["perturbations"] if p["modifier_shift"] and not p["letter_shift"]
        )
        self.assertEqual(sens["summary"]["n_outside_reproducibility_band"], n_outside)
        self.assertEqual(sens["summary"]["n_letter_shift"], n_letter)
        self.assertEqual(sens["summary"]["n_modifier_only_shift"], n_modifier_only)

    def test_deterministic_repeated_calls(self):
        out, _ = _baseline()
        a = compute_sensitivity(out)
        b = compute_sensitivity(out)
        self.assertEqual(a, b)

    def test_does_not_mutate_input(self):
        out, _ = _baseline()
        before = json.dumps(out, sort_keys=True, default=str)
        compute_sensitivity(out)
        after = json.dumps(out, sort_keys=True, default=str)
        self.assertEqual(before, after)

    def test_alt_bmi_clamped_in_0_100(self):
        out, _ = _baseline()
        sens = compute_sensitivity(out)
        for p in sens["perturbations"]:
            self.assertGreaterEqual(p["alt_bmi"], 0)
            self.assertLessEqual(p["alt_bmi"], 100)


class SensitivityRenderBlockTests(unittest.TestCase):
    def test_block_contains_panel_title(self):
        out, _ = _baseline()
        block = render_sensitivity_block(out)
        self.assertIn("Weight Sensitivity", block)
        self.assertIn(f"\u00b1{PERTURBATION_PCT}%", block)

    def test_block_lists_all_eight_rows(self):
        out, _ = _baseline()
        block = render_sensitivity_block(out)
        for pid in out["pillars"]:
            self.assertIn(f"{pid} +{PERTURBATION_PCT}%", block)
            self.assertIn(f"{pid} -{PERTURBATION_PCT}%", block)

    def test_block_self_contained_no_external_assets(self):
        out, _ = _baseline()
        block = render_sensitivity_block(out)
        for needle in ("<link", "<script", "src="):
            self.assertNotIn(needle, block, f"sensitivity block must not include {needle!r}")

    def test_stable_verdict_when_flat(self):
        out, _ = _flat_score(raw=2.0)
        block = render_sensitivity_block(out)
        self.assertIn("decision is stable to weighting", block)


class SensitivityIntegrationTests(unittest.TestCase):
    def test_render_includes_sensitivity_section_by_default(self):
        out, rubric = _baseline()
        html = render(out, rubric)
        self.assertIn("Weight Sensitivity", html)
        self.assertIn("sens-block", html)

    def test_render_omits_sensitivity_when_disabled(self):
        out, rubric = _baseline()
        html = render(out, rubric, include_sensitivity=False)
        self.assertNotIn("Weight Sensitivity", html)
        self.assertNotIn("sens-block", html)

    def test_bmi_and_band_unchanged_after_sensitivity_added(self):
        """Ensure render with sensitivity does not swap reported BMI/band."""
        out, rubric = _baseline()
        html = render(out, rubric)
        self.assertIn(f"{out['bmi']}", html)
        self.assertIn(out["band"]["display"], html)

    def test_render_self_contained_with_sensitivity(self):
        out, rubric = _baseline()
        html = render(out, rubric)
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("</html>", html)
        for needle in ("<link", "<script"):
            self.assertNotIn(needle, html)


if __name__ == "__main__":
    unittest.main()
