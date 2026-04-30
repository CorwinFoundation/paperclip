"""WS4 Report Card render tests."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from report_card.render import render
from rubric.scoring import Rubric, score

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
FIXTURE = REPO / "tests" / "fixtures" / "synthetic_eval_outputs.json"


def _baseline_score():
    rubric = Rubric.load(RUBRIC_PATH)
    eo = json.loads(FIXTURE.read_text())
    out = score(
        rubric,
        eo,
        snapshot_hash="sha256:test",
        forensic_bundle_uri="https://bundles.local/test",
        scored_at=datetime(2026, 4, 30, tzinfo=timezone.utc),
    )
    return out, rubric


class ReportCardRenderTests(unittest.TestCase):
    def test_renders_self_contained_html(self):
        out, rubric = _baseline_score()
        html = render(out, rubric)
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("</html>", html)
        # No external assets — single-file invariant.
        for needle in ("<link", "<script", "src="):
            self.assertNotIn(needle, html, f"render must not include {needle!r}")

    def test_includes_bmi_band_and_pricing_factor(self):
        out, rubric = _baseline_score()
        html = render(out, rubric)
        self.assertIn(f">{out['bmi']}<", html)
        self.assertIn(out["band"]["display"], html)
        self.assertIn(f"{out['band']['modifier_factor']:.2f}", html)
        self.assertIn(out["band"]["posture"], html)

    def test_includes_red_line_footer_fields(self):
        out, rubric = _baseline_score()
        html = render(out, rubric)
        self.assertIn(out["snapshot_hash"], html)
        self.assertIn(out["forensic_bundle_uri"], html)
        self.assertIn(out["scored_at"], html)
        self.assertIn(out["rubric_version"], html)

    def test_includes_every_pillar_and_dim(self):
        out, rubric = _baseline_score()
        html = render(out, rubric)
        for pid in ("P1", "P2", "P3", "P4"):
            self.assertIn(pid, html)
            self.assertIn(escape(rubric.pillars[pid]["name"]), html)
            for did in rubric.pillars[pid]["dim_ids"]:
                self.assertIn(did, html)

    def test_html_escapes_hostile_snapshot_hash(self):
        out, rubric = _baseline_score()
        out["snapshot_hash"] = "sha256:<script>alert(1)</script>"
        html = render(out, rubric)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_bind_blocked_warning_renders_when_p1_dim_below_floor(self):
        rubric = Rubric.load(RUBRIC_PATH)
        eo = json.loads(FIXTURE.read_text())
        eo["P1.1"] = {"raw": 0.0, "evidence_uri": "https://eval.local/p1.1"}
        out = score(
            rubric,
            eo,
            snapshot_hash="sha256:bind-block",
            forensic_bundle_uri="https://bundles.local/b",
            scored_at=datetime(2026, 4, 30, tzinfo=timezone.utc),
        )
        html = render(out, rubric)
        self.assertIn("BIND BLOCKED", html)


if __name__ == "__main__":
    unittest.main()
