"""BEAAA-561 — ADR-0007 §1.3 pricing-engine admittance gate tests.

Covers the four admittance_class branches end-to-end in `quote()`, the
ADR §2 telemetry sink with the three §4 production-trigger watches, and
a live-fixture integration pass against the BEAAA-461 dry-run set
(G1/G2/G3 → CONSTRAINED_BIND R7; B3 → REFUSE R1).

Authority chain: ADR-0007 ADOPTED 2026-05-19 (commit 08a1eb5, CEO co-sign
accepted 2026-05-19T14:34:21Z via request_confirmation 7945987c-cb30-4eb3-
9941-dce9590207f1, idempotencyKey confirmation:BEAAA-479:adr-0007:rev1).
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from pricing import quote as quote_mod
from pricing.quote import (
    Ineligible,
    ManualReferral,
    Rates,
    quote,
    set_telemetry_sink,
)
from rubric.bands import assign_band, band_to_dict
from underwriting import admittance

REPO = Path(__file__).resolve().parents[1]
RATES_PATH = REPO / "pricing" / "rates.yaml"
FIXTURES_DIR = REPO / "artifacts" / "beaaa-387"


def _score(are_total: int, *, na_dims: list[str] | None = None,
           pillars_with_all_na: list[str] | None = None,
           measured_dims: list[str] | None = None) -> dict:
    """Minimal score envelope with optional admittance evidence bundle."""
    s: dict = {"bmi": are_total, "band": band_to_dict(assign_band(are_total))}
    if measured_dims is not None or na_dims is not None:
        s["applicability_record"] = {
            "measured_dims": list(measured_dims or []),
            "na_dims": list(na_dims or []),
        }
    if pillars_with_all_na is not None:
        s["diagnostics"] = [{
            "code": "coverage_caveat",
            "detail": {
                "na_dims": list(na_dims or []),
                "pillars_with_all_na": list(pillars_with_all_na),
            },
        }]
    return s


STD_CUSTOMER = {
    "sector": "ENT_SAAS",
    "annual_revenue_usd": 100_000_000,
    "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
}


class TelemetryCollector:
    """Thread-unaware list-collector for ADR-0007 §2 events; test-only."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(event)


class AdmittanceBranchTests(unittest.TestCase):
    """Verify each admittance_class produces the correct quote-flow effect."""

    def setUp(self):
        self.rates = Rates.load(RATES_PATH)
        self.sink = TelemetryCollector()
        set_telemetry_sink(self.sink)

    def tearDown(self):
        set_telemetry_sink(None)

    def test_refuse_raises_ineligible_with_rule_id(self):
        # ADM.R1: anti_safety posture → REFUSE regardless of score.
        report = {
            "are_total": 63,
            "system_prompt_kind": "anti_safety",
            "applicability_record": {
                "measured_dims": ["P2.1", "P2.3", "P2.4", "P2.5", "P2.6", "P3.1", "P3.3"],
                "na_dims": ["P2.2", "P3.2"],
            },
            "diagnostics": [{"code": "coverage_caveat", "detail": {"pillars_with_all_na": []}}],
        }
        decision = admittance.evaluate(report)
        self.assertEqual(decision.admittance_class, admittance.REFUSE)
        self.assertEqual(decision.firing_rule_id, "ADM.R1")
        with self.assertRaises(Ineligible) as ctx:
            quote(_score(63), STD_CUSTOMER, rates=self.rates, admittance_decision=decision)
        self.assertEqual(ctx.exception.rule_id, "ADM.R1")
        self.assertEqual(ctx.exception.decision["admittance_class"], "REFUSE")
        # Telemetry emitted on this attempt.
        self.assertEqual(len(self.sink.events), 1)
        self.assertEqual(self.sink.events[0]["firing_rule_id"], "ADM.R1")

    def test_adm_r0_floor_breach_refuse_produces_no_premium(self):
        report = {
            "are_total": 96,
            "system_prompt_kind": "hardened",
            "applicability_record": {
                "measured_dims": ["P2.1", "P2.3", "P2.4", "P2.5", "P2.6", "P3.1", "P3.3"],
                "na_dims": [],
            },
            "pillars": {
                "ROBUSTNESS": {
                    "dimensions": {
                        "P3.3": {"raw": 0.0, "applicable": True},
                        "P3.1": {"raw": 4.0, "applicable": True},
                    }
                }
            },
            "pillar_floors": {"ROBUSTNESS": {"min_dim_raw": 1.5}},
            "diagnostics": [{"code": "coverage_caveat", "detail": {"pillars_with_all_na": []}}],
        }
        decision = admittance.evaluate(report)
        self.assertEqual(decision.firing_rule_id, "ADM.R0")
        self.assertEqual(decision.admittance_class, admittance.REFUSE)
        with self.assertRaises(Ineligible) as ctx:
            quote(_score(96), STD_CUSTOMER, rates=self.rates, admittance_decision=decision)
        self.assertEqual(ctx.exception.rule_id, "ADM.R0")
        self.assertNotIn("annual_premium_usd", ctx.exception.breakdown)

    def test_refer_to_uw_raises_manual_referral_with_evidence(self):
        # ADM.R4: posture=none AND are_total>=50 → REFER_TO_UW.
        report = {
            "are_total": 72,
            "system_prompt_kind": "none",
            "applicability_record": {
                "measured_dims": ["P2.1", "P2.3", "P2.4", "P2.5", "P2.6", "P3.1", "P3.3"],
                "na_dims": ["P2.2", "P3.2"],
            },
            "diagnostics": [{"code": "coverage_caveat", "detail": {"pillars_with_all_na": [], "na_dims": ["P2.2", "P3.2"]}}],
        }
        decision = admittance.evaluate(report)
        self.assertEqual(decision.firing_rule_id, "ADM.R4")
        with self.assertRaises(ManualReferral) as ctx:
            quote(_score(72, na_dims=["P2.2", "P3.2"], measured_dims=["P2.1", "P2.3", "P2.4", "P2.5", "P2.6", "P3.1", "P3.3"]),
                  STD_CUSTOMER, rates=self.rates, admittance_decision=decision)
        # Evidence bundle includes the canonical fields per ADR §1.3 row 2.
        bd = ctx.exception.breakdown
        self.assertEqual(bd["admittance"]["firing_rule_id"], "ADM.R4")
        self.assertIn("evidence", bd)
        self.assertEqual(bd["evidence"]["are_total"], 72)
        self.assertEqual(bd["evidence"]["na_dims"], ["P2.2", "P3.2"])

    def test_uncovered_robustness_dimension_routes_to_manual_referral(self):
        uncovered_dim = "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL"
        report = {
            "are_total": 92,
            "system_prompt_kind": "hardened",
            "applicability_record": {
                "measured_dims": ["P2.1", "P2.3", "P2.4", "P2.5", "P2.6", "P3.1", "P3.3"],
                "na_dims": [],
            },
            "uncovered_dimensions": [{"dim_id": uncovered_dim}],
            "diagnostics": [{"code": "coverage_caveat", "detail": {"pillars_with_all_na": []}}],
        }
        decision = admittance.evaluate(report)
        self.assertEqual(decision.firing_rule_id, "ADM.R0")
        self.assertEqual(decision.admittance_class, admittance.REFER_TO_UW)
        score = _score(92, na_dims=[], measured_dims=report["applicability_record"]["measured_dims"])
        score["uncovered_dimensions"] = [{"dim_id": uncovered_dim}]
        with self.assertRaises(ManualReferral) as ctx:
            quote(score, STD_CUSTOMER, rates=self.rates, admittance_decision=decision)
        self.assertEqual(ctx.exception.breakdown["admittance"]["firing_rule_id"], "ADM.R0")
        self.assertEqual(ctx.exception.breakdown["evidence"]["na_dims"], [uncovered_dim])

    def test_constrained_bind_applies_f_caveat_and_appends_endorsement(self):
        # ADM.R7: are_total in [70,84] AND measured>=6 → CONSTRAINED_BIND.
        measured = ["P2.1", "P2.3", "P2.4", "P2.5", "P2.6", "P3.1", "P3.3"]
        na = ["P4.2", "P4.3"]
        report = {
            "are_total": 79,
            "system_prompt_kind": "hardened",
            "applicability_record": {"measured_dims": measured, "na_dims": na},
            "diagnostics": [{"code": "coverage_caveat", "detail": {"pillars_with_all_na": [], "na_dims": na}}],
        }
        decision = admittance.evaluate(report)
        self.assertEqual(decision.admittance_class, admittance.CONSTRAINED_BIND)
        self.assertEqual(decision.firing_rule_id, "ADM.R7")
        score = _score(79, na_dims=na, measured_dims=measured)
        # Reference premium without admittance gate.
        q_ref = quote(score, STD_CUSTOMER, rates=self.rates)
        q = quote(score, STD_CUSTOMER, rates=self.rates, admittance_decision=decision)
        # f_caveat = 1 + 0.05 * 2 = 1.10 — ADR-0007 §1.3 placeholder.
        self.assertAlmostEqual(q["breakdown"]["f_caveat"], 1.10)
        self.assertEqual(q["breakdown"]["f_caveat_na_dims_count"], 2)
        self.assertEqual(q["breakdown"]["admittance"]["admittance_class"], "CONSTRAINED_BIND")
        # Premium uplifted by the f_caveat multiplier.
        self.assertGreater(q["annual_premium_usd"], q_ref["annual_premium_usd"])
        # Coverage-caveat exclusion endorsement appended (load_usd=0; the
        # dollar effect is the rated-chain f_caveat, not an additive load).
        codes = [e["code"] for e in q["endorsements"]]
        self.assertIn("coverage_caveat_exclusion", codes)
        cc_row = next(e for e in q["endorsements"] if e["code"] == "coverage_caveat_exclusion")
        self.assertEqual(cc_row["load_usd"], 0)
        self.assertTrue(cc_row["auto_added"])
        self.assertEqual(cc_row["na_dims"], na)

    def test_auto_bind_no_uplift_emits_transparency_row(self):
        # ADM.R8: are_total>=85 AND measured>=6 AND na<=2 → AUTO_BIND.
        measured = ["P2.1", "P2.3", "P2.4", "P2.5", "P2.6", "P3.1", "P3.3"]
        na = ["P4.2", "P4.3"]
        report = {
            "are_total": 92,
            "system_prompt_kind": "hardened",
            "applicability_record": {"measured_dims": measured, "na_dims": na},
            "diagnostics": [{"code": "coverage_caveat", "detail": {"pillars_with_all_na": [], "na_dims": na}}],
        }
        decision = admittance.evaluate(report)
        self.assertEqual(decision.admittance_class, admittance.AUTO_BIND)
        self.assertEqual(decision.firing_rule_id, "ADM.R8")
        score = _score(92, na_dims=na, measured_dims=measured)
        q_ref = quote(score, STD_CUSTOMER, rates=self.rates)
        q = quote(score, STD_CUSTOMER, rates=self.rates, admittance_decision=decision)
        # AUTO_BIND: no uplift, standard rated path.
        self.assertEqual(q["annual_premium_usd"], q_ref["annual_premium_usd"])
        self.assertEqual(q["breakdown"]["f_caveat"], 1.0)
        # Transparency endorsement row (no exclusion) on the policy schedule.
        codes = [e["code"] for e in q["endorsements"]]
        self.assertIn("coverage_caveat_transparency", codes)
        self.assertNotIn("coverage_caveat_exclusion", codes)
        cc_row = next(e for e in q["endorsements"] if e["code"] == "coverage_caveat_transparency")
        self.assertEqual(cc_row["load_usd"], 0)
        self.assertEqual(cc_row["na_dims"], na)


class AdmittanceTelemetryTests(unittest.TestCase):
    """ADR-0007 §4 trigger watches surface correctly in telemetry events."""

    def setUp(self):
        self.rates = Rates.load(RATES_PATH)
        self.sink = TelemetryCollector()
        set_telemetry_sink(self.sink)

    def tearDown(self):
        set_telemetry_sink(None)

    def test_trigger_2_watch_fires_when_adm_r2_fires(self):
        # ADM.R2: any pillar with all-N/A dims → REFUSE. Use posture=custom
        # (non-hardened) to isolate trigger_2_watch from trigger_4_watch —
        # trigger_4 is "hardened submission fires REFUSE on R2/R3", so a
        # non-hardened posture leaves trigger_2 as the sole watch.
        report = {
            "are_total": 80,
            "system_prompt_kind": "custom",
            "applicability_record": {"measured_dims": ["P2.1", "P2.3", "P2.4", "P3.1", "P3.3", "P4.1"], "na_dims": ["P1.1"]},
            "diagnostics": [{"code": "coverage_caveat", "detail": {"pillars_with_all_na": ["P1"]}}],
        }
        decision = admittance.evaluate(report)
        self.assertEqual(decision.firing_rule_id, "ADM.R2")
        with self.assertRaises(Ineligible):
            quote(_score(80), STD_CUSTOMER, rates=self.rates, admittance_decision=decision)
        ev = self.sink.events[-1]
        self.assertTrue(ev["trigger_2_watch"])
        self.assertFalse(ev["trigger_3_watch"])
        self.assertFalse(ev["trigger_4_watch"])

    def test_trigger_3_watch_fires_when_adm_r4_fires(self):
        report = {
            "are_total": 72,
            "system_prompt_kind": "none",
            "applicability_record": {"measured_dims": ["a", "b", "c", "d", "e", "f"], "na_dims": []},
            "diagnostics": [{"code": "coverage_caveat", "detail": {"pillars_with_all_na": []}}],
        }
        decision = admittance.evaluate(report)
        self.assertEqual(decision.firing_rule_id, "ADM.R4")
        with self.assertRaises(ManualReferral):
            quote(_score(72), STD_CUSTOMER, rates=self.rates, admittance_decision=decision)
        ev = self.sink.events[-1]
        self.assertTrue(ev["trigger_3_watch"])
        self.assertFalse(ev["trigger_2_watch"])
        self.assertFalse(ev["trigger_4_watch"])

    def test_trigger_4_watch_fires_on_hardened_refuse(self):
        # Hardened posture + REFUSE via ADM.R2 (pillar-all-NA) → §4 trigger 4.
        report = {
            "are_total": 88,
            "system_prompt_kind": "hardened",
            "applicability_record": {"measured_dims": ["a", "b", "c", "d", "e", "f"], "na_dims": ["X"]},
            "diagnostics": [{"code": "coverage_caveat", "detail": {"pillars_with_all_na": ["P3"]}}],
        }
        decision = admittance.evaluate(report)
        self.assertEqual(decision.admittance_class, admittance.REFUSE)
        self.assertEqual(decision.firing_rule_id, "ADM.R2")
        with self.assertRaises(Ineligible):
            quote(_score(88), STD_CUSTOMER, rates=self.rates, admittance_decision=decision)
        ev = self.sink.events[-1]
        self.assertTrue(ev["trigger_4_watch"])
        self.assertTrue(ev["trigger_2_watch"])

    def test_constrained_bind_emits_telemetry_no_triggers(self):
        report = {
            "are_total": 79,
            "system_prompt_kind": "hardened",
            "applicability_record": {"measured_dims": ["a", "b", "c", "d", "e", "f", "g"], "na_dims": ["X", "Y"]},
            "diagnostics": [{"code": "coverage_caveat", "detail": {"pillars_with_all_na": [], "na_dims": ["X", "Y"]}}],
        }
        decision = admittance.evaluate(report)
        score = _score(79, na_dims=["X", "Y"], measured_dims=["a", "b", "c", "d", "e", "f", "g"])
        quote(score, STD_CUSTOMER, rates=self.rates, admittance_decision=decision)
        ev = self.sink.events[-1]
        self.assertEqual(ev["admittance_class"], "CONSTRAINED_BIND")
        self.assertEqual(ev["firing_rule_id"], "ADM.R7")
        self.assertFalse(ev["trigger_2_watch"])
        self.assertFalse(ev["trigger_3_watch"])
        self.assertFalse(ev["trigger_4_watch"])

    def test_telemetry_sink_failure_does_not_break_quote(self):
        def broken_sink(_event):
            raise RuntimeError("dashboard down")

        set_telemetry_sink(broken_sink)
        report = {
            "are_total": 92,
            "system_prompt_kind": "hardened",
            "applicability_record": {"measured_dims": ["a", "b", "c", "d", "e", "f", "g"], "na_dims": ["X", "Y"]},
            "diagnostics": [{"code": "coverage_caveat", "detail": {"pillars_with_all_na": [], "na_dims": ["X", "Y"]}}],
        }
        decision = admittance.evaluate(report)
        score = _score(92, na_dims=["X", "Y"], measured_dims=["a", "b", "c", "d", "e", "f", "g"])
        # Must not raise — telemetry failure is contained.
        q = quote(score, STD_CUSTOMER, rates=self.rates, admittance_decision=decision)
        self.assertEqual(q["status"], "indicative")


class BeaAA461DryRunIntegrationTests(unittest.TestCase):
    """End-to-end: load BEAAA-461 dry-run breach reports → admittance.evaluate
    → quote() and assert the per-fixture admittance class plus quote artifact."""

    def setUp(self):
        self.rates = Rates.load(RATES_PATH)

    def _load_report(self, fixture: str) -> dict:
        return json.loads((FIXTURES_DIR / fixture / "breach_report.json").read_text())

    def _customer(self) -> dict:
        return dict(STD_CUSTOMER)

    def test_G1_safety_critical_na_routes_to_manual_referral(self):
        report = self._load_report("G1")
        self.assertEqual(report["are_total"], 79)
        decision = admittance.evaluate(report)
        self.assertEqual(decision.admittance_class, admittance.REFER_TO_UW)
        self.assertEqual(decision.firing_rule_id, "ADM.R0")
        score = _score(
            report["are_total"],
            na_dims=report["applicability_record"]["na_dims"],
            measured_dims=report["applicability_record"]["measured_dims"],
        )
        with self.assertRaises(ManualReferral) as ctx:
            quote(score, self._customer(), rates=self.rates, admittance_decision=decision)
        self.assertEqual(ctx.exception.breakdown["admittance"]["firing_rule_id"], "ADM.R0")

    def test_G2_safety_critical_na_routes_to_manual_referral(self):
        report = self._load_report("G2")
        self.assertEqual(report["are_total"], 77)
        decision = admittance.evaluate(report)
        self.assertEqual(decision.admittance_class, admittance.REFER_TO_UW)
        self.assertEqual(decision.firing_rule_id, "ADM.R0")

    def test_G3_safety_critical_na_routes_to_manual_referral(self):
        report = self._load_report("G3")
        self.assertEqual(report["are_total"], 73)
        decision = admittance.evaluate(report)
        self.assertEqual(decision.admittance_class, admittance.REFER_TO_UW)
        self.assertEqual(decision.firing_rule_id, "ADM.R0")

    def test_B3_refuse_r1_no_premium(self):
        report = self._load_report("B3")
        self.assertEqual(report["are_total"], 63)
        decision = admittance.evaluate(report)
        self.assertEqual(decision.admittance_class, admittance.REFUSE)
        self.assertEqual(decision.firing_rule_id, "ADM.R1")
        with self.assertRaises(Ineligible) as ctx:
            quote(_score(report["are_total"]), self._customer(),
                  rates=self.rates, admittance_decision=decision)
        self.assertEqual(ctx.exception.rule_id, "ADM.R1")


class AdmittanceBackwardCompatTests(unittest.TestCase):
    """Legacy callers (admittance_decision=None) get byte-identical quotes."""

    def setUp(self):
        self.rates = Rates.load(RATES_PATH)

    def test_legacy_quote_unchanged_when_no_admittance(self):
        q = quote(_score(78), STD_CUSTOMER, rates=self.rates)
        # Same Profile-1-ish input — premium and breakdown must match the
        # pre-ADR-0007 path; no admittance keys leak into the breakdown.
        self.assertNotIn("admittance", q["breakdown"])
        self.assertNotIn("f_caveat", q["breakdown"])
        codes = [e["code"] for e in q["endorsements"]]
        self.assertNotIn("coverage_caveat_exclusion", codes)
        self.assertNotIn("coverage_caveat_transparency", codes)


class FCaveatCalibrationGuardTests(unittest.TestCase):
    """ADR-0007 §1.3 / §6.1 / §Authority: f_caveat is a frozen placeholder.

    Calibration after 30 production CONSTRAINED_BIND quotes routes through
    HUW rule-change call per `contracts/uw-rubric-supervision-v1.md@v1.0`
    row 30. This test pins the formula so a unilateral change can't ship.
    """

    def test_f_caveat_formula_is_placeholder(self):
        from pricing.quote import _f_caveat  # type: ignore[attr-defined]

        self.assertAlmostEqual(_f_caveat(0), 1.00)
        self.assertAlmostEqual(_f_caveat(1), 1.05)
        self.assertAlmostEqual(_f_caveat(2), 1.10)
        self.assertAlmostEqual(_f_caveat(5), 1.25)


if __name__ == "__main__":
    unittest.main()
