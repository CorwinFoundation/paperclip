"""Layer-2 reproducibility gate tests (BEAAA-163 Option C/B+C combined).

The L2 gate runs against N replicate ScoreResponses produced by re-scanning
a golden agent under seed-pinned probes. These tests cover the gate logic
itself, not live runs.
"""
from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from harness.golden_set.repro_gate import evaluate_replicates
from rubric.scoring import Rubric, score

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
FIXTURES = REPO / "tests" / "fixtures" / "golden_agents"

SCORED_AT = datetime(2026, 5, 9, 0, 0, 0, tzinfo=timezone.utc)
SNAPSHOT_HASH = "sha256:" + "a" * 64


def _baseline_replicate() -> dict:
    """Build a deterministic baseline ScoreResponse from the M1 fixture."""
    rubric = Rubric.load(RUBRIC_PATH)
    eo = json.loads((FIXTURES / "M1" / "eval_outputs.json").read_text())
    # BEAAA-7235: M1 (like every golden agent) carries an applicability.json
    # sidecar marking the rubric-1.1.0 TOOL_MISUSE + PII dims N/A on this
    # non-tool-calling, L1-replay surface. Thread it through score() exactly
    # as tests/test_golden_agents.py::_rerun_score does, else score() raises
    # "missing dim scores" for the architecturally-N/A active dims.
    app_path = FIXTURES / "M1" / "applicability.json"
    applicability_decisions = None
    applicability_policy_version = None
    if app_path.exists():
        _app = json.loads(app_path.read_text())
        applicability_decisions = _app.get("applicability_decisions")
        applicability_policy_version = _app.get("policy_version")
    res = score(
        rubric, eo,
        snapshot_hash=SNAPSHOT_HASH,
        forensic_bundle_uri="https://golden.local/M1",
        scored_at=SCORED_AT,
        applicability_decisions=applicability_decisions,
        applicability_policy_version=applicability_policy_version,
    )
    res["corpus_version"] = "halueval-qa@1.0+jbb@1.0+advbench@1.0+tqa@1.0"
    res["seed"] = 42
    res["scanner_version"] = "0.2.3"
    return res


class L2GateBasicTests(unittest.TestCase):
    """Identical replicates must pass. Anything else must explain itself."""

    def test_identical_replicates_pass(self):
        base = _baseline_replicate()
        verdict = evaluate_replicates([copy.deepcopy(base) for _ in range(5)])
        self.assertTrue(verdict.passed, msg=verdict.summary)
        self.assertEqual(verdict.n_replicates, 5)
        self.assertEqual(verdict.bmi_min, verdict.bmi_max)
        self.assertFalse(verdict.bmi_breach)
        self.assertEqual(verdict.dim_breaches, {})
        self.assertEqual(verdict.pillar_breaches, {})
        self.assertEqual(verdict.contract_drift, [])

    def test_two_replicates_minimum(self):
        base = _baseline_replicate()
        with self.assertRaises(ValueError):
            evaluate_replicates([base])

    def test_summary_includes_pass_marker_when_passing(self):
        base = _baseline_replicate()
        verdict = evaluate_replicates([copy.deepcopy(base), copy.deepcopy(base)])
        self.assertIn("PASS", verdict.summary)


class L2GateBmiBreachTests(unittest.TestCase):
    def test_bmi_drift_within_tolerance_passes(self):
        base = _baseline_replicate()
        bumped = copy.deepcopy(base)
        bumped["bmi"] = base["bmi"] + 1
        verdict = evaluate_replicates([base, bumped])
        self.assertFalse(verdict.bmi_breach)
        # passed only if dims also stable; here only bmi was bumped without
        # corresponding dim changes, so the dims still match -> pass.
        self.assertTrue(verdict.passed, msg=verdict.summary)

    def test_bmi_drift_outside_tolerance_breaches(self):
        base = _baseline_replicate()
        bumped = copy.deepcopy(base)
        bumped["bmi"] = base["bmi"] + 5
        verdict = evaluate_replicates([base, bumped])
        self.assertTrue(verdict.bmi_breach)
        self.assertFalse(verdict.passed)
        self.assertIn("BMI delta 5", verdict.summary)


class L2GateDimBreachTests(unittest.TestCase):
    def test_dim_drift_within_005_passes(self):
        base = _baseline_replicate()
        nudged = copy.deepcopy(base)
        for pid, pdata in nudged["pillars"].items():
            for did, dim in pdata["dimensions"].items():
                dim["raw"] = round(dim["raw"] + 0.04, 4)
                break
            break
        verdict = evaluate_replicates([base, nudged])
        self.assertEqual(verdict.dim_breaches, {})
        self.assertTrue(verdict.passed, msg=verdict.summary)

    def test_dim_drift_outside_005_breaches(self):
        base = _baseline_replicate()
        nudged = copy.deepcopy(base)
        target_did = None
        for pid, pdata in nudged["pillars"].items():
            for did, dim in pdata["dimensions"].items():
                dim["raw"] = round(dim["raw"] + 0.10, 4)
                target_did = did
                break
            break
        verdict = evaluate_replicates([base, nudged])
        self.assertIn(target_did, verdict.dim_breaches)
        self.assertFalse(verdict.passed)


class L2GateContractDriftTests(unittest.TestCase):
    """corpus_version / seed / scanner_version / rubric_version drift is
    a hard fail no matter how small the numeric variance — these fields
    must be byte-identical by construction."""

    def _two_replicates(self) -> tuple[dict, dict]:
        base = _baseline_replicate()
        return base, copy.deepcopy(base)

    def test_corpus_version_drift_is_hard_fail(self):
        a, b = self._two_replicates()
        b["corpus_version"] = a["corpus_version"] + "+drift@2.0"
        verdict = evaluate_replicates([a, b])
        self.assertIn("corpus_version", verdict.contract_drift)
        self.assertFalse(verdict.passed)

    def test_seed_drift_is_hard_fail(self):
        a, b = self._two_replicates()
        b["seed"] = 7
        verdict = evaluate_replicates([a, b])
        self.assertIn("seed", verdict.contract_drift)
        self.assertFalse(verdict.passed)

    def test_scanner_version_drift_is_hard_fail(self):
        a, b = self._two_replicates()
        b["scanner_version"] = "0.2.4"
        verdict = evaluate_replicates([a, b])
        self.assertIn("scanner_version", verdict.contract_drift)
        self.assertFalse(verdict.passed)

    def test_rubric_version_drift_is_hard_fail(self):
        a, b = self._two_replicates()
        b["rubric_version"] = "0.1.4"
        verdict = evaluate_replicates([a, b])
        self.assertIn("rubric_version", verdict.contract_drift)
        self.assertFalse(verdict.passed)


class L2GateSerializationTests(unittest.TestCase):
    def test_as_dict_round_trips_through_json(self):
        base = _baseline_replicate()
        verdict = evaluate_replicates([copy.deepcopy(base) for _ in range(3)])
        # as_dict must produce a JSON-serializable shape so the gate can
        # ship its verdict as an attachment to UW.
        encoded = json.dumps(verdict.as_dict())
        decoded = json.loads(encoded)
        self.assertEqual(decoded["passed"], True)
        self.assertEqual(decoded["n_replicates"], 3)


if __name__ == "__main__":
    unittest.main()
