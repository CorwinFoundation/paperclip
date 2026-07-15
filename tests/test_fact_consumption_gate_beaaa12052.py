"""BEAAA-12052 — ADR-0053 ADM.R0.FACT deployment_measured consumption gate.

Wires the HUW consumption-gate spec (uw-fact-consumption-gate-v0-1, BEAAA-12051):

  Condition 1 — per-submission provenance. A `deployment_measured`
  tool-grounded-factuality row is full-limit business-factual eligible ONLY when
  it characterizes the submission's OWN insured agent run against a REPRESENTATIVE
  deployment task set. Missing / unknown / reference_fixture provenance, or an
  agent_ref that does not match the submission's insured agent, fails closed —
  ADM.R0.FACT fires REFER_TO_UW (treated as indicative for this bind).

  Condition 2 — interim Misattribution-of-Value & Derived-Figure Exclusion.
  A full-limit business-factual bind that rests on a v1 (`verifier_version < 2`)
  `deployment_measured` row carries the named exclusion endorsement in the quote,
  sunsetting automatically at verifier_version >= 2.

Reproducibility invariant: this is an admittance/pricing dial, not a scoring
change. behavioral_integrity.scored stays false; are_total / band letters /
pillar weights are untouched; the golden roster carries no deployment_measured
business-factual rows, so its dispositions and premiums are unchanged.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from pricing.quote import Rates, quote
from rubric.bands import assign_band, band_to_dict
from underwriting import admittance

REPO = Path(__file__).resolve().parents[1]
RATES_PATH = REPO / "pricing" / "rates.yaml"

_MEASURED = ["P2.1", "P2.3", "P2.4", "P2.5", "P2.6", "P3.1", "P3.3"]
_INSURED = "agent-sha256-abc123"


def _all_enabled() -> dict[str, bool]:
    return {rid: True for rid in (
        "ADM.R0", "ADM.R1", "ADM.R2", "ADM.R3", "ADM.R4", "ADM.R5", "ADM.R6",
        "ADM.R0.STOP", "ADM.R0.TXN", "ADM.R0.FACT", "ADM.R7", "ADM.R8",
    )}


def _report(tool_grounded: dict | None) -> dict:
    """Band-A bind candidate (are_total=92, na<=2) with an optional
    tool_grounded_factuality block on the reported-not-scored envelope."""
    out: dict = {
        "are_total": 92,
        "system_prompt_kind": "hardened",
        "scan_autonomy": "tool_enabled",
        "applicability_record": {"measured_dims": list(_MEASURED), "na_dims": []},
        "diagnostics": [{"code": "coverage_caveat", "detail": {"pillars_with_all_na": []}}],
    }
    if tool_grounded is not None:
        out["behavioral_integrity"] = {
            "family": "BEHAVIORAL-INTEGRITY",
            "scored": False,
            "dimensions": {"tool_grounded_factuality": tool_grounded},
        }
    return out


def _representative(agent_ref: str = _INSURED, verifier_version: int | None = 1) -> dict:
    row = {
        "evidence_grade": "deployment_measured",
        "provenance": {
            "source": "deployment_representative",
            "agent_ref": agent_ref,
            "task_set_ref": "biz-factual-set-7",
            "task_set_kind": "representative_deployment",
        },
    }
    if verifier_version is not None:
        row["verifier_version"] = verifier_version
    return row


def _fixture() -> dict:
    return {
        "evidence_grade": "deployment_measured",
        "provenance": {
            "source": "reference_fixture",
            "agent_ref": "reference-faithful-fixture",
            "task_set_ref": "verifier-validation-fixture",
            "task_set_kind": "reference_fixture",
        },
    }


class Condition1ProvenanceGate(unittest.TestCase):
    """Done-when #1 — the provenance predicate fail-closes on everything except
    an own-agent representative deployment_measured row."""

    def _eval(self, report: dict, insured: str | None = _INSURED):
        return admittance.evaluate(
            report, insured_agent_ref=insured, rules_config=_all_enabled()
        )

    # --- the two Done-when #1 assertions, verbatim from the spec §4 ---
    def test_fixture_sourced_row_refers_not_full_limit(self):
        d = self._eval(_report(_fixture()))
        self.assertEqual(d.firing_rule_id, "ADM.R0.FACT")
        self.assertEqual(d.admittance_class, admittance.REFER_TO_UW)
        self.assertEqual(d.scope, "business_factual")

    def test_own_representative_row_is_full_limit(self):
        d = self._eval(_report(_representative()))
        self.assertEqual(d.firing_rule_id, "ADM.R8")
        self.assertEqual(d.admittance_class, admittance.AUTO_BIND)

    # --- fail-closed edge cases ---
    def test_missing_provenance_fails_closed(self):
        d = self._eval(_report({"evidence_grade": "deployment_measured"}))
        self.assertEqual(d.firing_rule_id, "ADM.R0.FACT")
        self.assertEqual(d.admittance_class, admittance.REFER_TO_UW)

    def test_agent_ref_mismatch_fails_closed(self):
        d = self._eval(_report(_representative(agent_ref="some-other-agent")))
        self.assertEqual(d.firing_rule_id, "ADM.R0.FACT")

    def test_absent_insured_agent_ref_fails_closed(self):
        # No submission insured-agent ref to match against ⇒ cannot confirm the
        # row is the own agent ⇒ fail-closed.
        d = self._eval(_report(_representative()), insured=None)
        self.assertEqual(d.firing_rule_id, "ADM.R0.FACT")

    def test_agent_id_on_envelope_is_used_as_fallback(self):
        report = _report(_representative())
        report["agent_id"] = _INSURED
        d = admittance.evaluate(report, rules_config=_all_enabled())
        self.assertEqual(d.firing_rule_id, "ADM.R8")

    def test_indicative_still_refers_unchanged(self):
        d = self._eval(_report({"evidence_grade": "indicative"}))
        self.assertEqual(d.firing_rule_id, "ADM.R0.FACT")
        self.assertEqual(d.admittance_class, admittance.REFER_TO_UW)

    def test_no_tool_grounded_row_leaves_rule_inactive(self):
        # No business-factual exposure asserted ⇒ the rule does not fire; the
        # bind proceeds on band alone (this is the golden-roster shape).
        d = self._eval(_report(None))
        self.assertEqual(d.firing_rule_id, "ADM.R8")
        self.assertEqual(d.admittance_class, admittance.AUTO_BIND)
        self.assertFalse(d.business_factual_v1_exclusion_required)


class Condition2ExclusionFlag(unittest.TestCase):
    """The decision carries the misattribution-exclusion basis only for a v1
    full-limit business-factual bind, and sunsets it at verifier_version >= 2."""

    def _eval(self, report: dict):
        return admittance.evaluate(
            report, insured_agent_ref=_INSURED, rules_config=_all_enabled()
        )

    def test_v1_row_sets_exclusion_flag(self):
        d = self._eval(_report(_representative(verifier_version=1)))
        self.assertEqual(d.admittance_class, admittance.AUTO_BIND)
        self.assertTrue(d.business_factual_v1_exclusion_required)

    def test_absent_verifier_version_defaults_to_v1(self):
        d = self._eval(_report(_representative(verifier_version=None)))
        self.assertTrue(d.business_factual_v1_exclusion_required)

    def test_v2_row_sunsets_exclusion_flag(self):
        d = self._eval(_report(_representative(verifier_version=2)))
        self.assertEqual(d.admittance_class, admittance.AUTO_BIND)
        self.assertFalse(d.business_factual_v1_exclusion_required)

    def test_fixture_row_carries_no_exclusion_flag(self):
        # It refers (never reaches full-limit), so no exclusion basis exists.
        d = self._eval(_report(_fixture()))
        self.assertFalse(d.business_factual_v1_exclusion_required)


class Condition2EndorsementWiring(unittest.TestCase):
    """Done-when #2 (path b) — the exclusion endorsement is attached to the quote
    for a v1 full-limit business-factual bind, and absent otherwise."""

    def setUp(self):
        self.rates = Rates.load(RATES_PATH)
        self.customer = {
            "sector": "ENT_SAAS",
            "annual_revenue_usd": 100_000_000,
            "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
        }

    def _score(self) -> dict:
        return {
            "bmi": 92,
            "are_total": 92,
            "band": band_to_dict(assign_band(92)),
            "applicability_record": {"measured_dims": list(_MEASURED), "na_dims": []},
            "diagnostics": [{"code": "coverage_caveat", "detail": {"na_dims": [], "pillars_with_all_na": []}}],
        }

    def test_v1_bind_attaches_exclusion_and_no_premium_change(self):
        decision = admittance.evaluate(
            _report(_representative(verifier_version=1)),
            insured_agent_ref=_INSURED,
        )
        self.assertEqual(decision.admittance_class, admittance.AUTO_BIND)
        q_ref = quote(self._score(), self.customer, rates=self.rates)
        q = quote(self._score(), self.customer, rates=self.rates, admittance_decision=decision)
        codes = [e["code"] for e in q["endorsements"]]
        self.assertIn("misattribution_derived_figure_exclusion", codes)
        row = next(e for e in q["endorsements"] if e["code"] == "misattribution_derived_figure_exclusion")
        self.assertEqual(row["load_usd"], 0)
        # Exclusion narrows coverage; it does not move the premium.
        self.assertEqual(q["annual_premium_usd"], q_ref["annual_premium_usd"])

    def test_v2_bind_omits_exclusion(self):
        decision = admittance.evaluate(
            _report(_representative(verifier_version=2)),
            insured_agent_ref=_INSURED,
        )
        q = quote(self._score(), self.customer, rates=self.rates, admittance_decision=decision)
        codes = [e["code"] for e in q["endorsements"]]
        self.assertNotIn("misattribution_derived_figure_exclusion", codes)

    def test_no_business_factual_row_omits_exclusion(self):
        decision = admittance.evaluate(_report(None), insured_agent_ref=_INSURED)
        q = quote(self._score(), self.customer, rates=self.rates, admittance_decision=decision)
        codes = [e["code"] for e in q["endorsements"]]
        self.assertNotIn("misattribution_derived_figure_exclusion", codes)


if __name__ == "__main__":
    unittest.main()
