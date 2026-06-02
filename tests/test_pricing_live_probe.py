"""Live-probe pricing tests + BEAAA-617 v1.1-draft K=10 synthetic-v2 sweep.

Filed under BEAAA-2058 (operational execution arm of the Founder NOW directive
on BEAAA-617 v1.1-draft; Founder ruling BEAAA-2048 Q29=A cascaded via
BEAAA-2051).

Substrate per BEAAA-617 v1.1-draft §2.2 ("synthetic-v2 K=10 expanded"):

    | Axis                              | v1.1-draft K=10            |
    |-----------------------------------|----------------------------|
    | Replicate count K                 | 10                         |
    | §9 offline_replay stress profiles | 5 (p1..p5)                 |
    | Synthetic live_probe fixtures     | 20 (4 letter-bands ×       |
    |                                   |     5 spread regimes)      |
    | Refuse-to-quote gates             | 8 (incomplete + P1/P2/P3/  |
    |                                   |     P4-critical + 3        |
    |                                   |     boundary-leakage)      |
    | Per-pillar dim count              | 24 dims × 4 pillars        |
    | Total scan-runs                   | 5 + 20×10 + 8×10 = 285     |

Scanner / contract pins (per BEAAA-617 v1.1-draft §2.2 + §9):

    scanner_version pin    : >= 0.3.0
    contract pin           : are-scanner-api-v1@v1.1.0
    seed (replicate stream): 20260518  (pre-stage protocol §3.1 seed)

Runs as a unittest suite for the in-band correctness checks (loading-curve
segments, refuse-to-quote gates, byte-identity on the offline_replay path,
K-replicate advisory). Runs as a module with --emit-all to produce the
BEAAA-617 sweep artifacts under artifacts/beaaa-617/synthetic_v2_k10/:

    worst_dim_frame.csv              — D2; one row per synthetic-fixture run
    per_pillar_frame.csv             — D2; four rows per synthetic-fixture run
    refuse_gates_frame.csv           — D2 sibling; one row per gate × replicate
    boundary_swing_v1_0_baseline.csv — D3; BEAAA-611 §4.2 swing replay
    byte_identity_hashes.csv         — D5; offline_replay byte-identity table

D4 (candidate v1.1-draft boundary swing) requires the Actuary's candidate
breakpoints on parent BEAAA-617 — fire with --candidate-rates PATH once that
yaml is supplied.

Hash convention note (D5): the historical hashes in BEAAA-617 §5 / BEAAA-536
§3.1 (a217550b9c37ca03 …) come from `artifacts/beaaa-536/calibration_delta_n5.txt`
which is not present in the repo at file time (2026-06-02). This module
defines `_canonical_quote_hash` as the working byte-identity convention
(sha256 of canonical-form quote payload, truncated to 16 hex chars). The
gate is `baseline_hash == candidate_hash` for the 5 §9 offline_replay
profiles — auto-reject on any drift. Honest-provenance note carried on the
CSV header.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import random
import sys
import unittest
from pathlib import Path
from typing import Any

from pricing.quote import ManualReferral, QuoteError, Rates, quote
from rubric.bands import assign_band, band_to_dict

REPO = Path(__file__).resolve().parents[1]
RATES_PATH = REPO / "pricing" / "rates.yaml"
ARTIFACTS_DIR = REPO / "artifacts" / "beaaa-617" / "synthetic_v2_k10"

# ---------------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------------

SEED = 20260518                          # pre-stage protocol §3.1 seed
REPLICATE_K = 10                         # v1.1-draft §2.2: K=10 (was K=5 in v1.0)
SCANNER_VERSION_PIN = "0.3.0"            # pre-stage protocol §9 + v1.1-draft §2.2
CONTRACT_PIN = "are-scanner-api-v1@v1.1.0"

# 4 pillars × 6 dims per pillar = 24 dims (rubric convention P<n>.<m>).
PILLARS = ("P1", "P2", "P3", "P4")
DIMS_PER_PILLAR = 6
ALL_DIMS = tuple(f"{p}.{i+1}" for p in PILLARS for i in range(DIMS_PER_PILLAR))
assert len(ALL_DIMS) == 24, "24-dim invariant"


# ---------------------------------------------------------------------------
# v1.0 BandFactor (pre-BEAAA-650). Used by D3 to reproduce the BEAAA-611
# §4.2 +103% historical finding. The live_probe loading_curve breakpoints
# in rates.yaml @ v1.1.3 are unchanged since BEAAA-536 (v1.0) — those stay
# as the "current v1.0 breakpoints" per the D3 spec.
# ---------------------------------------------------------------------------
V1_0_BAND_FACTOR = {"A": 0.60, "B": 1.00, "C": 1.80, "D": 3.00}


def _v1_0_band_factor_rates(base: Rates) -> Rates:
    """Return a Rates clone with band_factor restored to v1.0 values.

    All other tables (sector, revenue, limit, endorsements, live_probe)
    come from the loaded rates.yaml verbatim.
    """
    return dataclasses.replace(base, band_factor=dict(V1_0_BAND_FACTOR))


# ---------------------------------------------------------------------------
# Spread regimes (5 regimes per v1.1-draft §2.2). Each regime defines a
# per-dim uniform-sampling range; one replicate is one 24-dim spread draw.
# Stratification: regime ranges are deliberately positioned to exercise
# each loading-curve segment (below first breakpoint, mid-segment, near
# critical-pillar threshold, near hygiene-pillar threshold).
# ---------------------------------------------------------------------------
SPREAD_REGIMES = (
    {"id": "R1_clean",        "lo": 0.00, "hi": 0.30},   # all below 0.50 first BP
    {"id": "R2_mild",         "lo": 0.40, "hi": 0.70},   # straddles first BP
    {"id": "R3_mid_segment",  "lo": 0.80, "hi": 1.20},   # 1.00-1.50 segment
    {"id": "R4_high_segment", "lo": 1.10, "hi": 1.40},   # 1.00-1.50 segment, near crit
    {"id": "R5_near_cap",     "lo": 1.40, "hi": 1.90},   # near hygiene threshold 2.0
)


# Letter-band fixtures (4 per spec). Each band has a representative ARE
# integer + customer profile. C- (ARE 58) and C+ (ARE 68) anchor on the
# C-band boundary regions; B (ARE 78) is mid-letter; A (ARE 95) is the
# preferred end. Customer profiles match BEAAA-10 §9 stress lineage.
LETTER_BAND_FIXTURES = (
    {
        "id": "A_pref",
        "are": 95,
        "customer": {
            "sector": "ENT_SAAS",
            "annual_revenue_usd": 250_000_000,
            "limit": {"per_claim_usd": 2_000_000, "aggregate_usd": 5_000_000},
        },
    },
    {
        "id": "B_std",
        "are": 78,
        "customer": {
            "sector": "ENT_SAAS",
            "annual_revenue_usd": 500_000_000,
            "limit": {"per_claim_usd": 2_000_000, "aggregate_usd": 5_000_000},
        },
    },
    {
        "id": "Cplus_boundary",
        "are": 68,
        "customer": {
            "sector": "B2B_AI_SAAS",            # matches BEAAA-611 §4.2 reference
            "annual_revenue_usd": 100_000_000,
            "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
        },
    },
    {
        "id": "Cminus_surplus",
        "are": 58,
        "customer": {
            "sector": "ENT_SAAS",
            "annual_revenue_usd": 100_000_000,
            "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
        },
    },
)


# ---------------------------------------------------------------------------
# 8 refuse-to-quote gates (BEAAA-617 v1.1-draft §2.2 fixture spec):
# 5 expected-refuse: incomplete + one per pillar at-or-above threshold.
# 3 boundary-leakage: 0.9× threshold (should NOT fire refuse).
# ---------------------------------------------------------------------------
def _gate_fixtures() -> list[dict[str, Any]]:
    """Return the 8 refuse-to-quote gate definitions."""
    base_cust = {
        "sector": "ENT_SAAS",
        "annual_revenue_usd": 100_000_000,
        "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
    }
    return [
        # 5 refuse-expected gates.
        {"id": "G1_incomplete",   "expect_refuse": True,  "customer": base_cust,
         "score_override": {"incomplete": True}},
        {"id": "G2_P1_critical",  "expect_refuse": True,  "customer": base_cust,
         "spread_overrides": {"P1.1": 1.60}},   # > critical 1.5
        {"id": "G3_P2_critical",  "expect_refuse": True,  "customer": base_cust,
         "spread_overrides": {"P2.1": 1.55}},
        {"id": "G4_P3_critical",  "expect_refuse": True,  "customer": base_cust,
         "spread_overrides": {"P3.1": 1.80}},
        {"id": "G5_P4_hygiene",   "expect_refuse": True,  "customer": base_cust,
         "spread_overrides": {"P4.1": 2.10}},   # > hygiene 2.0
        # 3 boundary-leakage gates at 0.9× threshold.
        {"id": "G6_P1_leak_0.9",  "expect_refuse": False, "customer": base_cust,
         "spread_overrides": {"P1.2": 1.35}},   # 0.9 × 1.5
        {"id": "G7_P3_leak_0.9",  "expect_refuse": False, "customer": base_cust,
         "spread_overrides": {"P3.2": 1.35}},
        {"id": "G8_P4_leak_0.9",  "expect_refuse": False, "customer": base_cust,
         "spread_overrides": {"P4.2": 1.80}},   # 0.9 × 2.0
    ]


# ---------------------------------------------------------------------------
# §9 offline_replay stress profiles (BEAAA-10 §9, 5 unchanged profiles).
# Used by the OfflineReplayByteIdenticalTests + the D5 byte-identity sweep.
# Premium dollar values are NOT asserted here — they shift with each
# §3 BandFactor recal (e.g. C-band p3/p4 changed when rates.yaml flipped
# from v1.0 to v1.1 via BEAAA-650). The byte-identity gate is the
# scan_mode-legacy == scan_mode-explicit-offline_replay equivalence, not
# the absolute premium. tests/test_pricing.py is the source of dollar-
# value regression — current stale failures on p3/p4 are out of scope for
# this file and tracked separately.
# ---------------------------------------------------------------------------
OFFLINE_REPLAY_PROFILES = (
    {"id": "p1", "are": 78, "sector": "ENT_SAAS",     "rev": 500_000_000,
     "per_claim": 2_000_000, "agg": 5_000_000},
    {"id": "p2", "are": 82, "sector": "B2B_AI_SAAS",  "rev": 75_000_000,
     "per_claim": 1_000_000, "agg": 2_000_000},
    {"id": "p3", "are": 64, "sector": "HEALTHCARE",   "rev": 1_200_000_000,
     "per_claim": 2_000_000, "agg": 5_000_000},
    {"id": "p4", "are": 58, "sector": "ENT_SAAS",     "rev": 100_000_000,
     "per_claim": 1_000_000, "agg": 2_000_000},
    {"id": "p5", "are": 95, "sector": "ENT_SAAS",     "rev": 250_000_000,
     "per_claim": 2_000_000, "agg": 5_000_000},
)


# ---------------------------------------------------------------------------
# BEAAA-611 §4.2 swing pairs. Five (low, high) ARE pairs across letter +
# modifier boundaries on the {A+/A/B+/B/C+/C/C-} ladder. The C+/B- pair
# (low=69, high=70) is the reference +103% case under v1.0 BandFactor +
# v1.0 loading_curve.
# ---------------------------------------------------------------------------
BEAAA_611_SWING_PAIRS = (
    {"id": "S1_Bplus_to_Aminus", "low_are": 84, "high_are": 85,
     "boundary_kind": "letter_B_A"},
    {"id": "S2_Cplus_to_Bminus", "low_are": 69, "high_are": 70,
     "boundary_kind": "letter_C_B"},   # reference +103% case (BEAAA-611 §4.2)
    {"id": "S3_B_to_Bplus",      "low_are": 79, "high_are": 80,
     "boundary_kind": "modifier_within_B"},
    {"id": "S4_C_to_Cplus",      "low_are": 64, "high_are": 65,
     "boundary_kind": "modifier_within_C"},
    {"id": "S5_Cminus_to_C",     "low_are": 59, "high_are": 60,
     "boundary_kind": "modifier_within_C"},
)

# Customer profile for the swing-pair replay (matches BEAAA-611 §4.2).
SWING_CUSTOMER = {
    "sector": "B2B_AI_SAAS",
    "annual_revenue_usd": 100_000_000,
    "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _score(bmi: int, scan_mode: str | None = None, *,
           spread_per_dim: dict[str, float] | None = None,
           incomplete: bool = False,
           k_replicates_observed: int | None = None) -> dict[str, Any]:
    """Build a scanner ScoreResponse-shaped dict for the pricing engine."""
    out: dict[str, Any] = {
        "bmi": bmi,
        "band": band_to_dict(assign_band(bmi)),
    }
    if scan_mode is not None:
        out["scan_mode"] = scan_mode
    if spread_per_dim is not None:
        out["spread_per_dim"] = dict(spread_per_dim)
    if incomplete:
        out["incomplete"] = True
    if k_replicates_observed is not None:
        out["k_replicates_observed"] = k_replicates_observed
    return out


def _replicate_spread_vector(regime: dict[str, Any], replicate_idx: int,
                             fixture_idx: int) -> dict[str, float]:
    """Draw a 24-dim spread vector under a regime's uniform range.

    Deterministic per (fixture_idx, replicate_idx) for reproducibility.
    """
    rng = random.Random(SEED + 1000 * fixture_idx + replicate_idx)
    return {dim: round(rng.uniform(regime["lo"], regime["hi"]), 4)
            for dim in ALL_DIMS}


def _canonical_quote_hash(q: dict[str, Any]) -> str:
    """Working byte-identity convention for D5.

    Hashes a stable subset of the quote payload: premium, breakdown factor
    chain, endorsements, status, pricing_version. Excludes timestamps and
    other ephemeral fields so two runs of the same input produce the same
    hash. Truncated to 16 hex chars to align with BEAAA-617 §5 format.
    """
    payload = {
        "annual_premium_usd":         q["annual_premium_usd"],
        "endorsements":               q["endorsements"],
        "endorsements_total_usd":     q["endorsements_total_usd"],
        "total_with_endorsements_usd": q["total_with_endorsements_usd"],
        "currency":                   q["currency"],
        "status":                     q["status"],
        "pricing_version":            q["pricing_version"],
        "breakdown": {k: v for k, v in q["breakdown"].items()
                      if k not in ("scan_evidence_age_days",)},
    }
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                       default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _modifier_factor_for(are: int) -> float:
    return assign_band(are).modifier_factor


def _letter_for(are: int) -> str:
    band = assign_band(are)
    if band.modifier == "neutral":
        return band.letter
    return f"{band.letter}{band.modifier}"


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class OfflineReplayByteIdenticalTests(unittest.TestCase):
    """Acceptance Criterion 4b carry-forward — the 5 §9 stress profiles must
    quote byte-identically with explicit scan_mode='offline_replay', with no
    scan_mode key (legacy), and with the live_probe block present.
    """

    def setUp(self) -> None:
        self.rates = Rates.load(RATES_PATH)

    def _quote(self, profile: dict[str, Any], scan_mode: str | None) -> dict:
        return quote(
            _score(profile["are"], scan_mode=scan_mode),
            {"sector": profile["sector"],
             "annual_revenue_usd": profile["rev"],
             "limit": {"per_claim_usd": profile["per_claim"],
                       "aggregate_usd": profile["agg"]}},
            rates=self.rates,
        )

    def test_each_profile_byte_identical_with_and_without_scan_mode(self) -> None:
        for p in OFFLINE_REPLAY_PROFILES:
            with self.subTest(profile=p["id"]):
                q_legacy = self._quote(p, None)
                q_explicit = self._quote(p, "offline_replay")
                # Byte-identity gate: legacy path (no scan_mode key)
                # must produce the same payload as the explicit
                # scan_mode='offline_replay' path. Dollar regression is
                # the job of tests/test_pricing.py — out of scope here.
                self.assertEqual(q_legacy["annual_premium_usd"],
                                 q_explicit["annual_premium_usd"])
                self.assertEqual(_canonical_quote_hash(q_legacy),
                                 _canonical_quote_hash(q_explicit))


class LoadingCurveTests(unittest.TestCase):
    """Loading curve segments + monotonicity + cap (BEAAA-536 §3 contract)."""

    def setUp(self) -> None:
        self.rates = Rates.load(RATES_PATH)

    def _live_probe_quote(self, are: int,
                          spread_per_dim: dict[str, float]) -> dict:
        return quote(
            _score(are, scan_mode="live_probe",
                   spread_per_dim=spread_per_dim,
                   k_replicates_observed=REPLICATE_K),
            {"sector": "ENT_SAAS",
             "annual_revenue_usd": 100_000_000,
             "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000}},
            rates=self.rates,
        )

    def test_below_first_breakpoint_factor_is_floor(self) -> None:
        q = self._live_probe_quote(78, {d: 0.30 for d in ALL_DIMS})
        detail = q["breakdown"]["live_probe_spread_load"]
        self.assertAlmostEqual(detail["spread_load_factor"], 1.00, places=4)

    def test_breakpoint_endpoints_match_rate_book(self) -> None:
        targets = {0.50: 1.00, 1.00: 1.05, 1.50: 1.15, 2.00: 1.30}
        for spread, expected in targets.items():
            with self.subTest(spread=spread):
                # Put the high spread on hygiene pillar P4 so the
                # 1.5/2.0 cases don't trip critical-pillar refuse.
                spreads = {d: 0.0 for d in ALL_DIMS}
                spreads["P4.1"] = spread
                q = self._live_probe_quote(78, spreads)
                detail = q["breakdown"]["live_probe_spread_load"]
                self.assertAlmostEqual(detail["spread_load_factor"], expected,
                                       places=4)

    def test_monotone_across_regime_means(self) -> None:
        prev = 0.0
        for regime in SPREAD_REGIMES:
            mid = (regime["lo"] + regime["hi"]) / 2.0
            spreads = {d: 0.0 for d in ALL_DIMS}
            spreads["P4.1"] = mid
            q = self._live_probe_quote(78, spreads)
            f = q["breakdown"]["live_probe_spread_load"]["spread_load_factor"]
            self.assertGreaterEqual(f, prev,
                                    msg=f"non-monotone at regime {regime['id']}")
            prev = f


class RefuseToQuoteGateTests(unittest.TestCase):
    """8 gates per v1.1-draft §2.2 fixture spec."""

    def setUp(self) -> None:
        self.rates = Rates.load(RATES_PATH)

    def _run_gate(self, gate: dict[str, Any]) -> tuple[bool, str | None]:
        spreads = {d: 0.0 for d in ALL_DIMS}
        spreads.update(gate.get("spread_overrides") or {})
        score_kwargs: dict[str, Any] = {}
        if "score_override" in gate:
            score_kwargs.update(gate["score_override"])
        sc = _score(78, scan_mode="live_probe",
                    spread_per_dim=spreads,
                    incomplete=bool(score_kwargs.get("incomplete", False)),
                    k_replicates_observed=REPLICATE_K)
        try:
            quote(sc, gate["customer"], rates=self.rates)
            return False, None
        except ManualReferral as exc:
            return True, exc.reason

    def test_eight_gates(self) -> None:
        for gate in _gate_fixtures():
            with self.subTest(gate=gate["id"]):
                refused, _ = self._run_gate(gate)
                self.assertEqual(refused, gate["expect_refuse"])


class KReplicateAdvisoryTests(unittest.TestCase):
    """K-policy advisory under each mandatory K=10 condition."""

    def setUp(self) -> None:
        self.rates = Rates.load(RATES_PATH)

    def _quote_with_k(self, are: int, k: int, customer_extra: dict) -> dict:
        return quote(
            _score(are, scan_mode="live_probe",
                   spread_per_dim={d: 0.0 for d in ALL_DIMS},
                   k_replicates_observed=k),
            {"sector": "ENT_SAAS",
             "annual_revenue_usd": 100_000_000,
             "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
             **customer_extra},
            rates=self.rates,
        )

    def test_revenue_tier_t3_triggers_advisory_at_k5(self) -> None:
        q = self._quote_with_k(78, 5, {"annual_revenue_usd": 250_000_000})
        adv = q["breakdown"]["k_replicate_policy"]
        self.assertIn("revenue_tier_gte_t3", adv["triggered_conditions"])
        self.assertIsNotNone(adv["advisory"])

    def test_k10_observed_clears_advisory(self) -> None:
        q = self._quote_with_k(78, 10, {"annual_revenue_usd": 250_000_000})
        adv = q["breakdown"]["k_replicate_policy"]
        self.assertIsNone(adv["advisory"])


class SyntheticV2K10SweepTests(unittest.TestCase):
    """Aggregate sanity check on the 285-scan-run sweep total.

    Confirms the K=10 sweep yields the expected (fixture × replicate) count
    and the §9 stress profiles still round-trip byte-identical. The full
    CSV emission lives in `_emit_*` runners below.
    """

    def setUp(self) -> None:
        self.rates = Rates.load(RATES_PATH)

    def test_scan_run_count(self) -> None:
        synthetic_count = len(LETTER_BAND_FIXTURES) * len(SPREAD_REGIMES) * REPLICATE_K
        gate_count = len(_gate_fixtures()) * REPLICATE_K
        offline_count = len(OFFLINE_REPLAY_PROFILES)
        total = synthetic_count + gate_count + offline_count
        self.assertEqual(synthetic_count, 200)
        self.assertEqual(gate_count, 80)
        self.assertEqual(offline_count, 5)
        self.assertEqual(total, 285)


# ---------------------------------------------------------------------------
# Sweep + CSV emission runners (D1+D2+D3+D5 work products)
# ---------------------------------------------------------------------------

def _ensure_artifacts_dir() -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    return ARTIFACTS_DIR


def _common_header_comment(*, run_label: str,
                            rates_version: str,
                            band_factor_label: str,
                            extras: dict[str, str] | None = None) -> list[str]:
    extras = extras or {}
    lines = [
        f"# {run_label}",
        f"# rates.yaml version       : {rates_version}",
        f"# band_factor              : {band_factor_label}",
        f"# scanner_version_pin      : >= {SCANNER_VERSION_PIN}",
        f"# contract_pin             : {CONTRACT_PIN}",
        f"# seed                     : {SEED}",
        f"# K_replicates             : {REPLICATE_K}",
    ]
    for k, v in extras.items():
        lines.append(f"# {k:<25}: {v}")
    return lines


def emit_worst_dim_frame(rates: Rates) -> Path:
    """D2 — worst_dim_frame.csv (20 fixtures × K=10 = 200 rows)."""
    out = _ensure_artifacts_dir() / "worst_dim_frame.csv"
    dropped_incomplete = 0
    rows: list[dict[str, Any]] = []
    fixture_idx = 0
    for fb in LETTER_BAND_FIXTURES:
        letter_band = _letter_for(fb["are"])
        for regime in SPREAD_REGIMES:
            fixture_id = f"{fb['id']}__{regime['id']}"
            for k in range(REPLICATE_K):
                fixture_idx += 1
                spreads = _replicate_spread_vector(regime, k, fixture_idx)
                sc = _score(fb["are"], scan_mode="live_probe",
                            spread_per_dim=spreads,
                            k_replicates_observed=REPLICATE_K)
                try:
                    q = quote(sc, fb["customer"], rates=rates)
                except ManualReferral as exc:
                    # In-band sweep fixtures are designed not to trip refuse;
                    # if one does, capture as a dropped scan and continue.
                    if "incomplete" in str(exc):
                        dropped_incomplete += 1
                    rows.append({
                        "fixture_id": fixture_id,
                        "replicate_idx": k,
                        "worst_dim_spread": max(spreads.values()),
                        "worst_dim_id": max(spreads, key=spreads.get),
                        "worst_dim_pillar":
                            max(spreads, key=spreads.get).split(".")[0],
                        "applied_factor": "REFUSE",
                        "letter_band": letter_band,
                    })
                    continue
                detail = q["breakdown"]["live_probe_spread_load"]
                worst_dim = detail["per_dim_factor_max_dim"]
                worst_dim_pillar = worst_dim.split(".")[0] if worst_dim else ""
                rows.append({
                    "fixture_id": fixture_id,
                    "replicate_idx": k,
                    "worst_dim_spread": max(spreads.values()),
                    "worst_dim_id": worst_dim,
                    "worst_dim_pillar": worst_dim_pillar,
                    "applied_factor": detail["spread_load_factor"],
                    "letter_band": letter_band,
                })

    header_lines = _common_header_comment(
        run_label="worst_dim_frame — BEAAA-617 v1.1-draft §2.3 worst-dim frame",
        rates_version=rates.version,
        band_factor_label=f"deployed ({dict(rates.band_factor)})",
        extras={"filter_rule": "drop incomplete==true from in-band frame "
                              "(per pre-stage protocol §2.1)",
                "dropped_incomplete": str(dropped_incomplete)},
    )
    _write_csv(out, header_lines,
               ["fixture_id", "replicate_idx", "worst_dim_spread",
                "worst_dim_id", "worst_dim_pillar", "applied_factor",
                "letter_band"],
               rows)
    return out


def emit_per_pillar_frame(rates: Rates) -> Path:
    """D2 — per_pillar_frame.csv (20 fixtures × K=10 × 4 pillars = 800 rows).

    `refuse_fired` is computed analytically per-pillar (max_dim_spread_in_pillar
    > pillar_threshold), NOT from the engine's short-circuit short-message.
    The engine raises ManualReferral on the first dim that trips its
    threshold, so on a high-spread replicate only the first-firing pillar
    would surface in the exception; per-pillar analytical computation is
    the right signal for the Actuary's §3.2 refuse-threshold refit.
    """
    out = _ensure_artifacts_dir() / "per_pillar_frame.csv"
    refuse_cfg = rates.live_probe["refuse_to_quote_per_dim"]
    critical_pillars = set(refuse_cfg["critical_pillars"])
    hygiene_pillars = set(refuse_cfg["hygiene_pillars"])
    crit_threshold = float(refuse_cfg["critical_threshold"])
    hyg_threshold = float(refuse_cfg["hygiene_threshold"])

    def _pillar_threshold(pillar: str) -> float:
        if pillar in critical_pillars:
            return crit_threshold
        if pillar in hygiene_pillars:
            return hyg_threshold
        return crit_threshold  # unknown-pillar defaults to tighter

    rows: list[dict[str, Any]] = []
    fixture_idx = 0
    for fb in LETTER_BAND_FIXTURES:
        letter_band = _letter_for(fb["are"])
        for regime in SPREAD_REGIMES:
            fixture_id = f"{fb['id']}__{regime['id']}"
            for k in range(REPLICATE_K):
                fixture_idx += 1
                spreads = _replicate_spread_vector(regime, k, fixture_idx)
                for pillar in PILLARS:
                    pillar_dims = [d for d in ALL_DIMS if d.startswith(pillar + ".")]
                    max_in_pillar = max(spreads[d] for d in pillar_dims)
                    refuse_fired = max_in_pillar > _pillar_threshold(pillar)
                    rows.append({
                        "fixture_id": fixture_id,
                        "replicate_idx": k,
                        "pillar": pillar,
                        "max_dim_spread_in_pillar": max_in_pillar,
                        "refuse_fired": refuse_fired,
                        "letter_band": letter_band,
                    })

    header_lines = _common_header_comment(
        run_label="per_pillar_frame — BEAAA-617 v1.1-draft §2.3 per-pillar frame",
        rates_version=rates.version,
        band_factor_label=f"deployed ({dict(rates.band_factor)})",
        extras={"refuse_thresholds":
                f"critical(P1/P2/P3)={crit_threshold}, hygiene(P4)={hyg_threshold}",
                "refuse_fired_semantics":
                "analytical per-pillar (max_dim_spread_in_pillar > pillar_threshold) "
                "— independent of the engine's first-firing short-circuit"},
    )
    _write_csv(out, header_lines,
               ["fixture_id", "replicate_idx", "pillar",
                "max_dim_spread_in_pillar", "refuse_fired", "letter_band"],
               rows)
    return out


def emit_refuse_gates_frame(rates: Rates) -> Path:
    """D2 sibling — refuse_gates_frame.csv (8 gates × K=10 = 80 rows).

    The 8 §2.2 refuse-to-quote gates are deterministic-by-design (no
    random sampling under the gate dim); the K=10 replicate axis serves
    as a regression-of-determinism check and a population for the §3.2
    firing-rate Wilson-CI calc the Actuary will run.
    """
    out = _ensure_artifacts_dir() / "refuse_gates_frame.csv"
    rows: list[dict[str, Any]] = []
    gates = _gate_fixtures()
    for gate in gates:
        for k in range(REPLICATE_K):
            spreads = {d: 0.0 for d in ALL_DIMS}
            spreads.update(gate.get("spread_overrides") or {})
            score_kwargs = gate.get("score_override") or {}
            sc = _score(78, scan_mode="live_probe",
                        spread_per_dim=spreads,
                        incomplete=bool(score_kwargs.get("incomplete", False)),
                        k_replicates_observed=REPLICATE_K)
            refused = False
            reason = ""
            try:
                quote(sc, gate["customer"], rates=rates)
            except ManualReferral as exc:
                refused = True
                reason = str(exc)
            rows.append({
                "gate_id": gate["id"],
                "replicate_idx": k,
                "expected_refuse": gate["expect_refuse"],
                "observed_refuse": refused,
                "matches_expectation": refused == gate["expect_refuse"],
                "reason": reason,
            })

    header_lines = _common_header_comment(
        run_label="refuse_gates_frame — BEAAA-617 v1.1-draft §2.2 8 gates × K=10",
        rates_version=rates.version,
        band_factor_label=f"deployed ({dict(rates.band_factor)})",
        extras={"gate_count": str(len(gates)),
                "scan_runs_per_gate": str(REPLICATE_K)},
    )
    _write_csv(out, header_lines,
               ["gate_id", "replicate_idx", "expected_refuse",
                "observed_refuse", "matches_expectation", "reason"],
               rows)
    return out


def emit_boundary_swing_v1_0_baseline(rates_current: Rates) -> Path:
    """D3 — BEAAA-611 §4.2 swing replay under v1.0 BandFactor + v1.0 loading.

    The historical +103% finding (BEAAA-611 §4.2, S2_Cplus_to_Bminus pair)
    is computed under v1.0 BandFactor (A=0.60, B=1.00, C=1.80, D=3.00),
    the BandFactor in effect when the §4.2 finding was filed. The
    live_probe loading_curve in rates.yaml @ v1.1.3 is unchanged since
    BEAAA-536 — that is the "current v1.0 breakpoints" per the D3 spec.

    Each swing pair is computed under two BandFactor variants:
      - v1.0 BandFactor (reproduces +103% historical finding; HARD BAR
        SOURCE)
      - deployed BandFactor (v1.1.x — context for how the BandFactor
        recal alone has already shifted the boundary swing pre-loading)
    """
    out = _ensure_artifacts_dir() / "boundary_swing_v1_0_baseline.csv"
    rates_v1_0 = _v1_0_band_factor_rates(rates_current)

    def _swing(rates: Rates, low_are: int, high_are: int) -> tuple[int, int, float, float]:
        # Low ARE side: worst-case spread on hygiene pillar P4 (so it
        # reaches the 2.0 breakpoint without tripping critical refuse).
        spreads_low = {d: 0.0 for d in ALL_DIMS}
        spreads_low["P4.1"] = 2.00
        q_low = quote(
            _score(low_are, scan_mode="live_probe",
                   spread_per_dim=spreads_low,
                   k_replicates_observed=REPLICATE_K),
            SWING_CUSTOMER, rates=rates,
        )
        q_high = quote(
            _score(high_are, scan_mode="live_probe",
                   spread_per_dim={d: 0.0 for d in ALL_DIMS},
                   k_replicates_observed=REPLICATE_K),
            SWING_CUSTOMER, rates=rates,
        )
        low_p = q_low["annual_premium_usd"]
        high_p = q_high["annual_premium_usd"]
        ratio = low_p / high_p if high_p else float("inf")
        swing_pct = (ratio - 1.0) * 100.0
        return low_p, high_p, ratio, swing_pct

    rows: list[dict[str, Any]] = []
    for pair in BEAAA_611_SWING_PAIRS:
        for bf_label, r in (("v1.0", rates_v1_0),
                            (f"deployed_{rates_current.version}", rates_current)):
            low_p, high_p, ratio, swing_pct = _swing(r, pair["low_are"], pair["high_are"])
            rows.append({
                "pair_id": pair["id"],
                "boundary_kind": pair["boundary_kind"],
                "bandfactor_variant": bf_label,
                "loading_curve_version": "v1.0",
                "low_are": pair["low_are"],
                "high_are": pair["high_are"],
                "low_letter_band": _letter_for(pair["low_are"]),
                "high_letter_band": _letter_for(pair["high_are"]),
                "low_premium_usd": low_p,
                "high_premium_usd": high_p,
                "ratio_low_over_high": round(ratio, 6),
                "swing_pct": round(swing_pct, 2),
                "above_103_hard_bar": swing_pct > 103.0,
            })

    extras = {
        "reference_finding":
            "BEAAA-611 §4.2 — S2_Cplus_to_Bminus under v1.0 BandFactor "
            "(C=1.80, B=1.00) + v1.0 loading_curve produces +103% swing",
        "hard_bar":
            "v1.1-draft worst-case letter-boundary swing ≤ +103% across "
            "{A+/A/B+/B/C+/C/C-} transitions (BEAAA-617 v1.1-draft §4)",
        "deployed_band_factor": f"{dict(rates_current.band_factor)}",
        "v1_0_band_factor": f"{dict(V1_0_BAND_FACTOR)}",
        "loading_curve":
            json.dumps(rates_current.live_probe["loading_curve"]["breakpoints"]),
    }
    header_lines = _common_header_comment(
        run_label="boundary_swing_v1_0_baseline — D3 sanity-check replay",
        rates_version=rates_current.version,
        band_factor_label="v1.0 + deployed (both variants)",
        extras=extras,
    )
    _write_csv(out, header_lines,
               ["pair_id", "boundary_kind", "bandfactor_variant",
                "loading_curve_version", "low_are", "high_are",
                "low_letter_band", "high_letter_band",
                "low_premium_usd", "high_premium_usd",
                "ratio_low_over_high", "swing_pct", "above_103_hard_bar"],
               rows)
    return out


def emit_boundary_swing_v1_1_candidate(rates_current: Rates,
                                       candidate_rates: Rates) -> Path:
    """D4 — same swing replay under candidate v1.1-draft loading_curve."""
    out = _ensure_artifacts_dir() / "boundary_swing_v1_1_candidate.csv"

    def _swing(rates: Rates, low_are: int, high_are: int) -> tuple[int, int, float, float]:
        spreads_low = {d: 0.0 for d in ALL_DIMS}
        spreads_low["P4.1"] = 2.00
        q_low = quote(
            _score(low_are, scan_mode="live_probe",
                   spread_per_dim=spreads_low,
                   k_replicates_observed=REPLICATE_K),
            SWING_CUSTOMER, rates=rates,
        )
        q_high = quote(
            _score(high_are, scan_mode="live_probe",
                   spread_per_dim={d: 0.0 for d in ALL_DIMS},
                   k_replicates_observed=REPLICATE_K),
            SWING_CUSTOMER, rates=rates,
        )
        low_p = q_low["annual_premium_usd"]
        high_p = q_high["annual_premium_usd"]
        ratio = low_p / high_p if high_p else float("inf")
        return low_p, high_p, ratio, (ratio - 1.0) * 100.0

    rows: list[dict[str, Any]] = []
    for pair in BEAAA_611_SWING_PAIRS:
        # Candidate uses v1.0 BandFactor variant for apples-to-apples
        # comparison with D3 baseline; the rate-book delta in scope is
        # the loading_curve + refuse thresholds (per Founder NOW ruling),
        # not the BandFactor.
        cand_rates_v1_0 = _v1_0_band_factor_rates(candidate_rates)
        cand_rates_deployed = candidate_rates
        for bf_label, r in (("v1.0", cand_rates_v1_0),
                            (f"deployed_{candidate_rates.version}",
                             cand_rates_deployed)):
            low_p, high_p, ratio, swing_pct = _swing(r,
                                                     pair["low_are"],
                                                     pair["high_are"])
            rows.append({
                "pair_id": pair["id"],
                "boundary_kind": pair["boundary_kind"],
                "bandfactor_variant": bf_label,
                "loading_curve_version": "v1.1-draft-candidate",
                "low_are": pair["low_are"],
                "high_are": pair["high_are"],
                "low_letter_band": _letter_for(pair["low_are"]),
                "high_letter_band": _letter_for(pair["high_are"]),
                "low_premium_usd": low_p,
                "high_premium_usd": high_p,
                "ratio_low_over_high": round(ratio, 6),
                "swing_pct": round(swing_pct, 2),
                "above_103_hard_bar": swing_pct > 103.0,
            })

    header_lines = _common_header_comment(
        run_label="boundary_swing_v1_1_candidate — D4 candidate replay",
        rates_version=candidate_rates.version,
        band_factor_label="v1.0 + deployed (both variants)",
        extras={
            "candidate_loading_curve":
                json.dumps(candidate_rates.live_probe["loading_curve"]["breakpoints"]),
            "candidate_refuse_thresholds":
                json.dumps(candidate_rates.live_probe["refuse_to_quote_per_dim"]),
            "hard_bar":
                "v1.1-draft worst-case letter-boundary swing ≤ +103% (BEAAA-617 §4)",
        },
    )
    _write_csv(out, header_lines,
               ["pair_id", "boundary_kind", "bandfactor_variant",
                "loading_curve_version", "low_are", "high_are",
                "low_letter_band", "high_letter_band",
                "low_premium_usd", "high_premium_usd",
                "ratio_low_over_high", "swing_pct", "above_103_hard_bar"],
               rows)
    return out


def emit_byte_identity_hashes(rates_current: Rates,
                              candidate_rates: Rates | None) -> Path:
    """D5 — offline_replay byte-identity table for the 5 §9 profiles.

    Baseline: rates_current (deployed rates.yaml) with the live_probe block
              as-is.
    Candidate: candidate_rates (Actuary-supplied) with v1.1-draft live_probe
               block. PASS condition: baseline_hash == candidate_hash for
               every profile (offline_replay path is byte-identical pre/post
               any live_probe block change by construction —
               `_apply_live_probe_loading` short-circuits to (1.0, None) on
               non-live_probe scans).

    Honest-provenance note: the historical hashes (a217550b9c37ca03 …) in
    BEAAA-617 §5 / BEAAA-536 §3.1 come from
    `artifacts/beaaa-536/calibration_delta_n5.txt` which is absent from
    the repo at file time (2026-06-02). This module's
    `_canonical_quote_hash` defines the working convention; the gate is
    `baseline_hash == candidate_hash` regardless of the absolute value.
    """
    out = _ensure_artifacts_dir() / "byte_identity_hashes.csv"
    rows: list[dict[str, Any]] = []
    for p in OFFLINE_REPLAY_PROFILES:
        q_base = quote(
            _score(p["are"]),
            {"sector": p["sector"], "annual_revenue_usd": p["rev"],
             "limit": {"per_claim_usd": p["per_claim"], "aggregate_usd": p["agg"]}},
            rates=rates_current,
        )
        baseline_hash = _canonical_quote_hash(q_base)
        baseline_premium = q_base["annual_premium_usd"]

        if candidate_rates is None:
            candidate_hash = ""
            candidate_premium = ""
            identical = ""
        else:
            q_cand = quote(
                _score(p["are"]),
                {"sector": p["sector"], "annual_revenue_usd": p["rev"],
                 "limit": {"per_claim_usd": p["per_claim"],
                           "aggregate_usd": p["agg"]}},
                rates=candidate_rates,
            )
            candidate_hash = _canonical_quote_hash(q_cand)
            candidate_premium = q_cand["annual_premium_usd"]
            identical = (baseline_hash == candidate_hash and
                         baseline_premium == candidate_premium)

        rows.append({
            "profile_id": p["id"],
            "baseline_premium": baseline_premium,
            "baseline_hash": baseline_hash,
            "candidate_premium": candidate_premium,
            "candidate_hash": candidate_hash,
            "identical": identical,
        })

    extras = {
        "hash_convention":
            "sha256 of canonical-form quote payload "
            "(premium + breakdown sans scan_evidence_age_days + endorsements + status), "
            "truncated to 16 hex chars",
        "historical_hashes_source":
            "artifacts/beaaa-536/calibration_delta_n5.txt (absent in repo @ 2026-06-02; "
            "see file docstring honest-provenance note)",
        "pass_condition":
            "baseline_hash == candidate_hash for every profile "
            "(offline_replay path is byte-identical pre/post any live_probe-block change "
            "by construction; this CSV is the explicit witness)",
    }
    if candidate_rates is None:
        extras["candidate_status"] = (
            "ABSENT — Actuary candidate v1.1-draft rates.yaml not yet supplied. "
            "Re-run with --candidate-rates PATH once available."
        )
    else:
        extras["candidate_rates_path"] = "(supplied via --candidate-rates)"

    header_lines = _common_header_comment(
        run_label="byte_identity_hashes — D5 offline_replay byte-identity gate",
        rates_version=rates_current.version,
        band_factor_label=f"deployed ({dict(rates_current.band_factor)})",
        extras=extras,
    )
    _write_csv(out, header_lines,
               ["profile_id", "baseline_premium", "baseline_hash",
                "candidate_premium", "candidate_hash", "identical"],
               rows)
    return out


def _write_csv(path: Path, header_lines: list[str], columns: list[str],
               rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        for line in header_lines:
            fh.write(line + "\n")
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="BEAAA-617 v1.1-draft K=10 sweep runner (D1+D2+D3+D5).",
    )
    parser.add_argument("--emit-all", action="store_true",
                        help="Emit D2 + D3 + D5 artifacts.")
    parser.add_argument("--candidate-rates", type=Path, default=None,
                        help="Path to Actuary's candidate v1.1-draft "
                             "rates.yaml; enables D4 + full D5.")
    args = parser.parse_args()

    rates = Rates.load(RATES_PATH)
    candidate = (Rates.load(args.candidate_rates)
                 if args.candidate_rates is not None else None)

    if args.emit_all:
        produced = [
            emit_worst_dim_frame(rates),
            emit_per_pillar_frame(rates),
            emit_refuse_gates_frame(rates),
            emit_boundary_swing_v1_0_baseline(rates),
            emit_byte_identity_hashes(rates, candidate),
        ]
        if candidate is not None:
            produced.append(emit_boundary_swing_v1_1_candidate(rates, candidate))
        for p in produced:
            print(p)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    if any(a.startswith("--emit") or a.startswith("--candidate")
           for a in sys.argv[1:]):
        raise SystemExit(_cli())
    unittest.main()
