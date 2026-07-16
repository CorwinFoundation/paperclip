"""Synthetic-input tests for the v0.1 scoring engine.

Uses stdlib unittest (no pytest dep) so it runs in any environment with PyYAML.
Run from the project root: `python3 -m unittest discover -v tests`
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from rubric.scoring import Rubric, ScoringError, score

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
FIXTURE = REPO / "tests" / "fixtures" / "synthetic_eval_outputs.json"


def _flat_eval(rubric: Rubric, raw: float) -> dict:
    return {
        did: {"raw": raw, "evidence_uri": f"https://evidence.local/{did}"}
        for did in rubric.active_dim_ids
    }


class RubricLoadTests(unittest.TestCase):
    def test_loads_and_validates_weights(self):
        r = Rubric.load(RUBRIC_PATH)
        self.assertEqual(sum(p["weight"] for p in r.pillars.values()), 100)

    def test_active_dim_count_is_27(self):
        r = Rubric.load(RUBRIC_PATH)
        self.assertEqual(len(r.active_dim_ids), 27)
        self.assertEqual(len(set(r.active_dim_ids)), 27)


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)
        self.kwargs = dict(
            snapshot_hash="sha256:test",
            forensic_bundle_uri="https://bundles.local/test",
            scored_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
            corpus_version="test@0.0.0",
        )

    def test_score_emits_are_total_canonical_key(self):
        # BEAAA-5275 / A-001: score() must emit "are_total" as the canonical
        # contract key (are-scanner-api-v1@v1.3.0 §Output). "bmi" is the
        # one-release deprecation shim and must equal are_total.
        out = score(self.rubric, _flat_eval(self.rubric, 2.0), **self.kwargs)
        self.assertIn("are_total", out)
        self.assertEqual(out["are_total"], out["bmi"])

    def test_perfect_score_yields_bmi_100(self):
        out = score(self.rubric, _flat_eval(self.rubric, 4.0), **self.kwargs)
        self.assertEqual(out["bmi"], 100)
        for pid, p in out["pillars"].items():
            self.assertEqual(p["score"], p["weight"], f"{pid} not at full weight")

    def test_zero_score_yields_bmi_0(self):
        out = score(self.rubric, _flat_eval(self.rubric, 0.0), **self.kwargs)
        self.assertEqual(out["bmi"], 0)

    def test_midpoint_score_yields_bmi_50(self):
        out = score(self.rubric, _flat_eval(self.rubric, 2.0), **self.kwargs)
        self.assertEqual(out["bmi"], 50)

    def test_response_includes_red_line_fields(self):
        out = score(self.rubric, _flat_eval(self.rubric, 2.0), **self.kwargs)
        self.assertEqual(out["snapshot_hash"], "sha256:test")
        self.assertEqual(out["forensic_bundle_uri"], "https://bundles.local/test")
        self.assertEqual(out["rubric_version"], self.rubric.version)
        self.assertEqual(set(out["pillars"]), {"OPS", "BIAS_FAIRNESS", "ROBUSTNESS", "GOVERNANCE"})
        self.assertEqual(out["band"]["display"], "D+")  # ARE 50 = D+
        self.assertEqual(out["band"]["bands_version"], "1.0")

    def test_response_surfaces_rubric_locked_through(self):
        # BEAAA-11930: the scan-output envelope must self-describe lock
        # currency. rubric_locked_through mirrors the pinned rubric lock
        # horizon so a downstream reader (Underwriter/auditor) can compare it
        # against run_timestamp WITHOUT re-reading rubric/rubric.yaml.
        out = score(self.rubric, _flat_eval(self.rubric, 2.0), **self.kwargs)
        self.assertIn("rubric_locked_through", out)
        self.assertEqual(out["rubric_locked_through"], self.rubric.locked_through)
        # Distinct from rubric_version (the two are not aliased).
        self.assertNotEqual(out["rubric_locked_through"], out["rubric_version"])

    def test_pass_rate_curve_fallback_surfaces_in_diagnostics(self):
        eo = _flat_eval(self.rubric, 2.0)
        eo["P1.2"] = {"pass_rate": 0.95, "evidence_uri": "https://evidence.local/P1.2"}
        original = self.rubric.dim_calibration_curves["P1.2"]
        self.rubric.dim_calibration_curves["P1.2"] = "CURVE_DOES_NOT_EXIST"
        try:
            out = score(self.rubric, eo, **self.kwargs)
        finally:
            self.rubric.dim_calibration_curves["P1.2"] = original

        diagnostics = out["diagnostics"]
        fallback = [
            d for d in diagnostics
            if d["code"] == "calibration_curve_fallback"
        ]
        self.assertEqual(len(fallback), 1)
        self.assertEqual(fallback[0]["detail"]["dim_id"], "P1.2")
        self.assertEqual(
            fallback[0]["detail"]["requested_curve_id"],
            "CURVE_DOES_NOT_EXIST",
        )
        self.assertEqual(
            fallback[0]["detail"]["fallback_curve_id"],
            "CURVE_RUNNER_V1",
        )

    def test_missing_snapshot_hash_raises(self):
        with self.assertRaisesRegex(ScoringError, "snapshot_hash"):
            score(
                self.rubric,
                _flat_eval(self.rubric, 2.0),
                snapshot_hash="",
                forensic_bundle_uri="https://x",
            )

    def test_missing_forensic_bundle_uri_raises(self):
        with self.assertRaisesRegex(ScoringError, "forensic_bundle_uri"):
            score(
                self.rubric,
                _flat_eval(self.rubric, 2.0),
                snapshot_hash="sha256:x",
                forensic_bundle_uri="",
            )

    def test_missing_dim_raises(self):
        eo = _flat_eval(self.rubric, 2.0)
        eo.pop("P1.1")
        with self.assertRaisesRegex(ScoringError, "missing dim"):
            score(self.rubric, eo, **self.kwargs)

    def test_out_of_range_raw_raises(self):
        eo = _flat_eval(self.rubric, 2.0)
        eo["P1.1"]["raw"] = 5.0
        with self.assertRaisesRegex(ScoringError, "out of"):
            score(self.rubric, eo, **self.kwargs)

    def test_negative_raw_raises(self):
        eo = _flat_eval(self.rubric, 2.0)
        eo["P1.1"]["raw"] = -0.1
        with self.assertRaisesRegex(ScoringError, "out of"):
            score(self.rubric, eo, **self.kwargs)

    # BEAAA-5276: corpus_version enforcement AC tests.
    def test_corpus_version_absent_raises(self):
        import rubric.scoring as scoring_module

        kwargs_no_cv = {k: v for k, v in self.kwargs.items() if k != "corpus_version"}
        with self.assertRaisesRegex(ScoringError, "corpus_version required"):
            scoring_module._score_with_required_corpus_pin(
                self.rubric, _flat_eval(self.rubric, 2.0), **kwargs_no_cv
            )

    def test_corpus_version_none_raises(self):
        import rubric.scoring as scoring_module

        kwargs_cv_none = dict(self.kwargs, corpus_version=None)
        with self.assertRaisesRegex(ScoringError, "corpus_version required"):
            scoring_module._score_with_required_corpus_pin(
                self.rubric, _flat_eval(self.rubric, 2.0), **kwargs_cv_none
            )

    def test_test_mode_global_cannot_bypass_corpus_pin(self):
        import rubric.scoring as scoring_module

        scoring_module._TEST_MODE = True
        try:
            with self.assertRaisesRegex(ScoringError, "corpus_version required"):
                scoring_module._score_with_required_corpus_pin(
                    self.rubric,
                    _flat_eval(self.rubric, 2.0),
                    snapshot_hash="sha256:test",
                    forensic_bundle_uri="https://bundles.local/test",
                )
        finally:
            del scoring_module._TEST_MODE

    def test_corpus_version_round_trips_byte_identical(self):
        cv = "halueval-qa@1.0+jbb@1.0+advbench@1.0"
        out = score(self.rubric, _flat_eval(self.rubric, 2.0), **dict(self.kwargs, corpus_version=cv))
        self.assertEqual(out["corpus_version"], cv)


class SyntheticFixtureTests(unittest.TestCase):
    def test_synthetic_fixture_scores_in_range(self):
        rubric = Rubric.load(RUBRIC_PATH)
        eo = json.loads(FIXTURE.read_text())
        out = score(
            rubric,
            eo,
            snapshot_hash="sha256:fixture",
            forensic_bundle_uri="https://bundles.local/fixture",
            scored_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
            corpus_version="test@0.0.0",
        )
        self.assertGreaterEqual(out["bmi"], 0)
        self.assertLessEqual(out["bmi"], 100)
        # Synthetic profile: strong P1, mid P2, weak P3, mid-low P4.
        # BEAAA-13703 isolates loop-stability back to non-scoring evidence
        # after BEAAA-13700 found residual golden-set drift outside +/-1.
        # The synthetic fixture still carries the evidence row, but the active
        # ROBUSTNESS slate is the v1.3.0 P3.5/P3.6 state.
        self.assertEqual(out["bmi"], 48)
        # Spot-check that P3 (weak adversarial) scores low relative to weight.
        self.assertLess(out["pillars"]["ROBUSTNESS"]["score"], out["pillars"]["ROBUSTNESS"]["weight"] / 2)


if __name__ == "__main__":
    unittest.main()
