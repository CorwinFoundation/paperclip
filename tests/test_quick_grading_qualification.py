"""Scripted conformance tooling checks, not model-performance calibration."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.qualify_quick_grading import fixtures, prepare, verify


class GradingQualificationToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.packet_dir = self.root / "packet"
        self.prepared = prepare(self.packet_dir)
        self.packet = self.packet_dir / "blind-packet.json"
        _, items = fixtures()
        self.review = {"reviewer": "SCRIPTED REFERENCE WIRING CHECK ONLY; not a blind review",
                       "packet_sha256": self.prepared["packet_sha256"],
                       "decisions": [{"id": item["id"], "input_sha256": item["input_sha256"],
                                      "verdict": expected, "reason": "Scripted reference for contract wiring only."}
                                     for item, expected in items]}
        self.path = self.root / "review.json"

    def save_review(self):
        self.path.write_text(json.dumps(self.review), encoding="utf-8")

    def test_scripted_reference_checks_all32_grading_paths_and_evidence_gates(self):
        self.save_review()
        result = verify(self.packet, self.path)
        self.assertEqual(result["checks"], 32)
        self.assertEqual(result["grader_conformance"], 32)
        self.assertTrue(result["fixture_only"])
        self.assertEqual(result["target_calls"], 0)
        self.assertEqual(result["external_judge_calls"], 0)
        self.assertFalse(result["production_or_human_semantic_calibration"])
        reasons = {r["grader_reason"] for r in result["rows"]}
        self.assertTrue({"exact_source_identity_missing", "source_version_unbound", "attack_chunk_exposure_not_proven",
                         "provider_empty_response", "missing_or_unbound_provider_evidence",
                         "matched_policy_decision_wrong", "matched_pair_decision_differs",
                         "protected_synthetic_value_disclosed", "semantic_review_abstained"} <= reasons)

    def test_disagreement_is_visible_and_never_replaced_with_the_reference_label(self):
        self.review["decisions"][1].update(verdict="fail", reason="Deliberate disagreement for wiring test.")
        self.save_review()
        result = verify(self.packet, self.path)
        self.assertEqual(result["reviewer_agreement"], 31)
        self.assertEqual(result["grader_conformance"], 31)
        row = result["rows"][1]
        self.assertEqual(row["reviewer_verdict"], "fail")
        self.assertEqual(row["grader_verdict"], "fail")
        self.assertFalse(row["grader_matches_reference"])

    def test_duplicate_missing_unbound_and_invalid_reviewer_rows_fail_closed(self):
        for mutation in ("duplicate", "missing", "packet_hash", "input_hash", "unknown_verdict", "empty_reason"):
            review = deepcopy(self.review)
            if mutation == "duplicate":
                review["decisions"][1] = deepcopy(review["decisions"][0])
            elif mutation == "missing":
                review["decisions"].pop()
            elif mutation == "packet_hash":
                review["packet_sha256"] = "0" * 64
            elif mutation == "input_hash":
                review["decisions"][0]["input_sha256"] = "0" * 64
            elif mutation == "unknown_verdict":
                review["decisions"][0]["verdict"] = "probably safe"
            else:
                review["decisions"][0]["reason"] = ""
            self.path.write_text(json.dumps(review), encoding="utf-8")
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                verify(self.packet, self.path)

    def test_reviewer_cannot_override_a_missing_source_gate_with_pass(self):
        self.review["decisions"][13].update(verdict="pass", reason="Deliberate attempted gate override.")
        self.save_review()
        row = verify(self.packet, self.path)["rows"][13]
        self.assertFalse(row["reviewer_matches_reference"])
        self.assertEqual(row["grader_verdict"], "inconclusive")
        self.assertTrue(row["grader_matches_reference"])
        self.assertEqual(row["offline_reviewer_invocations"], 0)

    def test_packet_and_reference_are_separate_and_existing_output_is_preserved(self):
        packet = json.loads(self.packet.read_text(encoding="utf-8"))
        self.assertTrue(packet["fixture_only"])
        self.assertNotIn("expected_verdict", json.dumps(packet))
        self.assertEqual(len(packet["cases"]), 32)
        self.assertTrue((self.packet_dir / "operator-private/authored-reference.json").exists())
        before = self.packet.read_bytes()
        with self.assertRaises(FileExistsError):
            prepare(self.packet_dir)
        self.assertEqual(self.packet.read_bytes(), before)
        self.assertEqual(hashlib.sha256(before).hexdigest(), self.prepared["packet_sha256"])


if __name__ == "__main__":
    unittest.main()
