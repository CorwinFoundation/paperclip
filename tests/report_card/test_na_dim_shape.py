"""BEAAA-14448 F13 — report_card must not crash on present-but-N/A dims.

N/A dims are emitted by ``rubric/scoring.py`` (Option C) as
``{"raw": None, "applicable": False, ...}`` — the key is PRESENT with a
``None`` value, so ``float(entry.get("raw", 0))`` evaluates ``float(None)``
and raises ``TypeError``. This file pins the four report_card entrypoints
that ingest a dim block:

  1. ``diff.compute_diff``          (current-side dim loop)
  2. ``diff._find_prior_raw``       (prior-side lookup)
  3. ``reproducibility.compute_reproducibility`` (replicate raw append)
  4. ``render._compute_display_weighted``        (display-weighted pillar mean)

The display-weighted mean must also match the engine: normalize over
**measured dims only**, mirroring ``rubric/scoring.py`` (~L296)
``normalized = (sum(raws) / len(raws)) / 4.0`` where ``raws`` excludes N/A
dims — NOT ``(3.0 + 0) / 2``.

Test-first: on the pre-fix tree every case below raises ``TypeError``; the
fix commit makes them pass.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from report_card.diff import compute_diff
from report_card.reproducibility import compute_reproducibility
from report_card.render import _compute_display_weighted

# Canonical N/A dim shape emitted by rubric/scoring.py under Option C.
NA_DIM = {"raw": None, "applicable": False, "na_justification": "not exercised"}


def _resp(*, bmi: int, letter: str, dims: dict) -> dict:
    """Minimal ScoreResponse-shaped dict with one OPS pillar."""
    return {
        "bmi": bmi,
        "band": {"letter": letter, "modifier": "", "display": f"{letter} — band"},
        "scored_at": "2026-07-15T00:00:00+00:00",
        "pillars": {
            "OPS": {"weight": 25, "score": 0.0, "dimensions": dict(dims)},
        },
    }


class DiffNaDimTests(unittest.TestCase):
    def test_compute_diff_skips_na_dim_on_current_side(self):
        # Site 1: current-side loop hits an N/A dim (raw=None).
        current = _resp(bmi=50, letter="B", dims={"P1.1": {"raw": 3.0}, "P1.2": dict(NA_DIM)})
        prior = _resp(bmi=48, letter="B", dims={"P1.1": {"raw": 2.0}, "P1.2": dict(NA_DIM)})
        diff = compute_diff(current, prior)
        # N/A dim yields no delta; measured dim does.
        self.assertNotIn("P1.2", diff["dim_deltas"])
        self.assertIn("P1.1", diff["dim_deltas"])
        self.assertAlmostEqual(diff["dim_deltas"]["P1.1"]["delta"], 1.0)
        # No N/A dim appears as a mover.
        self.assertNotIn("P1.2", [did for did, _ in diff["movers"]])

    def test_find_prior_raw_returns_none_for_na_prior(self):
        # Site 2: current dim measured, prior dim N/A -> _find_prior_raw(None).
        current = _resp(bmi=50, letter="B", dims={"P1.1": {"raw": 3.0}, "P1.2": {"raw": 3.0}})
        prior = _resp(bmi=48, letter="B", dims={"P1.1": {"raw": 2.0}, "P1.2": dict(NA_DIM)})
        diff = compute_diff(current, prior)
        # P1.2 has no prior measured raw -> no delta (caller continues).
        self.assertNotIn("P1.2", diff["dim_deltas"])
        self.assertIn("P1.1", diff["dim_deltas"])


class ReproducibilityNaDimTests(unittest.TestCase):
    def test_reproducibility_excludes_na_dim(self):
        # Site 3: N/A dim raw=None across all replicates.
        reps = [
            _resp(bmi=50, letter="B", dims={"P1.1": {"raw": 3.0}, "P1.2": dict(NA_DIM)})
            for _ in range(3)
        ]
        repro = compute_reproducibility(reps)
        self.assertIn("P1.1", repro["dims"])
        self.assertNotIn("P1.2", repro["dims"])


class DisplayWeightedNaDimTests(unittest.TestCase):
    def test_display_weighted_normalizes_over_measured_only(self):
        # Site 4: crash on N/A + must divide by measured count, not all dims.
        pillars = {
            "OPS": {
                "weight": 25,
                "dimensions": {"P1.1": {"raw": 3.0}, "P1.2": dict(NA_DIM)},
            }
        }
        rubric = SimpleNamespace(pillars={"OPS": {"dim_ids": ["P1.1", "P1.2"]}})
        disp_w = 25
        new_bmi, new_band, new_pillars = _compute_display_weighted(
            pillars, rubric, {"OPS": disp_w}
        )
        # Engine parity: (sum(measured)/len(measured))/4 * disp_w = 3.0/1/4*25.
        expected = (3.0 / 1) / 4.0 * disp_w
        self.assertAlmostEqual(new_pillars["OPS"]["score"], round(expected, 4))
        # NOT the all-dims mean (3.0 + 0)/2/4*25 = 9.375.
        wrong = (3.0 + 0.0) / 2 / 4.0 * disp_w
        self.assertNotAlmostEqual(new_pillars["OPS"]["score"], round(wrong, 4))
        self.assertEqual(new_bmi, int(round(expected)))

    def test_display_weighted_all_na_pillar_scores_zero(self):
        # Whole pillar N/A -> mirror engine all-N/A branch (pillar score 0).
        pillars = {"OPS": {"weight": 25, "dimensions": {"P1.1": dict(NA_DIM), "P1.2": dict(NA_DIM)}}}
        rubric = SimpleNamespace(pillars={"OPS": {"dim_ids": ["P1.1", "P1.2"]}})
        _bmi, _band, new_pillars = _compute_display_weighted(pillars, rubric, {"OPS": 25})
        self.assertEqual(new_pillars["OPS"]["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
