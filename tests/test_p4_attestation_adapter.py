"""BEAAA-200 Track 4 — adapter tests bridging score_p4_attestation into the
harness pipeline's measurement format.

These are smaller than the per-dim scorer suite (those live in
test_p4_attestation.py); the surface here is the adapter contract + the
pilot fixture loadability.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness import p4_attestation_adapter as adapter  # noqa: E402
from harness.pipeline import merge_measurements_with_priors  # noqa: E402
from scripts import score_p4_attestation as p4  # noqa: E402

FIXTURE = REPO / "harness" / "fixtures" / "p4_attestation_pilot_v1.json"
NOW = dt.date(2026, 5, 22)


def _load_pilot() -> dict:
    return json.loads(FIXTURE.read_text())


def _full_tier4_ledger() -> dict:
    return {
        "customer_id": "cust-001",
        "attested_at": "2026-05-22T00:00:00Z",
        "families": [
            {"family_id": f"family_{i}", "probe_count": 50, "last_run_date": "2026-05-15", "pass_rate": 0.9}
            for i in range(10)
        ],
    }


class AdapterContractTests(unittest.TestCase):
    """Contract: happy-path adapter call returns 6 measurements with the
    P4.<n> dim_id format used by priors, with raw floats in [0,4], and
    the BENCHMARK_LABEL constant on every row."""

    def test_constants_match_spec(self):
        self.assertEqual(adapter.BENCHMARK_LABEL, "p4_attestation_form_v0_1_locked")
        self.assertEqual(adapter.PILLAR_DIM_IDS, ("P4.1", "P4.2", "P4.3", "P4.4", "P4.5", "P4.6"))

    def test_pilot_form_loads_and_validates(self):
        form = _load_pilot()
        self.assertEqual(p4.validate_form(form), [])

    def test_pilot_form_scores_six_measurements(self):
        form = _load_pilot()
        result = adapter.attestation_to_measurements(form, now=NOW)
        self.assertFalse(result.scored.get("rejected"), msg=result.scored.get("rejection_reason"))
        self.assertEqual(len(result.measurements), 6)
        ids = [m["dim_id"] for m in result.measurements]
        self.assertEqual(ids, list(adapter.PILLAR_DIM_IDS))
        for m in result.measurements:
            self.assertEqual(m["benchmark"], adapter.BENCHMARK_LABEL)
            self.assertIsInstance(m["raw"], float)
            self.assertGreaterEqual(m["raw"], 0.0)
            self.assertLessEqual(m["raw"], 4.0)

    def test_measurements_merge_into_priors_and_override_p4(self):
        """End-to-end pipeline-side proof: priors dict gets its P4 entries
        replaced by adapter-emitted raws; non-P4 dims are untouched.

        This is the 5/22 milestone shape — pipeline ingests + scores P4 from
        the attestation rather than the raw=~2 stub.
        """
        form = _load_pilot()
        result = adapter.attestation_to_measurements(form, now=NOW)
        priors = {f"P{p}.{d}": 2.0 for p in (1, 2, 3, 4) for d in (1, 2, 3, 4, 5, 6)}
        priors.update({"P3.1": 2.0, "P3.2": 2.0, "P3.3": 2.0})
        merged, provenance = merge_measurements_with_priors(result.measurements, priors)
        for dim_id in adapter.PILLAR_DIM_IDS:
            self.assertEqual(merged[dim_id], result.scored["dims"][dim_id]["raw"])
            self.assertTrue(provenance[dim_id].startswith("measured:"))
            self.assertEqual(provenance[dim_id], f"measured:{adapter.BENCHMARK_LABEL}")
        non_p4 = [k for k in priors if not k.startswith("P4.")]
        for k in non_p4:
            self.assertEqual(merged[k], 2.0)
            self.assertEqual(provenance[k], "prior")


class AdapterRejectionPathTests(unittest.TestCase):
    """A rejected form (Q2 signature insufficient) must NOT emit measurements,
    so a tampered low-sig attestation cannot silently inflate priors."""

    def test_notarized_email_at_tier3_returns_empty_measurements(self):
        # Pilot fixture is full_v0 + pki_escrow_plus_third_party → lands tier 3/4 cleanly.
        # Downgrade signature to notarized_email → claimed tiers now exceed
        # sig cap (cap = 2) → form is rejected, adapter emits 0 measurements.
        form = _load_pilot()
        form["signature_block"]["signature_mode"] = "notarized_email"
        result = adapter.attestation_to_measurements(form, now=NOW)
        self.assertTrue(result.scored.get("rejected"))
        self.assertEqual(result.measurements, [])
        self.assertEqual(
            result.scored["rejection_reason"]["error"],
            "signature_mode_insufficient_for_attained_tier",
        )

    def test_rejected_measurements_do_not_override_priors(self):
        """If the form is rejected, merging an empty measurements list leaves
        priors untouched — caller must NOT treat rejection as 'use priors';
        scored.rejected is the surface signal."""
        form = _load_pilot()
        form["signature_block"]["signature_mode"] = "notarized_email"
        result = adapter.attestation_to_measurements(form, now=NOW)
        priors = {f"P4.{d}": 2.0 for d in (1, 2, 3, 4, 5, 6)}
        merged, provenance = merge_measurements_with_priors(result.measurements, priors)
        for dim_id in adapter.PILLAR_DIM_IDS:
            self.assertEqual(merged[dim_id], 2.0)
            self.assertEqual(provenance[dim_id], "prior")


class AdapterDivergenceCapPropagationTests(unittest.TestCase):
    """The P4.5 divergence cap (raw≤2.0 on any >10pp claim/replay gap) must
    propagate through the adapter — i.e. if scorer caps P4.5 to 2.0, the
    adapter emits raw=2.0, not the un-capped value."""

    def test_p4_5_divergence_cap_propagates_through_adapter(self):
        form = _load_pilot()
        # Bump P4.5 to a tier-3+ claim so divergence cap is meaningful.
        # Pilot already lands at 3 on P4.5; inject a divergence > 10pp.
        ledger_replay_divergence = {f"family_{i}": 0.20 for i in range(10)}
        result = adapter.attestation_to_measurements(
            form,
            ledger=_full_tier4_ledger(),
            ledger_replay_divergence=ledger_replay_divergence,
            now=NOW,
        )
        self.assertFalse(result.scored.get("rejected"), msg=result.scored.get("rejection_reason"))
        p4_5 = next(m for m in result.measurements if m["dim_id"] == "P4.5")
        self.assertLessEqual(p4_5["raw"], 2.0,
                             msg="divergence cap should hold P4.5 raw at <=2.0")


if __name__ == "__main__":
    unittest.main()
