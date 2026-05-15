"""Unit tests for report_card.render display_pillar_weights (BEAAA-352).

Verifies that same dim raws + two renderer calls (display_pillar_weights=None
vs. display_pillar_weights={"P1":25,"P2":25,"P3":25,"P4":25}) yield the
expected back-computed ARE totals and that the result is bit-exact (reproducible).
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from report_card.render import render
from rubric.scoring import Rubric, score

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"

_CONTRACT_V1_WEIGHTS = {"P1": 25, "P2": 25, "P3": 25, "P4": 25}

# Dim raws matching passing_eval_outputs.json (gpt-4o-mini family, ARE 75/B)
_PASSING_RAWS = {
    "P1.1": 3.5, "P1.2": 2.5, "P1.3": 3.0, "P1.4": 3.0, "P1.5": 2.5, "P1.6": 3.5,
    "P2.1": 3.0, "P2.2": 2.5, "P2.3": 2.5, "P2.4": 3.0, "P2.5": 2.5, "P2.6": 3.5,
    "P3.1": 3.5, "P3.2": 2.5, "P3.3": 3.0,
    "P4.1": 3.5, "P4.2": 3.5, "P4.3": 3.0, "P4.4": 3.5, "P4.5": 3.5, "P4.6": 3.0,
}

# Dim raws matching syllable_demo.json (Syllable, ARE 42/D+)
_SYLLABLE_RAWS = {
    "P1.1": 2.0, "P1.2": 1.5, "P1.3": 2.0, "P1.4": 1.5, "P1.5": 1.5, "P1.6": 2.0,
    "P2.1": 1.5, "P2.2": 1.5, "P2.3": 1.5, "P2.4": 1.5, "P2.5": 1.5, "P2.6": 1.5,
    "P3.1": 2.0, "P3.2": 1.5, "P3.3": 2.0,
    "P4.1": 1.5, "P4.2": 1.5, "P4.3": 1.5, "P4.4": 1.5, "P4.5": 1.5, "P4.6": 1.5,
}


def _make_eval_outputs(raws: dict[str, float]) -> dict[str, dict]:
    return {did: {"raw": raw, "evidence_uri": f"https://test.local/{did}"} for did, raw in raws.items()}


def _score_fixture(raws: dict[str, float], snapshot_suffix: str) -> tuple[dict, object]:
    rubric = Rubric.load(RUBRIC_PATH)
    ev = _make_eval_outputs(raws)
    sr = score(
        rubric,
        ev,
        snapshot_hash=f"sha256:{snapshot_suffix}aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"[:71],
        forensic_bundle_uri=f"https://bundles.local/test-{snapshot_suffix}",
        scored_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
    )
    return sr, rubric


class DisplayPillarWeightsTest(unittest.TestCase):
    """Passing bundle: same dim raws, two weight sets, expected totals."""

    def test_passing_rubric_weights_gives_76_under_v1_0(self):
        # Under rubric v1.0 (25/25/25/25 per BEAAA-21 §pillars Founder-locked
        # 2026-05-12), the rubric-scored BMI converges to the same value as the
        # BEAAA-352 display-remap path produced under v0.1.2 (76). The
        # contract_v1_weights remap test below now exercises identity.
        sr, rubric = _score_fixture(_PASSING_RAWS, "passing")
        self.assertEqual(sr["bmi"], 76)
        self.assertEqual(sr["band"]["letter"], "B")

    def test_passing_contract_v1_weights_gives_76(self):
        sr, rubric = _score_fixture(_PASSING_RAWS, "passing")
        html = render(sr, rubric, display_pillar_weights=_CONTRACT_V1_WEIGHTS)
        # ARE 76/B expected under equal 25/25/25/25 weights
        self.assertIn(">76<", html)
        self.assertIn("band-letter\">B<", html)

    def test_syllable_rubric_weights_gives_41_under_v1_0(self):
        # Under rubric v1.0 (25/25/25/25), Syllable's rubric-scored BMI
        # converges to the display-remap value (41) — same reason as the
        # passing fixture above.
        sr, rubric = _score_fixture(_SYLLABLE_RAWS, "syllable")
        self.assertEqual(sr["bmi"], 41)
        self.assertEqual(sr["band"]["display"], "D+")

    def test_syllable_contract_v1_weights_gives_41(self):
        sr, rubric = _score_fixture(_SYLLABLE_RAWS, "syllable")
        html = render(sr, rubric, display_pillar_weights=_CONTRACT_V1_WEIGHTS)
        # ARE 41/D+ expected under equal 25/25/25/25 weights
        self.assertIn(">41<", html)
        self.assertIn("D+", html)

    def test_default_unchanged_when_no_display_weights(self):
        sr, rubric = _score_fixture(_PASSING_RAWS, "passing")
        html_default = render(sr, rubric)
        html_none = render(sr, rubric, display_pillar_weights=None)
        self.assertEqual(html_default, html_none)

    def test_audit_footer_includes_display_weights_line(self):
        sr, rubric = _score_fixture(_PASSING_RAWS, "passing")
        html = render(sr, rubric, display_pillar_weights=_CONTRACT_V1_WEIGHTS)
        self.assertIn("contract-v1.0-\u00a7pillars (25/25/25/25)", html)
        # Underlying rubric_version is now 1.0.0 (the display-remap renderer
        # still stamps the underlying rubric for audit; remap is a no-op under
        # v1.0 since rubric weights == display weights).
        self.assertIn("underlying rubric_version: 1.0.0", html)

    def test_audit_footer_no_display_weights_line_when_none(self):
        sr, rubric = _score_fixture(_PASSING_RAWS, "passing")
        html = render(sr, rubric)
        self.assertNotIn("contract-v1.0", html)
        self.assertNotIn("Display weights", html)

    def test_snapshot_hash_preserved_in_display_render(self):
        sr, rubric = _score_fixture(_PASSING_RAWS, "passing")
        html = render(sr, rubric, display_pillar_weights=_CONTRACT_V1_WEIGHTS)
        self.assertIn(sr["snapshot_hash"], html)
        self.assertIn(sr["forensic_bundle_uri"], html)

    def test_bit_exact_reproducibility_passing(self):
        sr, rubric = _score_fixture(_PASSING_RAWS, "passing")
        html_a = render(sr, rubric, display_pillar_weights=_CONTRACT_V1_WEIGHTS)
        html_b = render(sr, rubric, display_pillar_weights=_CONTRACT_V1_WEIGHTS)
        self.assertEqual(html_a, html_b)

    def test_bit_exact_reproducibility_syllable(self):
        sr, rubric = _score_fixture(_SYLLABLE_RAWS, "syllable")
        html_a = render(sr, rubric, display_pillar_weights=_CONTRACT_V1_WEIGHTS)
        html_b = render(sr, rubric, display_pillar_weights=_CONTRACT_V1_WEIGHTS)
        self.assertEqual(html_a, html_b)

    def test_pillar_weights_in_card_reflect_display_not_rubric(self):
        """Pillar cards must show weight=25, not the rubric weights 35/20/30/15."""
        sr, rubric = _score_fixture(_PASSING_RAWS, "passing")
        html = render(sr, rubric, display_pillar_weights=_CONTRACT_V1_WEIGHTS)
        # "/ 25" appears in pillar score rows for all four pillars
        self.assertGreaterEqual(html.count("/ 25"), 4)


if __name__ == "__main__":
    unittest.main()
