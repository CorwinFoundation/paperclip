"""BEAAA-2867 — calibration-harness PII override behavior-preservation.

Behavior-preservation contract (ticket acceptance bullet 3):

    pre-PII-rubric synthetic + post-PII-rubric synthetic both score B2
    fixture to same total (proves no Δ on existing dims).

Path-A calibration harness (scripts/path_a_baselines.py) does not wire
the BEAAA-781 LLM Guard Anonymize / Deanonymize probe slate. Under
rubric 1.1.0 the two PII rubric_point_ids fire_when: always, so the
canonical Path-A harness ScoringErrors with `missing dim scores`. The
harness override marks both PII dims N/A via the existing Option C
re-normalization apparatus (BEAAA-198 Track 2 Phase 3, UW + CEO
co-signed). This file proves that override does not move the ARE total
relative to the pre-PII rubric on any of the existing dims.

Lane: rule-execution per
`contracts/scanner-implementation-supervision-v1.md@v1.0`. The override
does not touch rubric.yaml, applicability_policy.yaml,
rubric/scoring.py, weights, or judge pins.
"""
from __future__ import annotations

import unittest
import json
from datetime import datetime, timezone
from pathlib import Path

from api.bundle import _signing_key
from harness.applicability import applicability_record, load_policy, validate_for_scoring
import rubric.scoring as scoring_module
from rubric.scoring import Rubric, ScoringError, score
from scripts import path_a_baselines

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
SCORED_AT = datetime(2026, 6, 5, 0, 0, 0, tzinfo=timezone.utc)
SNAPSHOT_HASH = "sha256:" + "b" * 64
FORENSIC_URI = "https://goldens.local/path-a-pii-uncovered"

PII_SUBSTRATE = "ROBUSTNESS.PII.SCAN_TIME_DETECTION"
PII_ATTESTATION = "GOVERNANCE.PII.HANDLING_ATTESTATION"
G1_APPLICABILITY = REPO / "tests" / "fixtures" / "golden_agents" / "G1" / "applicability.json"


def _full_suite_eval_outputs(active_dim_ids, raw_value: float = 3.0) -> dict:
    return {
        did: {"raw": raw_value, "evidence_uri": f"https://goldens.local/{did}"}
        for did in active_dim_ids
    }


class PathAPiiHarnessUncoveredTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rubric = Rubric.load(RUBRIC_PATH)
        cls.policy = load_policy()
        cls.architecture_flags = json.loads(G1_APPLICABILITY.read_text())["architecture_flags"]
        cls.scanner_record = applicability_record(cls.policy, cls.architecture_flags)
        cls.validated = validate_for_scoring(
            cls.policy,
            cls.architecture_flags,
            cls.scanner_record["applicability_decisions"],
            cls.rubric.active_dim_ids,
            _signing_key(),
        )

    def test_module_constants_pinned(self):
        """Both PII dim_ids are pinned in the harness constant exactly as
        the rubric activation declared them; drift breaks the override."""
        self.assertIn(PII_SUBSTRATE, path_a_baselines._HARNESS_UNCOVERED_DIMS)
        self.assertIn(PII_ATTESTATION, path_a_baselines._HARNESS_UNCOVERED_DIMS)
        self.assertEqual(len(path_a_baselines._HARNESS_UNCOVERED_DIMS), 2)
        self.assertTrue(path_a_baselines._HARNESS_UNCOVERED_NA_JUSTIFICATION_KEY)
        self.assertIn(
            "calibration",
            path_a_baselines._HARNESS_UNCOVERED_NA_JUSTIFICATION.lower(),
        )

    def test_trusted_na_is_limited_to_genuine_non_applicability(self):
        """Scanner validation preserves Option C only for policy-approved N/A."""
        na_dims = set(self.scanner_record["na_dims"])
        self.assertTrue(na_dims)
        self.assertFalse(na_dims.intersection(path_a_baselines._HARNESS_UNCOVERED_DIMS))
        eval_outputs = _full_suite_eval_outputs(
            [did for did in self.rubric.active_dim_ids if did not in na_dims]
        )
        result = score(
            self.rubric, eval_outputs, snapshot_hash=SNAPSHOT_HASH,
            forensic_bundle_uri=FORENSIC_URI, scored_at=SCORED_AT,
            corpus_version="synthetic@trusted", seed=42,
            applicability_decisions=self.validated,
            applicability_policy_version=self.policy.version,
        )
        caveat = next(d for d in result["diagnostics"] if d["code"] == "coverage_caveat")
        self.assertEqual(set(caveat["detail"]["na_dims"]), na_dims)

    def test_pii_override_drops_missing_dim_raise(self):
        """Without the override, rubric 1.1.0 + a harness eval_outputs
        with no PII rows raises ScoringError. With the override, score()
        returns cleanly. This is the BEAAA-2867 unblock invariant.
        """
        active = self.rubric.active_dim_ids
        non_pii_dims = [d for d in active if d not in path_a_baselines._HARNESS_UNCOVERED_DIMS]
        eval_outputs = _full_suite_eval_outputs(non_pii_dims, raw_value=3.0)

        # Without override: ScoringError, same shape as the BEAAA-2316
        # rep-1 failure that drove this ticket.
        from rubric.scoring import ScoringError
        with self.assertRaises(ScoringError) as cm:
            score(
                self.rubric,
                eval_outputs,
                snapshot_hash=SNAPSHOT_HASH,
                forensic_bundle_uri=FORENSIC_URI,
                scored_at=SCORED_AT,
                corpus_version="synthetic",
                seed=42,
            )
        err = str(cm.exception)
        self.assertIn("missing dim scores", err)
        self.assertIn(PII_SUBSTRATE, err)
        self.assertIn(PII_ATTESTATION, err)

        # The legacy raw PII waiver is rejected at the hardened scorer boundary.
        decisions = {
            did: {
                "fires": False,
                "na_justification": path_a_baselines._HARNESS_UNCOVERED_NA_JUSTIFICATION,
                "na_justification_key": (
                    path_a_baselines._HARNESS_UNCOVERED_NA_JUSTIFICATION_KEY
                ),
            }
            for did in path_a_baselines._HARNESS_UNCOVERED_DIMS
        }
        with self.assertRaisesRegex(ScoringError, "raw applicability_decisions"):
            scoring_module._score_with_required_corpus_pin(
                self.rubric, eval_outputs, snapshot_hash=SNAPSHOT_HASH,
                forensic_bundle_uri=FORENSIC_URI, scored_at=SCORED_AT,
                corpus_version="synthetic", seed=42,
                applicability_decisions=decisions,
                applicability_policy_version="0.1.2",
            )

        # Scanner-produced validation keeps both always-applicable PII rows firing.
        self.assertTrue(self.validated.decisions[PII_SUBSTRATE]["fires"])
        self.assertTrue(self.validated.decisions[PII_ATTESTATION]["fires"])

    def test_rubric_active_dim_ids_still_include_pii(self):
        """Sanity: the override is harness-scope only — the rubric still
        lists the PII dims so production scans fire them. Drift here
        means someone retired the dims, which is a rule-change action."""
        self.assertIn(PII_SUBSTRATE, self.rubric.active_dim_ids)
        self.assertIn(PII_ATTESTATION, self.rubric.active_dim_ids)
        # BEAAA-13703 rollback: rubric returns to v1.3.0 with P3.5/P3.6
        # active and loop-stability isolated as non-scoring evidence.
        self.assertEqual(self.rubric.version, "1.3.0")


if __name__ == "__main__":
    unittest.main()
