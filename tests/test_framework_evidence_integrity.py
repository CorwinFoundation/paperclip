"""Regression coverage for evidence-qualified framework observations."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from framework_assessment import build_framework_envelope, verify_evidence_bytes

ROOT = Path(__file__).resolve().parents[1]


def ref(verdict="pass", **updates):
    result = {"test_id": "privacy", "rubric_point_id": "P3.3",
              "artifact_uri": "evidence://observation",
              "sha256": hashlib.sha256(b"observation").hexdigest(),
              "verdict": verdict}
    result.update(updates)
    return result


def project(refs, registry=None):
    score = {"are_total": 84, "band": "B", "subscores": {"ROBUSTNESS": 22},
             "run_timestamp": "2026-10-22T00:00:00Z", "evidence_refs": refs}
    if registry is None:
        registry = {"registry_version": "1.0.0", "frameworks": [{
            "display_name": "AIUC-1-mapped technical assessment",
            "mapping_version": "test", "limitations": [],
            "controls": [{"control_id": "A009", "rubric_point_prefixes": [],
                          "test_ids": ["privacy"]}]}]}
    before = deepcopy(score)
    result = build_framework_envelope(score, registry=registry)
    assert score == before
    assert result["backbond_score"]["total"] == 84
    assert result["backbond_score"]["band"] == "B"
    assert result["backbond_score"]["subscores"] == {"ROBUSTNESS": 22}
    return {row["control_id"]: row for row in
            result["aiuc_1_mapped_technical_assessment"]["controls"]}


class EvidenceQualifiedStatusTests(unittest.TestCase):
    def test_missing_or_unusable_reference_cannot_pass(self):
        for updates in [
            {"artifact_uri": None}, {"artifact_uri": ""}, {"artifact_uri": "  "},
            {"artifact_uri": 42}, {"sha256": None}, {"sha256": ""},
            {"sha256": "a"}, {"sha256": "g" * 64}, {"sha256": 123},
            {"sha256": "a" * 63}, {"sha256": "a" * 65},
        ]:
            with self.subTest(updates=updates):
                row = project([ref(**updates)])["A009"]
                self.assertEqual(row["status"], "not evaluated")
                self.assertEqual(row["evidence_refs"], [])

    def test_one_valid_pass_does_not_hide_missing_reference(self):
        row = project([ref(), ref(artifact_uri=None)])["A009"]
        self.assertEqual(row["status"], "not evaluated")
        self.assertEqual(len(row["evidence_refs"]), 1)

    def test_unexecuted_or_unknown_observation_prevents_pass_in_either_order(self):
        for verdict in ["could_not_run", "incomplete", "invalid", "unresolved",
                        "error", "running", "", None, "unexpected"]:
            for reverse in [False, True]:
                with self.subTest(verdict=verdict, reverse=reverse):
                    refs = [ref(), ref(verdict)]
                    if reverse:
                        refs.reverse()
                    self.assertEqual(project(refs)["A009"]["status"], "not evaluated")

    def test_valid_failure_remains_visible_among_partial_results(self):
        for other in [ref(), ref("could_not_run"), ref(artifact_uri=None)]:
            self.assertEqual(project([ref("failed"), other])["A009"]["status"], "failed")

    def test_failure_without_usable_evidence_is_not_an_evidenced_verdict(self):
        row = project([ref("fail", sha256=None)])["A009"]
        self.assertEqual(row["status"], "not evaluated")
        self.assertEqual(row["evidence_refs"], [])

    def test_terminal_pass_and_non_applicable_outcomes(self):
        for verdicts, expected in [
            ([], "not evaluated"), (["pass", "passed"], "passed"),
            (["pass", "not_applicable"], "passed"),
            (["n/a", "not applicable"], "not applicable"),
            (["not applicable", "could_not_run"], "not evaluated"),
        ]:
            with self.subTest(verdicts=verdicts):
                self.assertEqual(project([ref(v) for v in verdicts])["A009"]["status"], expected)

    def test_digest_alias_and_uppercase_hex_are_supported(self):
        digest = hashlib.sha256(b"observation").hexdigest()
        row = project([ref(sha256=None, artifact_sha256=digest.upper())])["A009"]
        self.assertEqual(row["status"], "passed")
        self.assertEqual(row["evidence_refs"][0]["sha256"], digest)
        verify_evidence_bytes({"artifact_sha256": digest.upper()}, b"observation")

    def test_digest_verification_rejects_invalid_metadata_and_substitution(self):
        for digest in [None, "", "g" * 64, "a" * 63, 123]:
            with self.subTest(digest=digest):
                with self.assertRaises(ValueError):
                    verify_evidence_bytes({"sha256": digest}, b"observation")
        with self.assertRaises(ValueError):
            verify_evidence_bytes(ref(), b"substituted")

    def test_unusable_extension_reference_is_not_exported(self):
        registry = {"registry_version": "1.0.0", "frameworks": [{
            "display_name": "AIUC-1-mapped technical assessment",
            "mapping_version": "test", "limitations": [], "controls": []}]}
        score = {"run_timestamp": "2026-10-22", "evidence_refs": [ref(sha256="bad")]}
        result = build_framework_envelope(score, registry=registry)
        self.assertEqual(result["backbond_extensions"]["evidence_refs"], [])


class DisclosureMappingTests(unittest.TestCase):
    def test_disclosure_success_and_failure_reach_both_requirements(self):
        registry = json.loads((ROOT / "rubric/framework_registry.v1.json").read_text())
        for verdict, expected in [("pass", "passed"), ("fail", "failed"),
                                  ("could_not_run", "not evaluated")]:
            with self.subTest(verdict=verdict):
                controls = project([ref(verdict, test_id="ai_disclosure",
                                        rubric_point_id="UNMAPPED")], registry)
                for control_id in ["E016.1", "E018"]:
                    self.assertEqual(controls[control_id]["status"], expected)
                    self.assertEqual(controls[control_id]["evidence_refs"][0]["test_id"],
                                     "ai_disclosure")

    def test_disclosure_mapping_keeps_scoring_and_underwriting_flags(self):
        # Read only the simple declaration; no YAML dependency is needed.
        mapping = (ROOT / "rubric/probe_mappings/ai_disclosure.yaml").read_text()
        line = next(line for line in mapping.splitlines()
                    if line.startswith("aiuc_1_crosswalk:"))
        self.assertEqual(set(json.loads(line.split(":", 1)[1])), {"E016.1", "E018"})
        self.assertIn("scoring_active: false", mapping.splitlines())
        self.assertIn("underwriting_active: false", mapping.splitlines())


if __name__ == "__main__":
    unittest.main()

