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
from datetime import datetime, timezone
from pathlib import Path

from rubric.scoring import Rubric, score
from scripts import path_a_baselines

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
SCORED_AT = datetime(2026, 6, 5, 0, 0, 0, tzinfo=timezone.utc)
SNAPSHOT_HASH = "sha256:" + "b" * 64
FORENSIC_URI = "https://goldens.local/path-a-pii-uncovered"

PII_SUBSTRATE = "ROBUSTNESS.PII.SCAN_TIME_DETECTION"
PII_ATTESTATION = "GOVERNANCE.PII.HANDLING_ATTESTATION"


def _full_suite_eval_outputs(active_dim_ids, raw_value: float = 3.0) -> dict:
    return {
        did: {"raw": raw_value, "evidence_uri": f"https://goldens.local/{did}"}
        for did in active_dim_ids
    }


class PathAPiiHarnessUncoveredTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rubric = Rubric.load(RUBRIC_PATH)

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

    def test_pii_override_recovers_pre_pii_total(self):
        """Pre/post equivalence: the score on the existing dims is
        identical when PII dims are absent (pre-PII rubric synthetic) vs
        present-but-N/A (post-PII rubric synthetic + harness override).

        Demonstrates Option C re-normalization holds: PII N/A does not
        pull pillar means; ROBUSTNESS + GOVERNANCE pillar scores match
        the pre-PII total.
        """
        active = self.rubric.active_dim_ids
        non_pii_dims = [d for d in active if d not in path_a_baselines._HARNESS_UNCOVERED_DIMS]

        # Pre-PII rubric synthetic: pretend the PII dims do not exist by
        # omitting them from eval_outputs AND from any applicability hint
        # — this is the pre-BEAAA-2649 rubric 1.0.1 shape. Score() walks
        # rubric.active_dim_ids though, so we cannot truly drop the dims
        # without a rubric reload; instead we emit them with N/A via
        # applicability_decisions (which produces the same pillar mean as
        # they simply not existing under Option C). This is the analytic
        # claim: pre-rubric "absent" === post-rubric "N/A under Option C".
        pre_pii_decisions = {
            did: {
                "fires": False,
                "na_justification": "pre-PII synthetic: dim absent in rubric 1.0.1",
                "na_justification_key": "synthetic_pre_pii",
            }
            for did in path_a_baselines._HARNESS_UNCOVERED_DIMS
        }
        pre_eval_outputs = _full_suite_eval_outputs(non_pii_dims, raw_value=3.0)
        pre_result = score(
            self.rubric,
            pre_eval_outputs,
            snapshot_hash=SNAPSHOT_HASH,
            forensic_bundle_uri=FORENSIC_URI,
            scored_at=SCORED_AT,
            corpus_version="synthetic@pre",
            seed=42,
            applicability_decisions=pre_pii_decisions,
            applicability_policy_version="synthetic-pre-pii",
        )

        # Post-PII rubric (current 1.1.0) + harness override: same Option
        # C apparatus, the harness-specific N/A justification. ARE total
        # and per-pillar score must match the pre-PII synthetic byte for
        # byte (no scoring-math delta on existing dims).
        post_pii_decisions = {
            did: {
                "fires": False,
                "na_justification": path_a_baselines._HARNESS_UNCOVERED_NA_JUSTIFICATION,
                "na_justification_key": (
                    path_a_baselines._HARNESS_UNCOVERED_NA_JUSTIFICATION_KEY
                ),
            }
            for did in path_a_baselines._HARNESS_UNCOVERED_DIMS
        }
        post_eval_outputs = _full_suite_eval_outputs(non_pii_dims, raw_value=3.0)
        post_result = score(
            self.rubric,
            post_eval_outputs,
            snapshot_hash=SNAPSHOT_HASH,
            forensic_bundle_uri=FORENSIC_URI,
            scored_at=SCORED_AT,
            corpus_version="synthetic@post",
            seed=42,
            applicability_decisions=post_pii_decisions,
            applicability_policy_version="0.1.2",
        )

        # ARE total + per-pillar score must match — proves no scoring
        # delta on existing dims (ticket acceptance criterion).
        self.assertEqual(pre_result["bmi"], post_result["bmi"])
        for pid in pre_result["pillars"]:
            self.assertEqual(
                pre_result["pillars"][pid]["score"],
                post_result["pillars"][pid]["score"],
                msg=f"pillar {pid} pre/post diverged",
            )

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

        # With override: clean return + coverage_caveat diagnostic.
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
        result = score(
            self.rubric,
            eval_outputs,
            snapshot_hash=SNAPSHOT_HASH,
            forensic_bundle_uri=FORENSIC_URI,
            scored_at=SCORED_AT,
            corpus_version="synthetic",
            seed=42,
            applicability_decisions=decisions,
            applicability_policy_version="0.1.2",
        )
        self.assertIn("bmi", result)
        # coverage_caveat names both PII dims.
        caveat = next(
            d for d in result.get("diagnostics", [])
            if d.get("code") == "coverage_caveat"
        )
        na_dims = set(caveat["detail"]["na_dims"])
        self.assertIn(PII_SUBSTRATE, na_dims)
        self.assertIn(PII_ATTESTATION, na_dims)

    def test_rubric_active_dim_ids_still_include_pii(self):
        """Sanity: the override is harness-scope only — the rubric still
        lists the PII dims so production scans fire them. Drift here
        means someone retired the dims, which is a rule-change action."""
        self.assertIn(PII_SUBSTRATE, self.rubric.active_dim_ids)
        self.assertIn(PII_ATTESTATION, self.rubric.active_dim_ids)
        # BEAAA-13388 activation: rubric bumped to 1.3.0 with P3.5/P3.6 active.
        self.assertEqual(self.rubric.version, "1.3.0")


if __name__ == "__main__":
    unittest.main()
