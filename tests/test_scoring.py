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

    def test_active_dim_count_is_21(self):
        r = Rubric.load(RUBRIC_PATH)
        self.assertEqual(len(r.active_dim_ids), 21)
        self.assertEqual(len(set(r.active_dim_ids)), 21)


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)
        self.kwargs = dict(
            snapshot_hash="sha256:test",
            forensic_bundle_uri="https://bundles.local/test",
            scored_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
        )

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
        )
        self.assertGreaterEqual(out["bmi"], 0)
        self.assertLessEqual(out["bmi"], 100)
        # Synthetic profile: strong P1, mid P2, weak P3, mid-low P4.
        # Under rubric v1.0 (25/25/25/25 per BEAAA-21 §pillars Founder-locked 2026-05-12):
        # P1=16.67, P2=11.98, P3=7.29, P4=10.94 → 46.87 → ARE 47.
        # (v0.1.2 prior was 48 under 35/20/30/15.)
        self.assertEqual(out["bmi"], 47)
        # Spot-check that P3 (weak adversarial) scores low relative to weight.
        self.assertLess(out["pillars"]["ROBUSTNESS"]["score"], out["pillars"]["ROBUSTNESS"]["weight"] / 2)


if __name__ == "__main__":
    unittest.main()
