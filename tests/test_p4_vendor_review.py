"""BEAAA-484 — P4 GOVERNANCE Tier B vendor-evidence review smoke + N=5 tests.

Smoke pipeline:
    questionnaire YAML spec  ->  vendor packet fixture (YAML)
                              ->  intake.vendor_packet_to_attestation_form
                              ->  score_p4_attestation.score_form (existing,
                                  v0.1-LOCKED scorer — not forked)
                              ->  review.review_vendor_packet
                              ->  evidence_refs + uncovered_dimensions

N=5 determinism: the questionnaire pipeline is deterministic given fixed
input. Running review_vendor_packet five times on the same fixture must
yield byte-identical evidence_refs (raw, verdict, weight) — i.e. delta ±0
on every dim. Any drift here is a code regression, full stop.

Anonymous-path: packet=None must surface every GOV.* rubric_point_id in
uncovered_dimensions so the Underwriter prices Path B at worst-case.

No real customer or carrier names appear in the fixture (Carrier Rule).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.p4_questionnaire import (  # noqa: E402
    GOV_RUBRIC_POINTS,
    GOV_TO_P4,
    PROBE_SET_VERSION,
    QUESTIONNAIRE_SPEC_VERSION,
    load_questionnaire_spec,
    vendor_packet_to_attestation_form,
)
from scanners.p4_vendor_review import (  # noqa: E402
    PATH_B_COVERAGE_CAP,
    VERDICT_FAIL,
    VERDICT_PARTIAL,
    VERDICT_PASS,
    review_anonymous_path,
    review_vendor_packet,
)
from scripts import score_p4_attestation as p4  # noqa: E402

FIXTURE_PATH = REPO / "harness" / "fixtures" / "p4_vendor_packet_fixture_v1.yaml"


def _load_fixture() -> dict:
    return yaml.safe_load(FIXTURE_PATH.read_text())


class QuestionnaireSpecTests(unittest.TestCase):
    """Spec sanity: the GOV.* rubric point identifiers in the questionnaire
    spec match the v1.0 contract GOVERNANCE pillar listing."""

    def test_six_canonical_rubric_points(self):
        self.assertEqual(len(GOV_RUBRIC_POINTS), 6)
        self.assertEqual(
            set(GOV_RUBRIC_POINTS),
            {
                "GOV.AUDIT_TRAIL",
                "GOV.CHANGE_MGMT",
                "GOV.KILL_SWITCH",
                "GOV.HITL_POLICY",
                "GOV.MONITORING",
                "GOV.INCIDENT_RESPONSE",
            },
        )

    def test_each_rubric_point_maps_to_p4_dim(self):
        for rubric_point_id in GOV_RUBRIC_POINTS:
            self.assertIn(GOV_TO_P4[rubric_point_id], {"P4.1", "P4.2", "P4.3", "P4.4", "P4.5", "P4.6"})

    def test_probe_set_version_pinned(self):
        self.assertEqual(QUESTIONNAIRE_SPEC_VERSION, "0.1.0")
        self.assertEqual(PROBE_SET_VERSION, "p4_questionnaire_v0.1.0")

    def test_spec_carrier_rule_clean(self):
        """Spec YAML must not mention specific carrier names."""
        spec_text = (REPO / "harness" / "p4_questionnaire" / "questionnaire_v0_1.yaml").read_text()
        for banned in ("Chubb", "AIG", "Munich Re", "Swiss Re", "Travelers", "Liberty Mutual", "Berkshire Hathaway", "HSB"):
            self.assertNotIn(banned, spec_text, msg=f"carrier name {banned!r} leaked into questionnaire spec")


class IntakeShapeTests(unittest.TestCase):
    """Intake produces a form shape the v0.1-LOCKED scorer accepts."""

    def test_fixture_maps_to_valid_attestation_form(self):
        packet = _load_fixture()
        form = vendor_packet_to_attestation_form(packet)
        errors = p4.validate_form(form)
        self.assertEqual(errors, [], msg=f"intake produced an invalid form: {errors}")
        # all six P4_* blocks present
        for canonical_block_key in ("P4_1", "P4_2", "P4_3", "P4_4", "P4_5", "P4_6"):
            self.assertIn(canonical_block_key, form["dims"])


class SmokeReviewTests(unittest.TestCase):
    """Cooperative vendor packet smoke — six evidence_refs emitted, all in
    v1.0 output schema shape, no uncovered_dimensions on the cooperative path."""

    def test_smoke_emits_six_evidence_refs(self):
        packet = _load_fixture()
        result = review_vendor_packet(packet)
        self.assertFalse(result["rejected"], msg=result.get("rejection_reason"))
        self.assertEqual(len(result["evidence_refs"]), 6)
        self.assertEqual(result["uncovered_dimensions"], [])

    def test_each_evidence_ref_matches_v1_0_schema(self):
        packet = _load_fixture()
        result = review_vendor_packet(packet)
        seen_rubric_ids = set()
        for ref in result["evidence_refs"]:
            # v1.0 required fields per contracts/are-scanner-api-v1.md
            self.assertIn(ref["rubric_point_id"], GOV_RUBRIC_POINTS)
            self.assertNotIn(ref["rubric_point_id"], seen_rubric_ids,
                             msg="duplicate rubric_point_id in evidence_refs")
            seen_rubric_ids.add(ref["rubric_point_id"])
            self.assertIsInstance(ref["test_id"], str)
            self.assertTrue(ref["test_id"].startswith("p4_questionnaire::"))
            self.assertIsInstance(ref["artifact_uri"], str)
            self.assertTrue(ref["artifact_uri"], msg="artifact_uri must be reachable, not empty")
            self.assertIn(ref["verdict"], {VERDICT_PASS, VERDICT_PARTIAL, VERDICT_FAIL})
            self.assertGreaterEqual(ref["weight"], 0.0)
            self.assertLessEqual(ref["weight"], PATH_B_COVERAGE_CAP)
        self.assertEqual(seen_rubric_ids, set(GOV_RUBRIC_POINTS))

    def test_path_b_coverage_cap_surfaced(self):
        result = review_vendor_packet(_load_fixture())
        self.assertEqual(result["path_b_coverage_cap"], PATH_B_COVERAGE_CAP)
        self.assertEqual(result["probe_set_version"], PROBE_SET_VERSION)
        self.assertEqual(result["questionnaire_spec_version"], QUESTIONNAIRE_SPEC_VERSION)


class AnonymousPathTests(unittest.TestCase):
    """No questionnaire returned -> every GOV.* in uncovered_dimensions."""

    def test_no_packet_emits_all_uncovered(self):
        result = review_vendor_packet(None)
        self.assertEqual(result["evidence_refs"], [])
        self.assertEqual(set(result["uncovered_dimensions"]), set(GOV_RUBRIC_POINTS))
        self.assertFalse(result["rejected"])

    def test_empty_packet_emits_all_uncovered(self):
        result = review_vendor_packet({})
        self.assertEqual(result["evidence_refs"], [])
        self.assertEqual(set(result["uncovered_dimensions"]), set(GOV_RUBRIC_POINTS))

    def test_anonymous_helper_matches_no_packet(self):
        a = review_anonymous_path()
        b = review_vendor_packet(None)
        self.assertEqual(a["uncovered_dimensions"], b["uncovered_dimensions"])
        self.assertEqual(a["evidence_refs"], b["evidence_refs"])


class SignatureRejectionPathTests(unittest.TestCase):
    """Signature_mode insufficient => scorer rejects, review wrapper surfaces
    all GOV.* as uncovered (no silent downgrade to priors)."""

    def test_notarized_email_with_tier3_claim_rejects(self):
        packet = _load_fixture()
        # Downgrade signature so claimed tier > sig cap (cap on notarized_email = 2)
        packet["envelope"]["signature_block"]["signature_mode"] = "notarized_email"
        result = review_vendor_packet(packet)
        self.assertTrue(result["rejected"])
        self.assertEqual(result["evidence_refs"], [])
        self.assertEqual(set(result["uncovered_dimensions"]), set(GOV_RUBRIC_POINTS))


class N5DeterminismTests(unittest.TestCase):
    """N=5 calibration delta on the questionnaire pipeline.

    Deterministic given fixed input -> byte-identical evidence_refs across
    five runs. Any drift here is a code regression and would re-classify the
    ticket as rule-change per supervision contract §3.
    """

    def test_n5_byte_identical(self):
        packet = _load_fixture()
        signatures = []
        for _ in range(5):
            result = review_vendor_packet(packet)
            # Strip the unstable `scored` payload from the signature — it
            # contains the same per-dim raws under a different key shape
            # already represented in evidence_refs; this keeps the signature
            # focused on the contract output surface.
            sig = json.dumps(
                {
                    "evidence_refs": result["evidence_refs"],
                    "uncovered_dimensions": result["uncovered_dimensions"],
                    "rejected": result["rejected"],
                    "rejection_reason": result["rejection_reason"],
                    "path_b_coverage_cap": result["path_b_coverage_cap"],
                    "probe_set_version": result["probe_set_version"],
                    "questionnaire_spec_version": result["questionnaire_spec_version"],
                },
                sort_keys=True,
                default=str,
            )
            signatures.append(sig)
        self.assertEqual(len(set(signatures)), 1,
                         msg="N=5 delta on questionnaire pipeline must be ±0; non-zero drift = halt + re-classify per BEAAA-484 §Classification")


class FixtureCarrierRuleTests(unittest.TestCase):
    """Carrier Rule: no carrier name and no real customer name in the fixture."""

    def test_fixture_carrier_rule_clean(self):
        text = FIXTURE_PATH.read_text()
        for banned in ("Chubb", "AIG", "Munich Re", "Swiss Re", "Travelers", "Liberty Mutual", "Berkshire Hathaway", "HSB"):
            self.assertNotIn(banned, text, msg=f"carrier name {banned!r} leaked into fixture")
        # Customer-name guard — no Acme either (the pilot fixture used Acme)
        self.assertNotIn("Acme", text)


if __name__ == "__main__":
    unittest.main()
