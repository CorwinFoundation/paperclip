"""BEAAA-70 v0.1.1 + BEAAA-71 v0.1.2 additive-fields round-trip test.

Verifies the additive (non-breaking) contract fields land end-to-end through
score -> /quote without changing existing pricing math:

v0.1.1 (BEAAA-70):
  1. ScoreResponse.pillars[Pn].dimensions[did].applied_threshold
  2. ScoreResponse.scan_evidence_age_days
  3. ScoreResponse.pillar_floors

v0.1.2 (BEAAA-71):
  4. applied_threshold.kind enum gains "warranty_trigger" (§2.5 class 2);
     enum value defined, no v0.1.1-bound dims auto-relabeled.
  5. pillar_floors.P3 gains min_dim_raw = 1.5 sibling (per-dim weak-link
     guarantee on adversarial-robustness dims).

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
        "corpus_version": "test@0.0.0",
        "tenant_id": TENANT,
        "snapshot": {
            "model_id": "v01-1-additive",
            "model_version": "v1",
            "prompt_version": "p1",
            "snapshot_hash": SNAPSHOT_HASH,
        },
        "eval_outputs": _eval_outputs(rubric, raw),
    }


# BEAAA-7937: the production /quote path now enforces the ADR-0007 admittance
# dial, so a /quote request must carry the admittance evidence bundle (assembled
# by the UW submission record, ADR-0007 §1.4 — the bare /score response does not
# carry it). These round-trip tests assert pricing/field behavior, not admittance
# outcomes, so we attach a bindable bundle (6 measured dims, no N/A dims, hardened
# posture): a [70,84] score lands ADM.R7 CONSTRAINED_BIND with f_caveat=1.0 →
# premium and breakdown fields unchanged vs the legacy path.
def _with_admittance(score: dict) -> dict:
    return {
        **score,
        "applicability_record": {
            "measured_dims": ["P2.1", "P2.3", "P2.4", "P2.5", "P2.6", "P3.1"],
            "na_dims": [],
        },
        "system_prompt_kind": "hardened",
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
                # v0.1.2 (BEAAA-71): warranty_trigger added to admissible enum;
                # no v0.1.1-bound dim is auto-relabeled, but the value is now
                # legal everywhere kind appears.
                self.assertIn(t["kind"], {"advisory", "bind_blocking_floor", "warranty_trigger"})

    def test_applied_threshold_p1_p3_are_bind_blocking(self):
        body = self._score()
        for pid in ("OPS", "ROBUSTNESS"):
            for did, dim in body["pillars"][pid]["dimensions"].items():
                self.assertEqual(
                    dim["applied_threshold"]["kind"], "bind_blocking_floor",
                    f"{did} should be bind_blocking_floor in {pid}",
                )
                self.assertEqual(dim["applied_threshold"]["min_raw"], 0.01)

    def test_applied_threshold_p2_p4_are_advisory(self):
        body = self._score()
        for pid in ("BIAS_FAIRNESS", "GOVERNANCE"):
            for did, dim in body["pillars"][pid]["dimensions"].items():
                self.assertEqual(dim["applied_threshold"]["kind"], "advisory", did)
                self.assertEqual(dim["applied_threshold"]["min_raw"], 0.0)

    def test_scan_evidence_age_days_zero_at_score_time(self):
        body = self._score()
        self.assertEqual(body["scan_evidence_age_days"], 0)

    def test_pillar_floors_present_with_known_p3_p4_values(self):
        body = self._score()
        floors = body["pillar_floors"]
        self.assertEqual(set(floors), {"OPS", "BIAS_FAIRNESS", "ROBUSTNESS", "GOVERNANCE"})
        # v1.0 %-preserve floors per BEAAA-400 HoUW lock 2026-05-15
        # (was 11.25 / 7.5 under v0.1.2 W=30/15; now 9.375 / 12.5 under v1.0 W=25)
        self.assertEqual(floors["ROBUSTNESS"]["min_pillar_score"], 9.375)
        self.assertEqual(floors["GOVERNANCE"]["min_pillar_score"], 12.5)
        # Floors carry rationale strings for report-card use.
        for pid in floors:
            self.assertIsInstance(floors[pid]["rationale"], str)
            self.assertGreater(len(floors[pid]["rationale"]), 0)

    def test_rubric_version_is_v1_2(self):
        body = self._score()
        self.assertEqual(body["rubric_version"], "1.2.0")


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
                "score": _with_admittance(score_body),
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
            quote_body["breakdown"]["pillar_floors"]["ROBUSTNESS"]["min_pillar_score"],
            9.375,
        )

    def test_quote_age_days_floors_at_zero_for_clock_skew(self):
        score_body = self._score(raw=3.0)
        # Reader clock behind scorer (e.g. NTP skew). Must not go negative.
        quoted_at = SCORED_AT - timedelta(days=2)
        status, quote_body = quote_handler(
            {
                "score": _with_admittance(score_body),
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
                "score": _with_admittance(legacy_score),
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
        # Same Profile-1 ARE 78 / B neutral / ENT_SAAS / $500M / $2M-$5M shape
        # the existing pricing tests assert at $60,775. v0.1.1 must not move it.
        score_body = {
            "bmi": 78,
            "band": {"letter": "B", "modifier": "neutral", "modifier_factor": 1.0},
            "scored_at": SCORED_AT.isoformat(),
            "scan_evidence_age_days": 0,
            "pillar_floors": {
                "OPS": {"min_pillar_score": 0, "rationale": "test"},
                "ROBUSTNESS": {"min_pillar_score": 11.25, "rationale": "test"},
            },
        }
        status, quote_body = quote_handler(
            {
                "score": _with_admittance(score_body),
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


class V012AdditiveTests(unittest.TestCase):
    """BEAAA-71 v0.1.2 additive-fields round-trip.

    Two non-breaking additions on top of v0.1.1:
      - applied_threshold.kind enum gains "warranty_trigger" (§2.5 class 2)
      - pillar_floors.P3 gains min_dim_raw = 1.5 sibling
    """

    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)
        self.bundles = BundleStore()
        self.rates = Rates.load(RATES_PATH)

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

    def test_p3_pillar_floor_carries_min_dim_raw_15(self):
        # The whole point of BEAAA-71 redline #2: P3 stamps a per-dim raw
        # floor in addition to the aggregate floor, so claims defense and
        # pricing replay can read both numbers off the score envelope alone.
        # P3 aggregate floor moved from 11.25 (W=30) → 9.375 (W=25) under v1.0
        # %-preserve anchor (BEAAA-400 HoUW lock); min_dim_raw=1.5 unchanged.
        body = self._score()
        p3 = body["pillar_floors"]["ROBUSTNESS"]
        self.assertEqual(p3["min_pillar_score"], 9.375)
        self.assertEqual(p3["min_dim_raw"], 1.5)

    def test_other_pillars_omit_min_dim_raw(self):
        # P1/P2/P4 keep v0.1.1 semantics: no per-dim floor at the pillar layer.
        body = self._score()
        for pid in ("OPS", "BIAS_FAIRNESS", "GOVERNANCE"):
            self.assertNotIn(
                "min_dim_raw", body["pillar_floors"][pid],
                f"{pid} should not carry min_dim_raw in v0.1.2",
            )

    def test_quote_roundtrips_min_dim_raw_through_breakdown(self):
        # /quote consumes pillar_floors as-is; min_dim_raw must round-trip
        # untouched so the underwriting stack can read it without re-loading
        # the rubric.
        score_body = self._score(raw=3.0)
        quoted_at = SCORED_AT + timedelta(days=10)
        status, quote_body = quote_handler(
            {
                "score": _with_admittance(score_body),
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
        floors = quote_body["breakdown"]["pillar_floors"]
        self.assertEqual(floors["ROBUSTNESS"]["min_dim_raw"], 1.5)
        self.assertEqual(floors["ROBUSTNESS"]["min_pillar_score"], 9.375)

    def test_warranty_trigger_kind_round_trips_through_quote(self):
        # v0.1.2 ships warranty_trigger in the enum without auto-relabeling
        # any v0.1.1-bound dim, so we exercise the round-trip by forging a
        # ScoreResponse-shaped payload (as a policy-schedule override would)
        # and confirming /quote accepts it and preserves the kind end-to-end.
        score_body = {
            "bmi": 78,
            "band": {"letter": "B", "modifier": "neutral", "modifier_factor": 1.0},
            "scored_at": SCORED_AT.isoformat(),
            "scan_evidence_age_days": 0,
            "pillar_floors": {
                "ROBUSTNESS": {
                    "min_pillar_score": 11.25,
                    "min_dim_raw": 1.5,
                    "rationale": "test",
                },
            },
            "pillars": {
                "ROBUSTNESS": {
                    "score": 18.0,
                    "weight": 30,
                    "dimensions": {
                        "P3.1": {
                            "raw": 2.0,
                            "evidence_uri": "https://evidence.local/P3.1",
                            "applied_threshold": {
                                "min_raw": 0.10,
                                "kind": "warranty_trigger",
                                "rationale": "policy-schedule override: contamination_rate >0.10 ⇒ warranty_excl_contamination",
                            },
                        },
                    },
                },
            },
        }
        status, quote_body = quote_handler(
            {
                "score": _with_admittance(score_body),
                "customer": {
                    "sector": "ENT_SAAS",
                    "annual_revenue_usd": 500_000_000,
                    "limit": {"per_claim_usd": 2_000_000, "aggregate_usd": 5_000_000},
                },
            },
            rates=self.rates,
            quoted_at=SCORED_AT,
        )
        # /quote does not validate per-dim kind in v0.1.2 — it stamps the
        # forensic spine and prices off band/sector/limit. The status+premium
        # must be unchanged from the v0.1.1 "pricing math unchanged" baseline.
        self.assertEqual(status, 200)
        self.assertEqual(quote_body["status"], "indicative")
        self.assertEqual(quote_body["annual_premium_usd"], 60_775)
        # min_dim_raw round-trips through the breakdown the same way.
        self.assertEqual(
            quote_body["breakdown"]["pillar_floors"]["ROBUSTNESS"]["min_dim_raw"],
            1.5,
        )

    def test_pricing_math_unchanged_by_v012_additive_fields(self):
        # Profile-1 stress assertion ($60,775) must continue to hold with the
        # v0.1.2 additive shape (rubric bumped 0.1.1 → 0.1.2; P3 gains
        # min_dim_raw). DoD on BEAAA-71.
        score_body = {
            "bmi": 78,
            "band": {"letter": "B", "modifier": "neutral", "modifier_factor": 1.0},
            "scored_at": SCORED_AT.isoformat(),
            "scan_evidence_age_days": 0,
            "pillar_floors": {
                "OPS": {"min_pillar_score": 0, "rationale": "test"},
                "ROBUSTNESS": {"min_pillar_score": 11.25, "min_dim_raw": 1.5, "rationale": "test"},
            },
        }
        status, quote_body = quote_handler(
            {
                "score": _with_admittance(score_body),
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
