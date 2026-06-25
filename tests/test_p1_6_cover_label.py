"""BEAAA-209 Phase 2 — report-card cover-label gate tests."""
from __future__ import annotations

import unittest
from pathlib import Path

from harness import p1_emit
from report_card import render as render_mod
from rubric.scoring import Rubric, score

_REPO = Path(__file__).resolve().parent.parent
_RUBRIC_PATH = _REPO / "rubric" / "rubric.yaml"


def _load_rubric() -> Rubric:
    return Rubric.load(_RUBRIC_PATH)


def _eval_outputs(rubric: Rubric, raw: float = 2.5) -> dict:
    return {
        d: {"raw": raw, "evidence_uri": f"https://test/{d}"}
        for d in rubric.active_dim_ids
    }


def _score_with_aggregator(rubric: Rubric, *, partial: list[str] | None = None) -> dict:
    eo = _eval_outputs(rubric)
    overrides: dict[str, str] = {}
    if partial:
        for d in partial:
            overrides[d] = "missing"
    eo = p1_emit.populate_p1_6_cell(
        eo, rubric=rubric, evidence_uri="s3://test/p1_6",
        explicit_dim_status=overrides or None,
    )
    return score(
        rubric, eo,
        snapshot_hash="sha256:test",
        forensic_bundle_uri="s3://test/bundle",
    )


class CoverLabelHelperTests(unittest.TestCase):

    def test_helper_returns_score_when_path_b_full_coverage(self):
        rubric = _load_rubric()
        sr = _score_with_aggregator(rubric)
        label, block = render_mod._cover_label_from_p1_6(sr)
        self.assertEqual(label, "ARE Score")
        self.assertGreaterEqual(block["coverage_breadth_pct"], 80.0)

    def test_helper_returns_spot_scan_when_under_80_pct(self):
        rubric = _load_rubric()
        # Drop enough dims to land under 80%. Drop all 6 P2 dims (≈25/6×6=25
        # weight) → covered ≈ 95.833-25 = ~70.833 → pct ≈ 73.9% → Spot-Scan.
        sr = _score_with_aggregator(rubric, partial=[
            "P2.1", "P2.2", "P2.3", "P2.4", "P2.5", "P2.6",
        ])
        label, block = render_mod._cover_label_from_p1_6(sr)
        self.assertEqual(label, "ARE Spot-Scan")
        self.assertLess(block["coverage_breadth_pct"], 80.0)

    def test_helper_fallback_when_v0_2_4_fields_absent(self):
        """Pre-v0.2.4 score response → defaults to Spot-Scan."""
        rubric = _load_rubric()
        eo = _eval_outputs(rubric)  # no populate_p1_6_cell call
        sr = score(rubric, eo, snapshot_hash="s", forensic_bundle_uri="f")
        label, _ = render_mod._cover_label_from_p1_6(sr)
        self.assertEqual(label, "ARE Spot-Scan")


class RenderHtmlCoverLabelTests(unittest.TestCase):

    def _render(self, sr: dict) -> str:
        return render_mod.render(sr, _load_rubric(), provenance="test")

    def test_rendered_html_includes_score_label_when_full_coverage(self):
        rubric = _load_rubric()
        sr = _score_with_aggregator(rubric)
        sr["forensic_bundle_uri"] = "https://attach.test/bundle"
        sr["snapshot_hash"] = "sha256:abc"
        html = self._render(sr)
        # report-title carries the cover label prominently.
        self.assertIn(">ARE Score \u2014 Agent Risk Exposure Report Card<", html)
        # And the <title> tag.
        self.assertIn("<title>ARE Score \u2014", html)

    def test_rendered_html_includes_spot_scan_when_partial_coverage(self):
        rubric = _load_rubric()
        sr = _score_with_aggregator(rubric, partial=[
            "P2.1", "P2.2", "P2.3", "P2.4", "P2.5", "P2.6",
        ])
        sr["forensic_bundle_uri"] = "https://attach.test/bundle"
        sr["snapshot_hash"] = "sha256:abc"
        html = self._render(sr)
        self.assertIn(">ARE Spot-Scan \u2014 Agent Risk Exposure Report Card<", html)


if __name__ == "__main__":
    unittest.main()
