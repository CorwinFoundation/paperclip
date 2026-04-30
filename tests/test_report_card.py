"""WS4 Report Card render tests."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from report_card.render import evaluate_bind_eligibility, render
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


def _clean_score():
    """Lift P3 above weak-link floors and P4 above pillar floor."""
    rubric = Rubric.load(RUBRIC_PATH)
    eo = json.loads(FIXTURE.read_text())
    for did in ("P3.1", "P3.2", "P3.3"):
        eo[did] = {"raw": 2.0, "evidence_uri": f"https://eval.local/{did}"}
    for did in ("P4.1", "P4.2", "P4.3", "P4.4", "P4.5", "P4.6"):
        eo[did] = {"raw": 2.5, "evidence_uri": f"https://eval.local/{did}"}
    return score(
        rubric,
        eo,
        snapshot_hash="sha256:clean",
        forensic_bundle_uri="https://bundles.local/c",
        scored_at=datetime(2026, 4, 30, tzinfo=timezone.utc),
    )


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


class BindEligibilityTests(unittest.TestCase):
    def test_synthetic_fixture_passes_per_dim_floors_but_fails_p3_weak_link(self):
        # Synthetic fixture: P3.2 = P3.3 = 1.0 < min_dim_raw 1.5 → fail.
        out, _ = _baseline_score()
        verdict = evaluate_bind_eligibility(out)
        self.assertFalse(verdict["eligible"])
        self.assertTrue(any("weak-link" in r for r in verdict["reasons"]))

    def test_p3_aggregate_floor_breach_reported(self):
        # P3.1=P3.2=P3.3=2.0 ⇒ P3 score=15.0 > 11.25 ⇒ aggregate ok.
        # Push to 1.4: P3 score=10.5 < 11.25 ⇒ aggregate floor fails AND
        # weak-link fails (1.4 < 1.5).
        rubric = Rubric.load(RUBRIC_PATH)
        eo = json.loads(FIXTURE.read_text())
        for did in ("P3.1", "P3.2", "P3.3"):
            eo[did] = {"raw": 1.4, "evidence_uri": f"https://eval.local/{did}"}
        out = score(
            rubric,
            eo,
            snapshot_hash="sha256:p3-fail",
            forensic_bundle_uri="https://bundles.local/p3",
            scored_at=datetime(2026, 4, 30, tzinfo=timezone.utc),
        )
        verdict = evaluate_bind_eligibility(out)
        self.assertFalse(verdict["eligible"])
        self.assertTrue(any("P3 score" in r and "pillar_floor" in r for r in verdict["reasons"]))

    def test_clean_fixture_passes_all_floors(self):
        # P3 dims >= 1.5 + aggregate above 11.25; P4 lifted above 7.50 floor.
        out = _clean_score()
        verdict = evaluate_bind_eligibility(out)
        self.assertTrue(verdict["eligible"], verdict["reasons"])
        self.assertEqual(verdict["reasons"], [])

    def test_no_bind_verdict_renders_in_html(self):
        out, rubric = _baseline_score()
        html = render(out, rubric)
        self.assertIn("NO BIND", html)
        self.assertIn("eligibility fail", html)

    def test_bind_verdict_renders_when_clean(self):
        rubric = Rubric.load(RUBRIC_PATH)
        out = _clean_score()
        html = render(out, rubric)
        self.assertIn(">BIND<", html)
        self.assertIn("eligibility pass", html)


class FloorAndAgeRenderTests(unittest.TestCase):
    def test_pillar_floor_rendered_for_p3_and_p4(self):
        out, rubric = _baseline_score()
        html = render(out, rubric)
        # P3 floor 11.25 + min_dim_raw 1.5 displayed.
        self.assertIn("≥ 11.25", html)
        self.assertIn("dim ≥ 1.50", html)
        # P4 floor 7.50 displayed.
        self.assertIn("≥ 7.50", html)

    def test_stale_scan_warning_fires_at_90_days(self):
        out, rubric = _baseline_score()
        out["scan_evidence_age_days"] = 90
        html = render(out, rubric)
        self.assertIn("STALE", html)
        self.assertIn("claims trip-wire fires", html)

    def test_no_stale_warning_under_90_days(self):
        out, rubric = _baseline_score()
        out["scan_evidence_age_days"] = 89
        html = render(out, rubric)
        self.assertNotIn("STALE", html)


if __name__ == "__main__":
    unittest.main()
