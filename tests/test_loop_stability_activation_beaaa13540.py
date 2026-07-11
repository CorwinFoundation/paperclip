"""BEAAA-13540 loop-stability ROBUSTNESS activation tests."""
from __future__ import annotations

import unittest
from pathlib import Path

from rubric.scoring import Rubric


_RUBRIC_PATH = Path(__file__).resolve().parents[1] / "rubric" / "rubric.yaml"
_DIM_ID = "ROBUSTNESS.LOOP_STABILITY.BUDGET_GUARD"


class LoopStabilityActivationTests(unittest.TestCase):
    def test_loop_stability_is_active_robustness_dimension_only(self) -> None:
        rubric = Rubric.load(_RUBRIC_PATH)

        self.assertEqual(rubric.version, "1.4.0")
        self.assertIn(_DIM_ID, rubric.pillars["ROBUSTNESS"]["dim_ids"])
        self.assertNotIn(_DIM_ID, rubric.pillars["OPS"]["dim_ids"])
        self.assertNotIn(_DIM_ID, rubric.pillars["GOVERNANCE"]["dim_ids"])
        self.assertNotIn(_DIM_ID, rubric.pillars["BIAS_FAIRNESS"]["dim_ids"])

    def test_loop_stability_activation_cites_superseded_placements(self) -> None:
        text = _RUBRIC_PATH.read_text(encoding="utf-8")
        robustness_idx = text.find("ROBUSTNESS:")
        governance_idx = text.find("GOVERNANCE:")

        self.assertNotEqual(robustness_idx, -1)
        self.assertNotEqual(governance_idx, -1)
        robustness_slice = text[robustness_idx:governance_idx]

        self.assertIn(_DIM_ID, robustness_slice)
        self.assertIn("BEAAA-13540", robustness_slice)
        self.assertIn("Prior OPS/GOVERNANCE loop-stability candidates are superseded", robustness_slice)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
