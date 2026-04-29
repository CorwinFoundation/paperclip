"""Pricing v1 quote tests — re-derives the 5 BEAAA-10 §9 stress profiles
from the formula and asserts the breakdown.

Note on doc rounding deltas: the pricing-v1 §9 doc shows headline figures
rounded to the dollar. Profiles 1, 2, 4 reproduce exactly; profiles 3 and 5
have small intermediate-rounding deltas in the doc table (Profile 3 doc
$206,202 vs formula $206,168; Profile 5 doc $33,907 vs formula $33,912).
These tests assert the formula — single source of truth — and flag the
deltas in comments so the doc owner can tighten the table.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from pricing.quote import ManualReferral, QuoteError, Rates, quote
from rubric.bands import assign_band, band_to_dict

REPO = Path(__file__).resolve().parents[1]
RATES_PATH = REPO / "pricing" / "rates.yaml"


def _score(bmi: int) -> dict:
    return {"bmi": bmi, "band": band_to_dict(assign_band(bmi))}


class StressProfileTests(unittest.TestCase):
    def setUp(self):
        self.rates = Rates.load(RATES_PATH)

    def test_profile_1_tier1_enterprise_b_neutral(self):
        # BMI 78 → B neutral. ENT_SAAS, $500M revenue, $2M/$5M.
        # 25000 × 1.00 × 1.00 × 1.00 × 1.30 × 1.70 × 1.10 = $60,775.
        q = quote(
            _score(78),
            {
                "sector": "ENT_SAAS",
                "annual_revenue_usd": 500_000_000,
                "limit": {"per_claim_usd": 2_000_000, "aggregate_usd": 5_000_000},
            },
            rates=self.rates,
        )
        self.assertEqual(q["status"], "indicative")
        self.assertEqual(q["annual_premium_usd"], 60_775)
        self.assertEqual(q["endorsements"], [])
        self.assertEqual(q["breakdown"]["band_letter"], "B")
        self.assertEqual(q["breakdown"]["modifier"], "neutral")

    def test_profile_2_b2b_ai_saas_b_plus(self):
        # BMI 82 → B+. B2B_AI_SAAS, $75M revenue, $1M/$2M.
        # 25000 × 1.00 × 0.93 × 1.10 × 1.00 × 1.00 × 1.10 = $28,132.5 → $28,133.
        q = quote(
            _score(82),
            {
                "sector": "B2B_AI_SAAS",
                "annual_revenue_usd": 75_000_000,
                "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
            },
            rates=self.rates,
        )
        self.assertEqual(q["annual_premium_usd"], 28_133)
        self.assertEqual(q["breakdown"]["modifier"], "+")
        self.assertAlmostEqual(q["breakdown"]["modifier_factor"], 0.93)

    def test_profile_3_healthcare_c_neutral(self):
        # BMI 68 → C+, but pricing keys off lower-bound (64 = C neutral).
        # Caller passes the lower-bound band per pricing-v1 §3.
        # 25000 × 1.80 × 1.00 × 1.40 × 1.75 × 1.70 × 1.10 = $206,167.5 → $206,168.
        q = quote(
            _score(64),  # lower bound
            {
                "sector": "HEALTHCARE",
                "annual_revenue_usd": 1_200_000_000,
                "limit": {"per_claim_usd": 2_000_000, "aggregate_usd": 5_000_000},
            },
            rates=self.rates,
        )
        self.assertEqual(q["annual_premium_usd"], 206_168)
        # Letter C → remediation auto-added.
        codes = [e["code"] for e in q["endorsements"]]
        self.assertIn("remediation", codes)
        self.assertEqual(q["endorsements_total_usd"], 2500)
        self.assertEqual(q["total_with_endorsements_usd"], 208_668)

    def test_profile_4_northstar_c_minus_lower_bound(self):
        # BMI 62 (±4) → point C neutral, lower bound 58 → C−.
        # 25000 × 1.80 × 1.07 × 1.00 × 1.00 × 1.00 × 1.10 = $52,965.
        q = quote(
            _score(58),
            {
                "sector": "ENT_SAAS",
                "annual_revenue_usd": 100_000_000,
                "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
            },
            rates=self.rates,
        )
        self.assertEqual(q["annual_premium_usd"], 52_965)
        self.assertEqual(q["breakdown"]["modifier"], "-")
        self.assertEqual(q["total_with_endorsements_usd"], 55_465)

    def test_profile_5_preferred_a_plus(self):
        # BMI 95 → A+. ENT_SAAS, $250M, $2M/$5M.
        # 25000 × 0.60 × 0.93 × 1.00 × 1.30 × 1.70 × 1.10 = $33,912.45 → $33,912.
        q = quote(
            _score(95),
            {
                "sector": "ENT_SAAS",
                "annual_revenue_usd": 250_000_000,
                "limit": {"per_claim_usd": 2_000_000, "aggregate_usd": 5_000_000},
            },
            rates=self.rates,
        )
        self.assertEqual(q["annual_premium_usd"], 33_912)
        self.assertEqual(q["breakdown"]["band_letter"], "A")
        self.assertEqual(q["breakdown"]["modifier"], "+")
        self.assertEqual(q["endorsements"], [])


class ManualReferralTests(unittest.TestCase):
    def setUp(self):
        self.rates = Rates.load(RATES_PATH)
        self.std_customer = {
            "sector": "ENT_SAAS",
            "annual_revenue_usd": 100_000_000,
            "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
        }

    def test_d_band_referral(self):
        with self.assertRaises(ManualReferral) as ctx:
            quote(_score(40), self.std_customer, rates=self.rates)
        self.assertIn("letter D", ctx.exception.reason)
        self.assertEqual(ctx.exception.breakdown["band_letter"], "D")

    def test_other_sector_referral(self):
        c = dict(self.std_customer, sector="OTHER")
        with self.assertRaises(ManualReferral):
            quote(_score(75), c, rates=self.rates)

    def test_oversize_revenue_referral(self):
        c = dict(self.std_customer, annual_revenue_usd=10_000_000_000)
        with self.assertRaises(ManualReferral):
            quote(_score(75), c, rates=self.rates)

    def test_unsupported_limit_referral(self):
        c = dict(self.std_customer, limit={"per_claim_usd": 25_000_000, "aggregate_usd": 50_000_000})
        with self.assertRaises(ManualReferral):
            quote(_score(75), c, rates=self.rates)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.rates = Rates.load(RATES_PATH)

    def test_missing_band_raises(self):
        with self.assertRaisesRegex(QuoteError, "band"):
            quote(
                {"bmi": 75},
                {
                    "sector": "ENT_SAAS",
                    "annual_revenue_usd": 100_000_000,
                    "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
                },
                rates=self.rates,
            )

    def test_unknown_sector_raises(self):
        with self.assertRaisesRegex(QuoteError, "unknown sector"):
            quote(
                _score(75),
                {
                    "sector": "MARS_COLONY",
                    "annual_revenue_usd": 100_000_000,
                    "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
                },
                rates=self.rates,
            )

    def test_missing_customer_field_raises(self):
        with self.assertRaisesRegex(QuoteError, "customer.sector required"):
            quote(
                _score(75),
                {"annual_revenue_usd": 100_000_000, "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000}},
                rates=self.rates,
            )

    def test_unknown_endorsement_raises(self):
        with self.assertRaisesRegex(QuoteError, "unknown endorsement"):
            quote(
                _score(75),
                {
                    "sector": "ENT_SAAS",
                    "annual_revenue_usd": 100_000_000,
                    "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
                    "endorsements": ["unicorn_load"],
                },
                rates=self.rates,
            )


class EndorsementTests(unittest.TestCase):
    def setUp(self):
        self.rates = Rates.load(RATES_PATH)

    def test_band_c_auto_adds_remediation(self):
        q = quote(
            _score(60),
            {
                "sector": "ENT_SAAS",
                "annual_revenue_usd": 100_000_000,
                "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
            },
            rates=self.rates,
        )
        rem = next(e for e in q["endorsements"] if e["code"] == "remediation")
        self.assertTrue(rem["auto_added"])

    def test_band_c_explicit_remediation_not_double_counted(self):
        q = quote(
            _score(60),
            {
                "sector": "ENT_SAAS",
                "annual_revenue_usd": 100_000_000,
                "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
                "endorsements": ["remediation"],
            },
            rates=self.rates,
        )
        rems = [e for e in q["endorsements"] if e["code"] == "remediation"]
        self.assertEqual(len(rems), 1)

    def test_optional_endorsement_added(self):
        q = quote(
            _score(75),
            {
                "sector": "ENT_SAAS",
                "annual_revenue_usd": 100_000_000,
                "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
                "endorsements": ["regulatory_fines_buyback"],
            },
            rates=self.rates,
        )
        self.assertEqual(q["endorsements_total_usd"], 5000)
        self.assertEqual(q["total_with_endorsements_usd"], q["annual_premium_usd"] + 5000)


class HandlerIntegrationTests(unittest.TestCase):
    """The /quote endpoint adapter — manual_referral surfaces as 200 with status field."""

    def setUp(self):
        from api.handlers import quote_handler
        self.quote_handler = quote_handler
        self.rates = Rates.load(RATES_PATH)

    def test_manual_referral_returns_200_with_status(self):
        status, body = self.quote_handler(
            {
                "score": _score(40),  # D band
                "customer": {
                    "sector": "ENT_SAAS",
                    "annual_revenue_usd": 100_000_000,
                    "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
                },
            },
            rates=self.rates,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "manual_referral")
        self.assertIsNone(body["annual_premium_usd"])
        self.assertIn("letter D", body["reason"])

    def test_indicative_returns_200_with_premium(self):
        status, body = self.quote_handler(
            {
                "score": _score(78),
                "customer": {
                    "sector": "ENT_SAAS",
                    "annual_revenue_usd": 500_000_000,
                    "limit": {"per_claim_usd": 2_000_000, "aggregate_usd": 5_000_000},
                },
            },
            rates=self.rates,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "indicative")
        self.assertEqual(body["annual_premium_usd"], 60_775)


if __name__ == "__main__":
    unittest.main()
