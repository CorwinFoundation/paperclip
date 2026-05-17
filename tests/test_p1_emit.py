"""BEAAA-209 Phase 2 — P1 emit helper + scoring contract propagation tests."""
from __future__ import annotations

import unittest
from pathlib import Path

from harness import p1_emit
from rubric.scoring import Rubric, score

_REPO = Path(__file__).resolve().parent.parent
_RUBRIC_PATH = _REPO / "rubric" / "rubric.yaml"


def _load_rubric() -> Rubric:
    return Rubric.load(_RUBRIC_PATH)


def _all_measured_eval_outputs(rubric: Rubric, raw: float = 2.5) -> dict:
    """Build an eval_outputs map covering every active dim with `raw`."""
    return {
        d: {"raw": raw, "evidence_uri": f"https://path-b.test/{d}"}
        for d in rubric.active_dim_ids
    }


class PopulateP16CellTests(unittest.TestCase):

    def test_full_coverage_yields_raw_4_pct_100(self):
        rubric = _load_rubric()
        eo = _all_measured_eval_outputs(rubric)
        # Caller's eval_outputs may already carry a P1.6 prior — the
        # helper must drop it from the classifier input.
        eo_with_prior = dict(eo)
        eo_with_prior["P1.6"] = {"raw": 0.5, "evidence_uri": "prior://stale"}
        out = p1_emit.populate_p1_6_cell(
            eo_with_prior, rubric=rubric, evidence_uri="s3://aggregator/p1_6",
        )
        cell = out["P1.6"]
        self.assertEqual(cell["raw"], 4.0)
        self.assertAlmostEqual(cell["coverage_breadth_pct"], 100.0, places=6)
        self.assertEqual(cell["evidence_path"], "B")
        self.assertEqual(cell["evidence_uri"], "s3://aggregator/p1_6")
        # All non-P1.6 active dims should be in covered_dim_ids.
        expected = {d for d in rubric.active_dim_ids if d != "P1.6"}
        self.assertEqual(set(cell["covered_dim_ids"]), expected)

    def test_partial_coverage_with_explicit_overrides(self):
        rubric = _load_rubric()
        eo = _all_measured_eval_outputs(rubric)
        # Mark two dims explicitly as not-measured.
        overrides = {
            "P3.2": "decline-to-attest",
            "P3.3": "decline-to-attest",
        }
        out = p1_emit.populate_p1_6_cell(
            eo, rubric=rubric, explicit_dim_status=overrides,
        )
        cell = out["P1.6"]
        self.assertNotIn("P3.2", cell["covered_dim_ids"])
        self.assertNotIn("P3.3", cell["covered_dim_ids"])
        # In v1.0 (25/3 per P3 dim), two dropped → ~16.7pp out of ~95.8
        # total ≈ 17.4pp missing → ~82.6% coverage → raw 4.0 still
        # because we're above the 80% gate.
        self.assertGreaterEqual(cell["coverage_breadth_pct"], 80.0)
        self.assertEqual(cell["raw"], 4.0)

    def test_does_not_mutate_input(self):
        rubric = _load_rubric()
        eo = _all_measured_eval_outputs(rubric)
        before = {k: dict(v) for k, v in eo.items()}
        p1_emit.populate_p1_6_cell(eo, rubric=rubric)
        self.assertEqual(eo, before)


class ScoringContractPropagationTests(unittest.TestCase):
    """v0.2.4 additive: rubric.scoring.score must forward the three
    optional P1.6 fields from the input cell onto the dim block."""

    def test_v0_2_4_fields_propagate_through_score(self):
        rubric = _load_rubric()
        eo = _all_measured_eval_outputs(rubric)
        eo = p1_emit.populate_p1_6_cell(
            eo, rubric=rubric, evidence_uri="s3://test/p1_6",
        )
        response = score(
            rubric, eo,
            snapshot_hash="sha256:test",
            forensic_bundle_uri="s3://forensic/test",
        )
        p1_6_block = response["pillars"]["OPS"]["dimensions"]["P1.6"]
        self.assertIn("coverage_breadth_pct", p1_6_block)
        self.assertIn("covered_dim_ids", p1_6_block)
        self.assertIn("evidence_path", p1_6_block)
        self.assertEqual(p1_6_block["evidence_path"], "B")
        self.assertAlmostEqual(
            p1_6_block["coverage_breadth_pct"], 100.0, places=6,
        )
        self.assertEqual(p1_6_block["raw"], 4.0)

    def test_v0_2_3_callers_unaffected_no_p1_6_fields(self):
        """A caller that does NOT call populate_p1_6_cell still produces
        a valid v0.2.3-shape response — the three optional fields are
        absent (not None) for byte-stable v0.2.3 compatibility."""
        rubric = _load_rubric()
        eo = _all_measured_eval_outputs(rubric)
        response = score(
            rubric, eo,
            snapshot_hash="sha256:test",
            forensic_bundle_uri="s3://forensic/test",
        )
        for pid, pdata in response["pillars"].items():
            for did, dim in pdata["dimensions"].items():
                self.assertNotIn("coverage_breadth_pct", dim,
                                 msg=f"{did} unexpectedly carried v0.2.4 field")
                self.assertNotIn("covered_dim_ids", dim)
                self.assertNotIn("evidence_path", dim)

    def test_byte_stable_replay_with_aggregator(self):
        """Same inputs → identical P1.6 block across runs."""
        rubric = _load_rubric()
        eo1 = p1_emit.populate_p1_6_cell(
            _all_measured_eval_outputs(rubric), rubric=rubric,
            evidence_uri="s3://stable",
        )
        eo2 = p1_emit.populate_p1_6_cell(
            _all_measured_eval_outputs(rubric), rubric=rubric,
            evidence_uri="s3://stable",
        )
        r1 = score(rubric, eo1, snapshot_hash="s", forensic_bundle_uri="f")
        r2 = score(rubric, eo2, snapshot_hash="s", forensic_bundle_uri="f")
        # Compare the P1.6 block byte-for-byte (excluding scored_at on
        # the response — known to vary).
        import json
        b1 = json.dumps(r1["pillars"]["OPS"]["dimensions"]["P1.6"], sort_keys=True)
        b2 = json.dumps(r2["pillars"]["OPS"]["dimensions"]["P1.6"], sort_keys=True)
        self.assertEqual(b1, b2)


if __name__ == "__main__":
    unittest.main()
