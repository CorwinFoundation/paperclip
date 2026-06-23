"""BEAAA-7937 (Pass-4 §1.1) — admittance enforcement on the production bind path.

The admittance dial (ADR-0007 §1.3, incl. the ADM.R0 critical-dim knockout from
ADR-0038 / BEAAA-7888) is consumed inside `pricing.quote.quote(...)` ONLY when a
caller threads an `admittance_decision`. The Pass-4 review found the production
`/quote` path (`api.handlers.quote_handler`) called `quote(...)` WITHOUT it, so
the entire REFUSE/REFER schedule was inert at the HTTP boundary — a non-bindable
agent rode the legacy D-band gate to a premium.

These tests are the standing guard: the handler MUST construct an
`AdmittanceDecision` and pass it on every bind path. The `test_*_guard` case
fails if anyone ever reverts the wiring so `quote(...)` is called without a
decision.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from pricing.quote import Rates
from rubric.bands import assign_band, band_to_dict
from underwriting import admittance

from api import handlers

REPO = Path(__file__).resolve().parents[1]
RATES_PATH = REPO / "pricing" / "rates.yaml"

STD_CUSTOMER = {
    "sector": "ENT_SAAS",
    "annual_revenue_usd": 100_000_000,
    "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
}


def _score(are_total: int, *, na_dims=None, measured_dims=None,
           pillars_with_all_na=None) -> dict:
    s: dict = {"bmi": are_total, "band": band_to_dict(assign_band(are_total))}
    if measured_dims is not None or na_dims is not None:
        s["applicability_record"] = {
            "measured_dims": list(measured_dims or []),
            "na_dims": list(na_dims or []),
        }
    if pillars_with_all_na is not None or na_dims is not None:
        s["diagnostics"] = [{
            "code": "coverage_caveat",
            "detail": {
                "na_dims": list(na_dims or []),
                "pillars_with_all_na": list(pillars_with_all_na or []),
            },
        }]
    return s


class QuoteHandlerAdmittanceWiringTests(unittest.TestCase):
    def setUp(self):
        self.rates = Rates.load(RATES_PATH)

    # ---- the standing guard ------------------------------------------------

    def test_quote_handler_always_threads_admittance_decision_guard(self):
        """FAILS if quote_handler ever calls quote() without admittance_decision.

        This is the load-bearing regression lock for Pass-4 §1.1: it does not
        care which class fires, only that the bind path can never reach the
        pricing engine without an admittance decision.
        """
        captured: dict = {}
        real_quote = handlers.quote

        def spy(*args, **kwargs):
            captured["admittance_decision"] = kwargs.get("admittance_decision", "ABSENT")
            return real_quote(*args, **kwargs)

        handlers.quote = spy
        try:
            handlers.quote_handler(
                {"score": _score(78, measured_dims=list("abcdefg"), na_dims=["X", "Y"]),
                 "customer": STD_CUSTOMER},
                rates=self.rates,
            )
        finally:
            handlers.quote = real_quote

        self.assertIn("admittance_decision", captured)
        self.assertNotEqual(captured["admittance_decision"], "ABSENT",
                            "quote() was called WITHOUT admittance_decision on the bind path")
        self.assertIsNotNone(captured["admittance_decision"],
                             "admittance_decision threaded as None on the bind path")
        self.assertIsInstance(captured["admittance_decision"], admittance.AdmittanceDecision)

    def test_missing_applicability_record_is_contract_clean_422(self):
        """BEAAA-7939 UW condition: a missing admittance evidence bundle is a
        contract error (422), NOT a silent ADM.R3 REFUSE. The bare Scanner
        /score ScoreResponse carries no applicability_record; the UW submission
        record assembles it (ADR-0007 §1.4)."""
        from api.handlers import HandlerError

        with self.assertRaises(HandlerError) as ctx:
            handlers.quote_handler(
                {"score": {"bmi": 78}, "customer": STD_CUSTOMER},
                rates=self.rates,
            )
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("applicability_record", ctx.exception.message)

    # ---- end-to-end enforcement through the real handler -------------------

    def test_refuse_below_50_returns_ineligible(self):
        # ADM.R5: are_total < 50 → REFUSE. Legacy path would have priced this.
        status, body = handlers.quote_handler(
            {"score": _score(42, measured_dims=list("abcdef"), na_dims=[]),
             "customer": STD_CUSTOMER},
            rates=self.rates,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ineligible")
        self.assertEqual(body["firing_rule_id"], "ADM.R5")
        self.assertIsNone(body["annual_premium_usd"])

    def test_anti_safety_posture_returns_ineligible_r1(self):
        # ADM.R1: anti_safety posture → REFUSE regardless of score. posture is
        # carried on the request (UW submission record), not the score envelope.
        status, body = handlers.quote_handler(
            {"score": _score(72, measured_dims=list("abcdefg"), na_dims=["X", "Y"]),
             "customer": STD_CUSTOMER,
             "system_prompt_kind": "anti_safety"},
            rates=self.rates,
        )
        self.assertEqual(body["status"], "ineligible")
        self.assertEqual(body["firing_rule_id"], "ADM.R1")

    def test_50_to_69_routes_manual_referral_r6(self):
        # ADM.R6: are_total in [50,69] → REFER_TO_UW (manual queue, no auto-bind).
        status, body = handlers.quote_handler(
            {"score": _score(63, measured_dims=list("abcdef"), na_dims=[]),
             "customer": STD_CUSTOMER},
            rates=self.rates,
        )
        self.assertEqual(body["status"], "manual_referral")
        self.assertIsNone(body["annual_premium_usd"])

    def test_constrained_bind_r7_still_binds_with_caveat(self):
        # ADM.R7: [70,84] AND measured>=6 → CONSTRAINED_BIND. A bindable agent
        # still gets a premium; admittance enforcement does not block it.
        measured = list("abcdefg")
        na = ["X", "Y"]
        status, body = handlers.quote_handler(
            {"score": _score(79, measured_dims=measured, na_dims=na),
             "customer": STD_CUSTOMER},
            rates=self.rates,
        )
        self.assertEqual(status, 200)
        self.assertIsNotNone(body.get("annual_premium_usd"))
        self.assertEqual(body["breakdown"]["admittance"]["admittance_class"],
                         "CONSTRAINED_BIND")
        codes = [e["code"] for e in body["endorsements"]]
        self.assertIn("coverage_caveat_exclusion", codes)


if __name__ == "__main__":
    unittest.main()
