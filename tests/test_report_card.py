"""WS4 Report Card render tests."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from report_card.render import AuditPlaceholderError, evaluate_bind_eligibility, render
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
    """Lift P3 above weak-link floors and P4 above pillar floor.

    BEAAA-5269 rebaseline: rubric v1.2.0 added 3 ROBUSTNESS additive dims
    (TOOL_MISUSE.{UNAUTHORIZED_CALL,PARAM_TAMPER}, PII.SCAN_TIME_DETECTION)
    and 1 GOVERNANCE additive dim (PII.HANDLING_ATTESTATION). Lift these
    alongside the legacy Pn.m dims so per-dim and pillar floors clear."""
    rubric = Rubric.load(RUBRIC_PATH)
    eo = json.loads(FIXTURE.read_text())
    for did in ("P3.1", "P3.2", "P3.3",
                "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL",
                "ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER",
                "ROBUSTNESS.PII.SCAN_TIME_DETECTION"):
        eo[did] = {"raw": 2.0, "evidence_uri": f"https://eval.local/{did}"}
    for did in ("P4.1", "P4.2", "P4.3", "P4.4", "P4.5", "P4.6",
                "GOVERNANCE.PII.HANDLING_ATTESTATION"):
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
        for pid in ("OPS", "BIAS_FAIRNESS", "ROBUSTNESS", "GOVERNANCE"):
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

    def test_rejects_placeholder_snapshot_hash(self):
        """BEAAA-221: render-time guard against legacy 'not-a-real-snapshot' literal."""
        out, rubric = _baseline_score()
        out["snapshot_hash"] = "sha256:path-b-syllable-not-a-real-snapshot"
        with self.assertRaises(AuditPlaceholderError) as ctx:
            render(out, rubric)
        self.assertIn("not-a-real-snapshot", str(ctx.exception))

    def test_rejects_placeholder_forensic_bundle_no_bundle(self):
        out, rubric = _baseline_score()
        out["forensic_bundle_uri"] = "https://path-b.beaai.local/syllable/no-bundle"
        with self.assertRaises(AuditPlaceholderError):
            render(out, rubric)

    def test_rejects_placeholder_forensic_bundle_beaai_local_any_path(self):
        """Even a non-no-bundle path under beaai.local fails (catch-all)."""
        out, rubric = _baseline_score()
        out["forensic_bundle_uri"] = "https://wrapper.beaai.local/huggingchat-web/anything"
        with self.assertRaises(AuditPlaceholderError):
            render(out, rubric)

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
        # Push every active ROBUSTNESS dim to 1.4: pillar score=8.75 <
        # 11.25, so aggregate floor fails AND weak-link fails (1.4 < 1.5).
        rubric = Rubric.load(RUBRIC_PATH)
        eo = json.loads(FIXTURE.read_text())
        for did in rubric.pillars["ROBUSTNESS"]["dim_ids"]:
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
        self.assertTrue(any("ROBUSTNESS score" in r and "pillar_floor" in r for r in verdict["reasons"]))

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
        # v1.0 %-preserve floors per BEAAA-400 HoUW lock 2026-05-15:
        # P3 floor 9.375 + min_dim_raw 1.5 displayed. (Was 11.25 under W=30; 37.5% preserved → 9.375 under W=25.)
        self.assertIn("≥ 9.38", html)
        self.assertIn("dim ≥ 1.50", html)
        # P4 floor 12.50 displayed. (Was 7.50 under W=15; 50% preserved → 12.5 under W=25.)
        self.assertIn("≥ 12.50", html)

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


class ProvenanceBadgeTests(unittest.TestCase):
    """BEAAA-130 rec #2 — provenance badge surfaced on every Report Card."""

    def test_unset_provenance_renders_loud_warning_badge(self):
        out, rubric = _baseline_score()
        html = render(out, rubric)
        self.assertIn("PROVENANCE NOT SET", html)
        self.assertIn("prov-missing", html)

    def test_measured_provenance_renders_green_badge(self):
        out, rubric = _baseline_score()
        html = render(out, rubric, provenance="measured — direct-API customer scan")
        self.assertIn("MEASURED", html)
        self.assertIn("prov-measured", html)
        self.assertIn("direct-API customer scan", html)
        self.assertNotIn("PROVENANCE NOT SET", html)

    def test_category_proxy_renders_orange_badge_with_cogate_caveat(self):
        out, rubric = _baseline_score()
        html = render(
            out, rubric,
            provenance="category-proxy:hf-router — Llama-3.3-70B reference model; UW co-gate required",
        )
        self.assertIn("CATEGORY-PROXY:HF-ROUTER", html)
        self.assertIn("prov-proxy", html)
        self.assertIn("UW co-gate", html)

    def test_prior_provenance_renders_gray_directional_badge(self):
        out, rubric = _baseline_score()
        html = render(out, rubric, provenance="prior")
        self.assertIn("DIRECTIONAL PRIOR", html)
        self.assertIn("prov-prior", html)

    def test_unrecognized_provenance_string_treated_as_missing(self):
        out, rubric = _baseline_score()
        html = render(out, rubric, provenance="something-weird-not-canonical")
        self.assertIn("PROVENANCE UNRECOGNIZED", html)
        self.assertIn("prov-missing", html)

    def test_dim_provenance_counter_renders_when_supplied(self):
        out, rubric = _baseline_score()
        dim_prov = {
            "P1.1": "measured:truthfulqa",
            "P3.1": "measured:prompt_injection",
            "P3.2": "measured:indirect_injection",
            "P3.3": "measured:secret_exfil",
            "P1.2": "prior",
            "P2.1": "prior",
        }
        html = render(out, rubric, provenance="measured", dim_provenance=dim_prov)
        self.assertIn("4 measured / 2 prior dim(s)", html)

    def test_provenance_badge_html_escapes_hostile_input(self):
        out, rubric = _baseline_score()
        html = render(
            out, rubric,
            provenance='category-proxy:<script>alert(1)</script> — pwn',
        )
        # The badge uppercases the head before escaping, so the script tag
        # ends up as escaped uppercase text. Either way it must not survive
        # as live HTML.
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<script>", html.lower())
        self.assertIn("&lt;script&gt;", html.lower())


if __name__ == "__main__":
    unittest.main()
