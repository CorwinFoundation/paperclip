"""BEAAA-70 v0.1.1 additive-fields round-trip test.

Verifies the three additive (non-breaking) fields land end-to-end through
score -> /quote without changing existing pricing math:

  1. ScoreResponse.pillars[Pn].dimensions[did].applied_threshold
  2. ScoreResponse.scan_evidence_age_days
  3. ScoreResponse.pillar_floors

Hard deadline: ship before 2026-05-09 v1.0 contract lock (BEAAA-21).
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api.bundle import BundleStore
from api.handlers import quote_handler, score_handler
from api.intake_guard import HOSTED_RUNNER
from pricing.quote import Rates
from rubric.scoring import Rubric

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
RATES_PATH = REPO / "pricing" / "rates.yaml"

TENANT = "44444444-4444-4444-4444-444444444444"
RUN_ID = "55555555-5555-5555-5555-555555555555"
SNAPSHOT_HASH = "sha256:" + "b" * 64
SCORED_AT = datetime(2026, 5, 1, tzinfo=timezone.utc)


def _eval_outputs(rubric: Rubric, raw: float) -> dict:
    return {
        did: {
            "raw": raw,
            "evidence_uri": f"https://evidence.local/{did}",
            "runner": HOSTED_RUNNER,
            "eval_run_id": RUN_ID,
        }
        for did in rubric.active_dim_ids
    }


def _score_request(rubric: Rubric, raw: float = 3.0) -> dict:
    return {
        "tenant_id": TENANT,
        "snapshot": {
            "model_id": "v01-1-additive",
            "model_version": "v1",
            "prompt_version": "p1",
            "snapshot_hash": SNAPSHOT_HASH,
        },
        "eval_outputs": _eval_outputs(rubric, raw),
    }


class V011AdditiveScoreFieldsTests(unittest.TestCase):
    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)
        self.bundles = BundleStore()

    def _score(self, raw: float = 3.0):
        status, body = score_handler(
            _score_request(self.rubric, raw=raw),
            tenant_header=TENANT,
            rubric=self.rubric,
            bundles=self.bundles,
            scored_at=SCORED_AT,
        )
        self.assertEqual(status, 200)
        return body

    def test_applied_threshold_present_on_every_dim(self):
        body = self._score()
        for pid, pillar in body["pillars"].items():
            for did, dim in pillar["dimensions"].items():
                self.assertIn("applied_threshold", dim, f"{did} missing applied_threshold")
                t = dim["applied_threshold"]
                self.assertIn("min_raw", t)
                self.assertIn("kind", t)
                self.assertIn("rationale", t)
                self.assertIn(t["kind"], {"advisory", "bind_blocking_floor"})

    def test_applied_threshold_p1_p3_are_bind_blocking(self):
        body = self._score()
        for pid in ("P1", "P3"):
            for did, dim in body["pillars"][pid]["dimensions"].items():
                self.assertEqual(
                    dim["applied_threshold"]["kind"], "bind_blocking_floor",
                    f"{did} should be bind_blocking_floor in {pid}",
                )
                self.assertEqual(dim["applied_threshold"]["min_raw"], 0.01)

    def test_applied_threshold_p2_p4_are_advisory(self):
        body = self._score()
        for pid in ("P2", "P4"):
            for did, dim in body["pillars"][pid]["dimensions"].items():
                self.assertEqual(dim["applied_threshold"]["kind"], "advisory", did)
                self.assertEqual(dim["applied_threshold"]["min_raw"], 0.0)

    def test_scan_evidence_age_days_zero_at_score_time(self):
        body = self._score()
        self.assertEqual(body["scan_evidence_age_days"], 0)

    def test_pillar_floors_present_with_known_p3_p4_values(self):
        body = self._score()
        floors = body["pillar_floors"]
        self.assertEqual(set(floors), {"P1", "P2", "P3", "P4"})
        self.assertEqual(floors["P3"]["min_pillar_score"], 11.25)
        self.assertEqual(floors["P4"]["min_pillar_score"], 7.5)
        # Floors carry rationale strings for report-card use.
        for pid in floors:
            self.assertIsInstance(floors[pid]["rationale"], str)
            self.assertGreater(len(floors[pid]["rationale"]), 0)

    def test_rubric_version_is_011(self):
        body = self._score()
        self.assertEqual(body["rubric_version"], "0.1.1")


class V011AdditiveQuoteRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)
        self.bundles = BundleStore()
        self.rates = Rates.load(RATES_PATH)

    def _score(self, raw: float = 3.0, scored_at=SCORED_AT):
        status, body = score_handler(
            _score_request(self.rubric, raw=raw),
            tenant_header=TENANT,
            rubric=self.rubric,
            bundles=self.bundles,
            scored_at=scored_at,
        )
        self.assertEqual(status, 200)
        return body

    def test_quote_roundtrips_pillar_floors_and_age_days(self):
        score_body = self._score(raw=3.0)
        quoted_at = SCORED_AT + timedelta(days=37)
        status, quote_body = quote_handler(
            {
                "score": score_body,
                "customer": {
                    "sector": "ENT_SAAS",
                    "annual_revenue_usd": 100_000_000,
                    "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
                },
            },
            rates=self.rates,
            quoted_at=quoted_at,
        )
        self.assertEqual(status, 200)
        self.assertEqual(quote_body["status"], "indicative")
        # scan_evidence_age_days is recomputed from scored_at at quote time.
        self.assertEqual(quote_body["breakdown"]["scan_evidence_age_days"], 37)
        # pillar_floors round-trip unchanged.
        self.assertEqual(
            quote_body["breakdown"]["pillar_floors"]["P3"]["min_pillar_score"],
            11.25,
        )

    def test_quote_age_days_floors_at_zero_for_clock_skew(self):
        score_body = self._score(raw=3.0)
        # Reader clock behind scorer (e.g. NTP skew). Must not go negative.
        quoted_at = SCORED_AT - timedelta(days=2)
        status, quote_body = quote_handler(
            {
                "score": score_body,
                "customer": {
                    "sector": "ENT_SAAS",
                    "annual_revenue_usd": 100_000_000,
                    "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
                },
            },
            rates=self.rates,
            quoted_at=quoted_at,
        )
        self.assertEqual(status, 200)
        self.assertEqual(quote_body["breakdown"]["scan_evidence_age_days"], 0)

    def test_quote_age_days_none_when_score_predates_v011(self):
        # Older Scanner emit without scored_at — quote engine must not crash.
        legacy_score = {
            "bmi": 75,
            "band": {"letter": "B", "modifier": "neutral", "modifier_factor": 1.0},
        }
        status, quote_body = quote_handler(
            {
                "score": legacy_score,
                "customer": {
                    "sector": "ENT_SAAS",
                    "annual_revenue_usd": 100_000_000,
                    "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
                },
            },
            rates=self.rates,
        )
        self.assertEqual(status, 200)
        self.assertIsNone(quote_body["breakdown"]["scan_evidence_age_days"])
        self.assertIsNone(quote_body["breakdown"]["pillar_floors"])

    def test_pricing_math_unchanged_by_v011_additive_fields(self):
        # Same Profile-1 BMI 78 / B neutral / ENT_SAAS / $500M / $2M-$5M shape
        # the existing pricing tests assert at $60,775. v0.1.1 must not move it.
        score_body = {
            "bmi": 78,
            "band": {"letter": "B", "modifier": "neutral", "modifier_factor": 1.0},
            "scored_at": SCORED_AT.isoformat(),
            "scan_evidence_age_days": 0,
            "pillar_floors": {
                "P1": {"min_pillar_score": 0, "rationale": "test"},
                "P3": {"min_pillar_score": 11.25, "rationale": "test"},
            },
        }
        status, quote_body = quote_handler(
            {
                "score": score_body,
                "customer": {
                    "sector": "ENT_SAAS",
                    "annual_revenue_usd": 500_000_000,
                    "limit": {"per_claim_usd": 2_000_000, "aggregate_usd": 5_000_000},
                },
            },
            rates=self.rates,
            quoted_at=SCORED_AT,
        )
        self.assertEqual(status, 200)
        self.assertEqual(quote_body["annual_premium_usd"], 60_775)


if __name__ == "__main__":
    unittest.main()
