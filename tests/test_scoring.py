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

    def test_active_dim_count_is_28(self):
        r = Rubric.load(RUBRIC_PATH)
        self.assertEqual(len(r.active_dim_ids), 28)
        self.assertEqual(len(set(r.active_dim_ids)), 28)
        self.assertIn("OPS.LOOP_STABILITY.BUDGET_GUARD", r.active_dim_ids)


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
    # These tests temporarily set _TEST_MODE=False to exercise the hard
    # contract boundary that conftest.py disables globally for legacy tests.
    def test_corpus_version_absent_raises(self):
        import rubric.scoring as _sc
        _sc._TEST_MODE = False
        try:
            kwargs_no_cv = {k: v for k, v in self.kwargs.items() if k != "corpus_version"}
            with self.assertRaisesRegex(ScoringError, "corpus_version required"):
                score(self.rubric, _flat_eval(self.rubric, 2.0), **kwargs_no_cv)
        finally:
            _sc._TEST_MODE = True

    def test_corpus_version_none_raises(self):
        import rubric.scoring as _sc
        _sc._TEST_MODE = False
        try:
            kwargs_cv_none = dict(self.kwargs, corpus_version=None)
            with self.assertRaisesRegex(ScoringError, "corpus_version required"):
                score(self.rubric, _flat_eval(self.rubric, 2.0), **kwargs_cv_none)
        finally:
            _sc._TEST_MODE = True

    def test_corpus_version_round_trips_byte_identical(self):
        cv = "halueval-qa@1.0+jbb@1.0+advbench@1.0"
        out = score(self.rubric, _flat_eval(self.rubric, 2.0), **dict(self.kwargs, corpus_version=cv))
        self.assertEqual(out["corpus_version"], cv)

    def test_p3_5_scores_by_default_after_approved_activation(self):
        eo_with_p35 = _flat_eval(self.rubric, 4.0)
        eo_all_green = _flat_eval(self.rubric, 4.0)
        eo_with_p35["P3.5"] = {
            "raw": 0.0,
            "evidence_uri": "artifacts/beaaa-9673/p3_5_multiturn_candidate.md#bad",
        }

        with_p35 = score(self.rubric, eo_with_p35, **self.kwargs)
        all_green = score(self.rubric, eo_all_green, **self.kwargs)

        self.assertLess(with_p35["are_total"], all_green["are_total"])
        self.assertLess(
            with_p35["pillars"]["ROBUSTNESS"]["score"],
            all_green["pillars"]["ROBUSTNESS"]["score"],
        )
        p35 = with_p35["pillars"]["ROBUSTNESS"]["dimensions"]["P3.5"]
        self.assertEqual(p35["raw"], 0.0)
        self.assertNotIn("scoring_active", p35)
        self.assertNotIn("candidate_gate", p35)
        self.assertEqual(
            p35["evidence_uri"],
            "artifacts/beaaa-9673/p3_5_multiturn_candidate.md#bad",
        )

    def test_explicit_p3_5_candidate_gate_closed_preserves_score_and_carries_evidence(self):
        eo_with_p35 = _flat_eval(self.rubric, 4.0)
        eo_without_p35 = _flat_eval(self.rubric, 4.0)
        eo_with_p35["P3.5"] = {
            "raw": 0.0,
            "evidence_uri": "artifacts/beaaa-9673/p3_5_multiturn_candidate.md#bad",
        }
        eo_without_p35.pop("P3.5")

        with_p35 = score(
            self.rubric,
            eo_with_p35,
            candidate_scoring_gates={"P3.5": False},
            **self.kwargs,
        )
        without_p35 = score(
            self.rubric,
            eo_without_p35,
            candidate_scoring_gates={"P3.5": False},
            **self.kwargs,
        )

        self.assertEqual(with_p35["are_total"], without_p35["are_total"])
        self.assertEqual(
            with_p35["pillars"]["ROBUSTNESS"]["score"],
            without_p35["pillars"]["ROBUSTNESS"]["score"],
        )
        p35 = with_p35["pillars"]["ROBUSTNESS"]["dimensions"]["P3.5"]
        self.assertEqual(p35["raw"], 0.0)
        self.assertFalse(p35["scoring_active"])
        self.assertEqual(p35["candidate_gate"], "closed")
        self.assertEqual(
            p35["evidence_uri"],
            "artifacts/beaaa-9673/p3_5_multiturn_candidate.md#bad",
        )

    def test_p3_5_candidate_gate_open_scores_under_robustness_without_shape_change(self):
        eo = _flat_eval(self.rubric, 4.0)
        eo["P3.5"] = {
            "raw": 0.0,
            "evidence_uri": "artifacts/beaaa-9673/p3_5_multiturn_candidate.md#bad",
        }

        closed = score(
            self.rubric,
            eo,
            candidate_scoring_gates={"P3.5": False},
            **self.kwargs,
        )
        opened = score(
            self.rubric,
            eo,
            candidate_scoring_gates={"P3.5": True},
            **self.kwargs,
        )

        self.assertEqual(set(opened) - {"diagnostics"}, set(closed))
        self.assertEqual(opened["pillars"]["ROBUSTNESS"]["weight"], 25)
        self.assertLess(
            opened["pillars"]["ROBUSTNESS"]["score"],
            closed["pillars"]["ROBUSTNESS"]["score"],
        )
        p35 = opened["pillars"]["ROBUSTNESS"]["dimensions"]["P3.5"]
        self.assertEqual(p35["raw"], 0.0)
        self.assertTrue(p35["scoring_active"])
        self.assertEqual(p35["candidate_gate"], "open")
        gate_diagnostics = [
            d for d in opened["diagnostics"]
            if d["code"] == "candidate_scoring_gate_open"
        ]
        self.assertEqual(len(gate_diagnostics), 1)
        self.assertEqual(gate_diagnostics[0]["detail"]["open_dims"], ["P3.5"])


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
        # BEAAA-13733 adds an OPS loop-stability candidate row. Synthetic
        # fixtures carry measured pass evidence; frozen golden replay emits
        # the candidate N/A through applicability sidecars.
        self.assertEqual(out["bmi"], 49)
        # Spot-check that P3 (weak adversarial) scores low relative to weight.
        self.assertLess(out["pillars"]["ROBUSTNESS"]["score"], out["pillars"]["ROBUSTNESS"]["weight"] / 2)


if __name__ == "__main__":
    unittest.main()
