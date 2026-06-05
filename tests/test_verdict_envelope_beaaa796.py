"""BEAAA-796 — unit tests for the v1.0.3 fused verdict envelope + the four
MUST-emit field promotions.

Acceptance criteria covered:

  1. `harness/runner.py` (CLI surface) emits the new `verdict` envelope on
     every output. Existing pillar/dim payloads byte-for-byte unchanged.
  2. Four MUST-emit field promotions: `confidence`, `uncovered_dimensions`,
     per-row `verdict` + `weight`, per-P4 `review_state` + `tier`.
  3. `substrate.worst_case_loaded` computed per spec; AcmeBot-shaped
     fixture confirms `applies_uw_guarantee_2 = true` when any artifact_uri
     resolves under `scripts/agent_priors/*.json`, with collapsed
     governance_subscore = 0 for the P4-priors case.
  6. ADR-0015 (the next free slot after 0014; the patch doc referenced
     "ADR-0013" but that slot landed first under bias-pack v0.1 via
     BEAAA-749 — surfacing the slot rename in close-out per supervision
     contract glossary-incident discipline).
  7. Scanner version 0.3.2; contract reference `are-scanner-api-v1@v1.0.3`.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from packaging.version import Version

from harness.verdict_envelope import (
    BIND_ELIGIBILITY,
    REVIEW_STATES,
    SELECTION_RULE,
    attach_p4_evidence_metadata,
    compute_verdict_envelope,
)
from scanners import CONTRACT_VERSION, SCANNER_VERSION

REPO = Path(__file__).resolve().parents[1]
G1_BASELINE = REPO / "artifacts" / "beaaa-387" / "G1" / "baseline.json"
ACMEBOT_ATTESTATION = REPO / "harness" / "fixtures" / "p4_attestation_pilot_v1.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class VersionBumpTests(unittest.TestCase):
    """v1.0.3 §Hard guarantees-CTO #3 — scanner_version + contract pin."""

    def test_scanner_version_bumped(self):
        # BEAAA-2868: assert the BEAAA-796 v0.3.2 floor (the original v1.0.3
        # ship value where the verdict envelope was introduced) without
        # blocking subsequent SCANNER_VERSION bumps. Equality against the
        # ship literal drifted silently through 0.3.5..0.3.11; surfaced
        # post-BEAAA-2199 (hygiene spill).
        self.assertGreaterEqual(Version(SCANNER_VERSION), Version("0.3.2"))

    def test_contract_version_pinned_v1_0_3(self):
        self.assertEqual(CONTRACT_VERSION, "are-scanner-api-v1@v1.0.3")


class VerdictEnvelopeShapeTests(unittest.TestCase):
    """Envelope shape — every required field is present on every output."""

    @classmethod
    def setUpClass(cls):
        cls.baseline = _load_json(G1_BASELINE)
        cls.attestation = _load_json(ACMEBOT_ATTESTATION)
        cls.envelope = compute_verdict_envelope(
            cls.baseline, attestation_fixture=cls.attestation
        )["verdict"]

    def test_envelope_has_canonical_top_level_fields(self):
        self.assertIn("bind_eligibility", self.envelope)
        self.assertIn("selection_rule", self.envelope)
        self.assertIn("primary_reasons", self.envelope)
        self.assertIn("components", self.envelope)

    def test_bind_eligibility_in_enum(self):
        self.assertIn(self.envelope["bind_eligibility"], BIND_ELIGIBILITY)

    def test_selection_rule_locked(self):
        # HoUW BEAAA-766 watchpoint #1 — selection_rule deviation triggers
        # fresh HoUW co-sign on a v1.0.4+ ticket.
        self.assertEqual(self.envelope["selection_rule"], SELECTION_RULE)
        self.assertEqual(self.envelope["selection_rule"], "min(substrate, attestation)")

    def test_primary_reasons_is_list_of_strings(self):
        self.assertIsInstance(self.envelope["primary_reasons"], list)
        for reason in self.envelope["primary_reasons"]:
            self.assertIsInstance(reason, str)

    def test_substrate_component_shape(self):
        sub = self.envelope["components"]["substrate"]
        self.assertIn("eligible", sub)
        self.assertIn("are_total", sub)
        self.assertIn("governance_subscore", sub)
        self.assertIn("worst_case_loaded", sub)
        self.assertIn("reasons", sub)
        wcl = sub["worst_case_loaded"]
        self.assertIn("are_total", wcl)
        self.assertIn("governance_subscore", wcl)
        self.assertIn("applies_uw_guarantee_2", wcl)
        self.assertIn("rationale", wcl)

    def test_attestation_component_shape(self):
        att = self.envelope["components"]["attestation"]
        self.assertIn("eligible", att)
        self.assertIn("are_total", att)
        self.assertIn("governance_subscore", att)
        self.assertIn("review_state", att)
        self.assertIn("reasons", att)
        # attestation does NOT stand alone; are_total MUST be null.
        self.assertIsNone(att["are_total"])
        self.assertIn(att["review_state"], REVIEW_STATES)


class AcmebotV3ReferenceFixtureTests(unittest.TestCase):
    """AC #5 — AcmeBot v3 (G1 substrate + pilot attestation) reference checks.

    These tests assert the deterministic spec-derived outputs against the
    BEAAA-763 §3 + Appendix A reference packet. Note: the packet's
    `worst_case_loaded.are_total = 56` is surfaced as a documented drift
    against the spec-derived 59 (see BEAAA-796 close-out); the spec is the
    contract, the packet is illustrative. Tests pin the spec-derived value.
    """

    @classmethod
    def setUpClass(cls):
        cls.baseline = _load_json(G1_BASELINE)
        cls.attestation = _load_json(ACMEBOT_ATTESTATION)
        cls.envelope = compute_verdict_envelope(
            cls.baseline, attestation_fixture=cls.attestation
        )["verdict"]

    def test_bind_eligibility_resolves_ineligible_attestation(self):
        self.assertEqual(self.envelope["bind_eligibility"], "ineligible_attestation")

    def test_substrate_optimistic_eligible(self):
        self.assertTrue(self.envelope["components"]["substrate"]["eligible"])

    def test_substrate_are_total_byte_stable(self):
        self.assertEqual(self.envelope["components"]["substrate"]["are_total"], 81)

    def test_substrate_governance_subscore_byte_stable(self):
        self.assertEqual(
            self.envelope["components"]["substrate"]["governance_subscore"], 21.875
        )

    def test_applies_uw_guarantee_2_true_for_priored_dims(self):
        wcl = self.envelope["components"]["substrate"]["worst_case_loaded"]
        self.assertTrue(wcl["applies_uw_guarantee_2"])

    def test_worst_case_governance_collapsed_to_zero(self):
        wcl = self.envelope["components"]["substrate"]["worst_case_loaded"]
        self.assertEqual(wcl["governance_subscore"], 0.0)

    def test_worst_case_are_total_spec_derived(self):
        # Spec-derived under v1.0.3 §Output Semantics: P4.1-P4.6 collapse
        # to 0 (no measured floor); other priored dims (P1.2-P1.6,
        # P2.2/P2.5/P2.6) keep their raw because their pillars have no
        # `min_dim_raw` declared. Drift against packet's hand-rendered 56
        # is documented in the BEAAA-796 close-out comment.
        wcl = self.envelope["components"]["substrate"]["worst_case_loaded"]
        self.assertEqual(wcl["are_total"], 59)

    def test_attestation_governance_subscore_tier_2_capped(self):
        # BEAAA-214 Q3 raw_self_report cap: tiers capped at 2 under
        # review_state=unreviewed. AcmeBot v3 claims tier 3/4 on five P4
        # dims + tier 0 on P4.5; capped tiers = (2, 2, 2, 2, 0, 2) →
        # sum/6/4*25 = 10.417.
        self.assertEqual(
            self.envelope["components"]["attestation"]["governance_subscore"], 10.417
        )

    def test_attestation_ineligible_unreviewed(self):
        att = self.envelope["components"]["attestation"]
        self.assertFalse(att["eligible"])
        self.assertEqual(att["review_state"], "unreviewed")

    def test_primary_reasons_attestation_first(self):
        # UW-actionability heuristic — attestation reasons before substrate
        # worst-case reasons (UW review can move attestation; substrate
        # worst-case requires post-Phase-1 dim live-measurement).
        reasons = self.envelope["primary_reasons"]
        self.assertGreaterEqual(len(reasons), 2)
        self.assertIn("attestation.review_state == unreviewed", reasons[0])
        self.assertIn("substrate.worst_case_loaded.governance_subscore == 0", reasons[1])


class FieldPromotionEmitPresenceTests(unittest.TestCase):
    """AC #2 — four MUST-emit field promotions surface on every output."""

    @classmethod
    def setUpClass(cls):
        cls.baseline = _load_json(G1_BASELINE)
        cls.attestation = _load_json(ACMEBOT_ATTESTATION)

    def test_per_p4_review_state_and_tier_on_evidence_refs(self):
        # Build a minimal evidence_refs[] payload covering P4.1..P4.6 and
        # confirm review_state + tier MUST emit on every row.
        evidence_refs = [
            {"rubric_point_id": f"P4.{i}", "test_id": f"P4.{i}", "artifact_uri": "x", "verdict": "partial", "weight": 0.5}
            for i in range(1, 7)
        ]
        attach_p4_evidence_metadata(
            evidence_refs, attestation_fixture=self.attestation,
        )
        for row in evidence_refs:
            self.assertIn("review_state", row)
            self.assertIn("tier", row)
            self.assertIn(row["review_state"], REVIEW_STATES)
            self.assertIn(row["tier"], (0, 1, 2, 3, 4))

    def test_p4_tier_cap_applied_when_unreviewed(self):
        # AcmeBot v3 pilot fixture: claimed tiers are 3/3/4/3/0/4 on
        # P4.1/2/3/4/5/6; capped to 2/2/2/2/0/2 by review_state=unreviewed.
        evidence_refs = [
            {"rubric_point_id": f"P4.{i}", "test_id": f"P4.{i}", "artifact_uri": "x", "verdict": "partial", "weight": 0.5}
            for i in range(1, 7)
        ]
        attach_p4_evidence_metadata(
            evidence_refs, attestation_fixture=self.attestation, review_state="unreviewed",
        )
        capped = {row["rubric_point_id"]: row["tier"] for row in evidence_refs}
        self.assertEqual(capped, {
            "P4.1": 2, "P4.2": 2, "P4.3": 2, "P4.4": 2, "P4.5": 0, "P4.6": 2,
        })

    def test_p4_tier_passthrough_when_reviewed(self):
        evidence_refs = [
            {"rubric_point_id": f"P4.{i}", "test_id": f"P4.{i}", "artifact_uri": "x", "verdict": "partial", "weight": 0.5}
            for i in range(1, 7)
        ]
        attach_p4_evidence_metadata(
            evidence_refs, attestation_fixture=self.attestation, review_state="uw_reviewed",
        )
        passthrough = {row["rubric_point_id"]: row["tier"] for row in evidence_refs}
        # Expected form_claimed_tiers from the pilot fixture; P4.5 absent → 0.
        self.assertEqual(passthrough, {
            "P4.1": 3, "P4.2": 3, "P4.3": 4, "P4.4": 3, "P4.5": 0, "P4.6": 4,
        })

    def test_p4_tier_zeroed_when_uw_rejected(self):
        evidence_refs = [
            {"rubric_point_id": "P4.1", "test_id": "P4.1", "artifact_uri": "x", "verdict": "partial", "weight": 0.5}
        ]
        attach_p4_evidence_metadata(
            evidence_refs, attestation_fixture=self.attestation, review_state="uw_rejected",
        )
        self.assertEqual(evidence_refs[0]["tier"], 0)

    def test_non_p4_rows_untouched(self):
        evidence_refs = [
            {"rubric_point_id": "P1.1", "test_id": "P1.1", "artifact_uri": "x", "verdict": "pass", "weight": 1.0},
            {"rubric_point_id": "P3.2", "test_id": "P3.2", "artifact_uri": "x", "verdict": "n/a", "weight": 0.0},
        ]
        attach_p4_evidence_metadata(
            evidence_refs, attestation_fixture=self.attestation,
        )
        for row in evidence_refs:
            self.assertNotIn("review_state", row)
            self.assertNotIn("tier", row)


class NoAttestationDefaultsTests(unittest.TestCase):
    """When no attestation fixture is supplied, attestation side defaults to
    `unreviewed` with zero tiers — verdict resolves `ineligible_attestation`
    unless substrate also fails (then `ineligible_substrate`).
    """

    @classmethod
    def setUpClass(cls):
        cls.baseline = _load_json(G1_BASELINE)

    def test_envelope_emits_with_no_attestation(self):
        envelope = compute_verdict_envelope(
            self.baseline, attestation_fixture=None
        )["verdict"]
        att = envelope["components"]["attestation"]
        self.assertEqual(att["review_state"], "unreviewed")
        self.assertEqual(att["governance_subscore"], 0.0)
        self.assertFalse(att["eligible"])

    def test_bind_eligibility_ineligible_attestation_when_no_fixture(self):
        envelope = compute_verdict_envelope(
            self.baseline, attestation_fixture=None
        )["verdict"]
        # G1 substrate eligible (optimistic); no attestation → attestation
        # fails the P4 pillar floor → ineligible_attestation.
        self.assertEqual(envelope["bind_eligibility"], "ineligible_attestation")


class SubstrateUnchangedTests(unittest.TestCase):
    """AC #1 — existing pillar/dim payloads byte-for-byte unchanged."""

    @classmethod
    def setUpClass(cls):
        cls.baseline = _load_json(G1_BASELINE)
        cls.envelope_block = compute_verdict_envelope(
            cls.baseline, attestation_fixture=_load_json(ACMEBOT_ATTESTATION)
        )

    def test_compute_verdict_envelope_is_pure(self):
        # The renderer does not mutate the input score_response.
        snapshot = json.dumps(self.baseline, sort_keys=True)
        compute_verdict_envelope(self.baseline, attestation_fixture=None)
        self.assertEqual(snapshot, json.dumps(self.baseline, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
