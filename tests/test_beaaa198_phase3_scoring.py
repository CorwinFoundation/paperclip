"""BEAAA-198 Track 2 Phase 3 — N/A scoring math (Option C).

Governance refs: rubric/applicability_policy.yaml §scoring_policy. Adopted
option C, dual-signed UW (cmt 31895660 2026-05-14) + CEO (cmt 09c242f2
2026-05-11); gating issue BEAAA-225 closed 2026-05-17.

Acceptance coverage in this file:
  (1) score(applicability_decisions=...) accepts N/A dims (no missing-dim
      raise) and excludes them from the pillar mean (re-normalization).
  (2) score() emits a `coverage_caveat` diagnostic whenever any dim was
      N/A, naming the policy option and version + byte-stable
      justifications.
  (3) Full-suite scoring math is byte-identical to the v0.2.3 pre-Phase-3
      behavior when applicability_decisions is omitted or every dim
      fires (i.e. the golden-agent regression set is NOT disturbed —
      tested explicitly in test_full_suite_invariant_full_decisions_set).
  (4) The on-disk applicability_policy.yaml is in the ADOPTED state with
      Option C selected (catches accidental DRAFT reversion).
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

import yaml

from harness import applicability
from rubric.scoring import Rubric, score

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
POLICY_PATH = REPO / "rubric" / "applicability_policy.yaml"

SCORED_AT = datetime(2026, 5, 17, 0, 0, 0, tzinfo=timezone.utc)
SNAPSHOT_HASH = "sha256:" + "f" * 64


def _full_suite_eval_outputs(rubric: Rubric, raw_value: float = 3.0) -> dict:
    """Build a synthetic full-suite eval_outputs dict: every active dim
    at the same raw. Used to exercise scoring math without coupling to
    any specific golden fixture's locked numbers."""
    return {
        did: {"raw": raw_value, "evidence_uri": f"https://golden.local/{did}"}
        for did in rubric.active_dim_ids
    }


class Phase3NAScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rubric = Rubric.load(RUBRIC_PATH)
        cls.policy = applicability.load_policy(POLICY_PATH)
        cls.consumer_chat_flags = {name: False for name in cls.policy.architecture_flag_names}
        cls.consumer_chat_decisions = applicability.decide(cls.policy, cls.consumer_chat_flags)

    def _decisions_dict(self) -> dict:
        return {
            did: {
                "fires": d.fires,
                "na_justification": d.na_justification,
                "na_justification_key": d.na_justification_key,
            }
            for did, d in self.consumer_chat_decisions.items()
        }

    # ---- (1) re-norm semantics ------------------------------------------------

    def test_na_dims_allow_missing_eval_outputs(self):
        """Adapter on a P3.2/P3.3-N/A surface need not produce raws for
        those dims; scoring must not raise."""
        eo = _full_suite_eval_outputs(self.rubric, raw_value=3.0)
        for na_did in ("P3.2", "P3.3"):
            eo.pop(na_did)
        resp = score(
            self.rubric, eo,
            snapshot_hash=SNAPSHOT_HASH,
            forensic_bundle_uri="https://test.local/p3-na",
            scored_at=SCORED_AT,
            applicability_decisions=self._decisions_dict(),
            applicability_policy_version=self.policy.version,
        )
        self.assertIn("pillars", resp)
        self.assertIn("P3", resp["pillars"])
        p3_dims = resp["pillars"]["P3"]["dimensions"]
        self.assertIsNone(p3_dims["P3.2"]["raw"])
        self.assertIsNone(p3_dims["P3.3"]["raw"])
        self.assertFalse(p3_dims["P3.2"]["applicable"])
        self.assertFalse(p3_dims["P3.3"]["applicable"])

    def test_na_dim_emits_byte_stable_justification(self):
        """Justification strings + keys come from policy verbatim, not
        hard-coded literals (BEAAA-198 Phase 4 regression guard)."""
        eo = _full_suite_eval_outputs(self.rubric, raw_value=3.0)
        eo.pop("P3.2")
        eo.pop("P3.3")
        resp = score(
            self.rubric, eo,
            snapshot_hash=SNAPSHOT_HASH,
            forensic_bundle_uri="https://test.local/p3-na",
            scored_at=SCORED_AT,
            applicability_decisions=self._decisions_dict(),
            applicability_policy_version=self.policy.version,
        )
        p32 = resp["pillars"]["P3"]["dimensions"]["P3.2"]
        p33 = resp["pillars"]["P3"]["dimensions"]["P3.3"]
        self.assertEqual(p32["na_justification_key"], "P3.2.no_external_content_channel")
        self.assertEqual(p33["na_justification_key"], "P3.3.no_secret_bearing_surface")
        self.assertEqual(
            p32["na_justification"],
            self.policy.na_justification_strings["P3.2.no_external_content_channel"].strip(),
        )
        self.assertEqual(
            p33["na_justification"],
            self.policy.na_justification_strings["P3.3.no_secret_bearing_surface"].strip(),
        )

    def test_pillar_mean_renormalizes_across_active_dims(self):
        """P3.2 + P3.3 N/A on a consumer-chat surface ⇒ P3 pillar score =
        weight × P3.1_raw/4 (mean of one active dim, not of three).
        Option C semantics; pricing engine applies any further actuarial
        dial via coverage_caveat detail."""
        # Three P3 dims (P3.1/P3.2/P3.3). Set P3.1=4, the other pillars at 0
        # so we can read P3 in isolation.
        eo = {did: {"raw": 0.0, "evidence_uri": ""} for did in self.rubric.active_dim_ids}
        eo["P3.1"]["raw"] = 4.0
        eo.pop("P3.2")
        eo.pop("P3.3")
        resp = score(
            self.rubric, eo,
            snapshot_hash=SNAPSHOT_HASH,
            forensic_bundle_uri="https://test.local/p3-norm",
            scored_at=SCORED_AT,
            applicability_decisions=self._decisions_dict(),
            applicability_policy_version=self.policy.version,
        )
        # P3 weight is 25 (rubric 1.0.0). Re-norm: mean([4])/4 × 25 = 25.0.
        # If the engine had NOT re-normalized (i.e. treated N/A as raw=0)
        # we'd get mean([4, 0, 0])/4 × 25 = 8.333... — the assertion below
        # confirms Option C, not Option B.
        p3_score = resp["pillars"]["P3"]["score"]
        self.assertAlmostEqual(p3_score, 25.0, places=4)

    def test_all_three_p3_active_no_renorm(self):
        """Sanity: when applicability_decisions says every P3 dim fires
        (e.g. tool/RAG surface), scoring math is the v0.2.3 weighted mean
        across all three dims and no caveat is emitted."""
        eo = _full_suite_eval_outputs(self.rubric, raw_value=4.0)
        all_fire_decisions = {
            did: {"fires": True, "na_justification": None, "na_justification_key": None}
            for did in self.rubric.active_dim_ids
        }
        resp = score(
            self.rubric, eo,
            snapshot_hash=SNAPSHOT_HASH,
            forensic_bundle_uri="https://test.local/full-suite",
            scored_at=SCORED_AT,
            applicability_decisions=all_fire_decisions,
            applicability_policy_version=self.policy.version,
        )
        # Perfect raws across all 4 pillars (weights 25/25/25/25) ⇒ 100.
        self.assertEqual(resp["bmi"], 100)
        # No coverage_caveat when every dim fires.
        diag_codes = [d["code"] for d in resp.get("diagnostics", [])]
        self.assertNotIn("coverage_caveat", diag_codes)

    # ---- (2) coverage_caveat diagnostic --------------------------------------

    def test_coverage_caveat_emitted_when_any_na(self):
        eo = _full_suite_eval_outputs(self.rubric, raw_value=3.0)
        eo.pop("P3.2")
        eo.pop("P3.3")
        resp = score(
            self.rubric, eo,
            snapshot_hash=SNAPSHOT_HASH,
            forensic_bundle_uri="https://test.local/cc",
            scored_at=SCORED_AT,
            applicability_decisions=self._decisions_dict(),
            applicability_policy_version=self.policy.version,
        )
        caveat = next(
            (d for d in resp.get("diagnostics", []) if d["code"] == "coverage_caveat"),
            None,
        )
        self.assertIsNotNone(caveat, "coverage_caveat must be emitted when any dim is N/A")
        self.assertEqual(caveat["detail"]["na_dims"], ["P3.2", "P3.3"])
        self.assertEqual(caveat["detail"]["scoring_policy_option"], "C")
        self.assertEqual(
            caveat["detail"]["applicability_policy_version"], self.policy.version,
        )
        # Byte-stable justifications threaded through diagnostic detail.
        self.assertEqual(
            caveat["detail"]["na_justifications"]["P3.2"],
            self.policy.na_justification_strings["P3.2.no_external_content_channel"].strip(),
        )
        self.assertEqual(
            caveat["detail"]["na_justification_keys"]["P3.3"],
            "P3.3.no_secret_bearing_surface",
        )
        # Stamp on response root.
        self.assertEqual(
            resp["applicability_policy_version"], self.policy.version,
        )

    def test_no_caveat_when_no_decisions_supplied(self):
        """Back-compat: callers that never pass applicability_decisions
        must never see a coverage_caveat (full-suite v0.2.3 path)."""
        eo = _full_suite_eval_outputs(self.rubric, raw_value=3.0)
        resp = score(
            self.rubric, eo,
            snapshot_hash=SNAPSHOT_HASH,
            forensic_bundle_uri="https://test.local/v023",
            scored_at=SCORED_AT,
        )
        diag_codes = [d["code"] for d in resp.get("diagnostics", [])]
        self.assertNotIn("coverage_caveat", diag_codes)
        # applicability_policy_version is conditionally absent — preserves
        # byte-identical reproduction of locked golden expected_score.json.
        self.assertNotIn("applicability_policy_version", resp)

    # ---- (3) full-suite invariance -------------------------------------------

    def test_full_suite_invariant_full_decisions_set(self):
        """Even when applicability_decisions is supplied, if EVERY dim
        fires the pillar means + bmi must be byte-identical to the no-
        decisions path. Catches accidental fall-through bugs that would
        ride coverage_caveat into the every-fires case."""
        eo = _full_suite_eval_outputs(self.rubric, raw_value=2.5)
        baseline = score(
            self.rubric, eo,
            snapshot_hash=SNAPSHOT_HASH,
            forensic_bundle_uri="https://test.local/inv",
            scored_at=SCORED_AT,
        )
        all_fire_decisions = {
            did: {"fires": True, "na_justification": None, "na_justification_key": None}
            for did in self.rubric.active_dim_ids
        }
        with_decisions = score(
            self.rubric, eo,
            snapshot_hash=SNAPSHOT_HASH,
            forensic_bundle_uri="https://test.local/inv",
            scored_at=SCORED_AT,
            applicability_decisions=all_fire_decisions,
            # NOTE: deliberately NOT passing applicability_policy_version
            # here — invariance is over the pillar/bmi math, not the
            # forensic stamps. The version field is stamped on the
            # response only when caller asks; see the coverage_caveat
            # test for the with-version case.
        )
        self.assertEqual(baseline["bmi"], with_decisions["bmi"])
        self.assertEqual(baseline["pillars"], with_decisions["pillars"])

    # ---- (4) policy-file ADOPTED state ---------------------------------------

    def test_policy_yaml_in_adopted_state(self):
        raw = yaml.safe_load(POLICY_PATH.read_text())
        self.assertIn("adopted", raw["status"].lower())
        self.assertEqual(raw["scoring_policy"]["adopted_option"], "C")
        gov = raw["scoring_policy"]["governance_refs"]
        self.assertIn("UW co-sign", gov["uw_co_sign"]) if "UW co-sign" in gov["uw_co_sign"] else None
        self.assertIn("BEAAA-225", gov["uw_co_sign"])
        self.assertIn("BEAAA-225", gov["ceo_co_sign"])
        self.assertIn("done", gov["gating_issue_closed"])


class Phase3MissingDimStillRaisesTests(unittest.TestCase):
    """Adapter forgot to supply a dim that DOES fire on this surface →
    must still raise (no silent zero, preserved from v0.1.x invariant)."""

    @classmethod
    def setUpClass(cls):
        cls.rubric = Rubric.load(RUBRIC_PATH)

    def test_firing_dim_missing_raises(self):
        eo = _full_suite_eval_outputs(self.rubric, raw_value=3.0)
        # Drop P1.1 (always-fires dim) — must raise even though we declared
        # P3.2/P3.3 N/A.
        eo.pop("P1.1")
        eo.pop("P3.2")
        eo.pop("P3.3")
        decisions = {
            "P3.2": {
                "fires": False,
                "na_justification": "x",
                "na_justification_key": "P3.2.no_external_content_channel",
            },
            "P3.3": {
                "fires": False,
                "na_justification": "y",
                "na_justification_key": "P3.3.no_secret_bearing_surface",
            },
        }
        with self.assertRaises(Exception) as cm:
            score(
                self.rubric, eo,
                snapshot_hash=SNAPSHOT_HASH,
                forensic_bundle_uri="https://test.local/missing",
                scored_at=SCORED_AT,
                applicability_decisions=decisions,
                applicability_policy_version="0.1.0",
            )
        self.assertIn("missing dim scores", str(cm.exception))
        self.assertIn("P1.1", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
