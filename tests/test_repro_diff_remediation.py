"""BEAAA-130 rec D+E+F tests — reproducibility band, scan diff, remediation playbook."""
from __future__ import annotations

import json
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from report_card.diff import compute_diff, render_dim_delta_pill
from report_card.render import render
from report_card.reproducibility import compute_reproducibility
from rubric.scoring import Rubric, score

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
FIXTURE = REPO / "tests" / "fixtures" / "synthetic_eval_outputs.json"


def _baseline_score(seed_offset: float = 0.0) -> dict:
    rubric = Rubric.load(RUBRIC_PATH)
    eo = {k: v for k, v in json.loads(FIXTURE.read_text()).items() if isinstance(v, dict)}
    if seed_offset:
        for did, entry in eo.items():
            entry["raw"] = max(0.0, min(4.0, float(entry["raw"]) + seed_offset))
    return score(
        rubric,
        eo,
        snapshot_hash=f"sha256:def-{seed_offset}",
        forensic_bundle_uri=f"https://bundles.local/def/{seed_offset}",
        scored_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )


def _baseline_with_rubric():
    rubric = Rubric.load(RUBRIC_PATH)
    eo = json.loads(FIXTURE.read_text())
    out = score(
        rubric,
        eo,
        snapshot_hash="sha256:def-base",
        forensic_bundle_uri="https://bundles.local/def-base",
        scored_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )
    return out, rubric


class ReproducibilityComputeTests(unittest.TestCase):
    def test_requires_at_least_two_replicates(self):
        with self.assertRaises(ValueError):
            compute_reproducibility([_baseline_score()])

    def test_three_identical_replicates_yields_zero_band(self):
        rep = [_baseline_score() for _ in range(3)]
        repro = compute_reproducibility(rep)
        self.assertEqual(repro["n_replicates"], 3)
        self.assertEqual(repro["bmi"]["min"], repro["bmi"]["max"])
        self.assertEqual(repro["bmi"]["stdev"], 0.0)

    def test_replicates_with_noise_produce_band(self):
        rep = [
            _baseline_score(),
            _baseline_score(seed_offset=+0.2),
            _baseline_score(seed_offset=-0.2),
        ]
        repro = compute_reproducibility(rep)
        self.assertGreaterEqual(repro["bmi"]["max"], repro["bmi"]["min"])
        # at least one dim should show stdev > 0
        had_noise = any(s.get("stdev", 0) > 0 for s in repro["dims"].values())
        self.assertTrue(had_noise)

    def test_dim_stats_keys_present(self):
        rep = [_baseline_score(), _baseline_score(seed_offset=+0.3)]
        repro = compute_reproducibility(rep)
        sample = next(iter(repro["dims"].values()))
        for key in ("median", "p2_5", "p97_5", "stdev", "min", "max"):
            self.assertIn(key, sample)


class ScoreComparisonLifecycleTests(unittest.TestCase):
    def test_development_mode_suppresses_report_comparison(self):
        current, rubric = _baseline_with_rubric()
        prior = _baseline_score(seed_offset=-0.2)

        html = render(
            current,
            rubric,
            prior_score_response=prior,
            lifecycle_mode="development",
        )

        self.assertNotIn("Change Since Last Scan", html)
        self.assertNotIn("dim-delta-pill", html)

    def test_production_without_baseline_suppresses_report_comparison(self):
        current, rubric = _baseline_with_rubric()
        prior = _baseline_score(seed_offset=-0.2)

        html = render(
            current,
            rubric,
            prior_score_response=prior,
            lifecycle_mode="production",
            comparison_baseline_exists=False,
        )

        self.assertNotIn("Change Since Last Scan", html)

    def test_production_with_baseline_renders_report_comparison(self):
        current, rubric = _baseline_with_rubric()
        prior = _baseline_score(seed_offset=-0.2)

        html = render(
            current,
            rubric,
            prior_score_response=prior,
            lifecycle_mode="production",
            comparison_baseline_exists=True,
        )

        self.assertIn("Change Since Last Scan", html)

    def test_high_noise_dims_capped_at_three(self):
        rep = [
            _baseline_score(),
            _baseline_score(seed_offset=+0.5),
            _baseline_score(seed_offset=-0.5),
        ]
        repro = compute_reproducibility(rep)
        self.assertLessEqual(len(repro["high_noise_dims"]), 3)


class ReproducibilityRenderTests(unittest.TestCase):
    def test_block_renders_when_supplied(self):
        out, rubric = _baseline_with_rubric()
        rep = [out, _baseline_score(seed_offset=+0.2)]
        repro = compute_reproducibility(rep)
        html = render(out, rubric, reproducibility=repro)
        self.assertIn("Reproducibility (n=2)", html)
        self.assertIn("repro-block", html)

    def test_block_omitted_when_none(self):
        out, rubric = _baseline_with_rubric()
        html = render(out, rubric)
        self.assertNotIn('<div class="repro-block">', html)


class ScanDiffComputeTests(unittest.TestCase):
    def test_zero_diff_when_identical_responses(self):
        cur = _baseline_score()
        prior = _baseline_score()
        diff = compute_diff(cur, prior)
        self.assertEqual(diff["bmi_delta"], 0)
        self.assertFalse(diff["band_letter_changed"])
        for d in diff["dim_deltas"].values():
            self.assertAlmostEqual(d["delta"], 0.0)

    def test_positive_drift_surfaces_in_movers(self):
        cur = _baseline_score(seed_offset=+0.5)
        prior = _baseline_score()
        diff = compute_diff(cur, prior)
        self.assertGreaterEqual(diff["bmi_delta"], 0)
        self.assertTrue(diff["movers"])
        # Movers sorted descending by abs delta
        deltas = [abs(d) for _, d in diff["movers"]]
        self.assertEqual(deltas, sorted(deltas, reverse=True))

    def test_band_letter_change_flagged(self):
        prior = _baseline_score(seed_offset=-1.5)  # lower ARE
        cur = _baseline_score(seed_offset=+1.5)    # higher ARE
        diff = compute_diff(cur, prior)
        # Likely letter change between low and high; at minimum modifier change
        self.assertTrue(
            diff["band_letter_changed"] or diff["band_modifier_changed"]
        )


class ScanDiffRenderTests(unittest.TestCase):
    def test_block_renders_when_prior_supplied(self):
        out, rubric = _baseline_with_rubric()
        prior = _baseline_score(seed_offset=-0.4)
        html = render(out, rubric, prior_score_response=prior)
        self.assertIn("Change Since Last Scan", html)
        self.assertIn('<div class="diff-block">', html)
        self.assertIn("\u0394ARE", html)

    def test_block_omitted_when_no_prior(self):
        out, rubric = _baseline_with_rubric()
        html = render(out, rubric)
        self.assertNotIn('<div class="diff-block">', html)

    def test_dim_delta_pill_renders_inline(self):
        out, rubric = _baseline_with_rubric()
        prior = _baseline_score(seed_offset=-0.5)
        html = render(out, rubric, prior_score_response=prior)
        self.assertIn("dim-delta-pill", html)
        # at least some dim should show a positive delta marker
        self.assertIn("dim-delta-pos", html)

    def test_dim_delta_pill_helper_returns_empty_for_no_diff(self):
        self.assertEqual(render_dim_delta_pill(None, "P3.1"), "")
        self.assertEqual(render_dim_delta_pill({"dim_deltas": {}}, "P3.1"), "")


class RemediationLoadTests(unittest.TestCase):
    def test_p3_1_remediation_loaded(self):
        _, rubric = _baseline_with_rubric()
        rem = rubric.dim_remediation.get("P3.1")
        self.assertTrue(rem, "P3.1 remediation should be present")
        self.assertGreaterEqual(len(rem["mitigations"]), 3)
        names = [m["name"] for m in rem["mitigations"]]
        self.assertTrue(any("System-prompt" in n for n in names))

    def test_p3_2_p3_3_p1_1_remediation_loaded(self):
        _, rubric = _baseline_with_rubric()
        for did in ("P1.1", "P3.2", "P3.3"):
            self.assertIn(did, rubric.dim_remediation)


class RemediationRenderTests(unittest.TestCase):
    def test_remediation_renders_for_below_threshold_dim(self):
        out, rubric = _baseline_with_rubric()
        # Synthetic baseline has P3.1 raw=0.5 (below default 2.5 threshold)
        html = render(out, rubric, remediation_threshold=2.5)
        self.assertIn("rem-block", html)
        self.assertIn("Recommended mitigations", html)
        self.assertIn("System-prompt hardening", html)

    def test_remediation_omitted_when_threshold_at_zero(self):
        out, rubric = _baseline_with_rubric()
        html = render(out, rubric, remediation_threshold=0.0)
        # Synthetic raws are all >= 0, so no dim qualifies
        self.assertNotIn('class="rem-block"', html)

    def test_remediation_only_drilldown_works_without_records(self):
        out, rubric = _baseline_with_rubric()
        html = render(out, rubric)
        # P3.1 raw=0.5 -> remediation only -> drilldown labeled "Recommended mitigations"
        self.assertIn("Recommended mitigations (P3.1)", html)


class IntegrationTests(unittest.TestCase):
    def test_combined_d_e_f_renders_self_contained(self):
        out, rubric = _baseline_with_rubric()
        prior = _baseline_score(seed_offset=-0.3)
        rep = [out, _baseline_score(seed_offset=+0.2), _baseline_score(seed_offset=-0.2)]
        repro = compute_reproducibility(rep)
        html = render(
            out,
            rubric,
            reproducibility=repro,
            prior_score_response=prior,
            probe_records={"P3.1": [
                {"item_id": "demo-1", "prompt": "test", "response": "ok",
                 "verdict": "fail"}
            ]},
        )
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("Reproducibility", html)
        self.assertIn("Change Since Last Scan", html)
        self.assertIn("rem-block", html)
        for needle in ("<script", "<link "):
            self.assertNotIn(needle, html)

    def test_bmi_unchanged_with_full_render(self):
        out, rubric = _baseline_with_rubric()
        prior = _baseline_score(seed_offset=-0.3)
        rep = [out, _baseline_score(seed_offset=+0.2)]
        repro = compute_reproducibility(rep)
        before = deepcopy(out)
        html = render(out, rubric, reproducibility=repro, prior_score_response=prior)
        self.assertEqual(out, before)
        self.assertIn(str(out["bmi"]), html)


if __name__ == "__main__":
    unittest.main()
