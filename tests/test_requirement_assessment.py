"""Contract tests for scope, completeness and byte-bound evidence decisions."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from framework_assessment import build_framework_envelope
from requirement_assessment import (
    OBSERVATION_SCHEMA, PLAN_SCHEMA, build_requirement_assessment,
    directory_reader, load_registry, validate_plan,
    plan_digest,
)

NOW = "2026-10-26T15:00:00+00:00"
SCOPE = {"target_id": "agent-1", "deployment_id": "test-configuration-1", "snapshot_sha256": "a" * 64}


def registry():
    return {"frameworks": [{"framework_id": "test", "standard_edition": "test@1", "mapping_version": "mapping@1",
                           "controls": [{"control_id": "validation", "candidate_test_ids": ["existing_family"],
                                         "required_document_ids": ["validation-plan"]}]}]}


def plan():
    return {"schema": PLAN_SCHEMA, "assessment_id": "assessment-1", "scope": deepcopy(SCOPE),
            "approval": {"reviewer": "scope-owner", "basis": "reviewed test scope", "approved_at": "2026-10-26T10:00:00+00:00"},
            "valid_from": "2026-10-26T09:00:00+00:00", "valid_until": "2026-10-27T10:00:00+00:00",
            "controls": [{"framework_id": "test", "control_id": "validation", "mapping_version": "mapping@1",
                          "applicability": "applicable", "justification": "Customer-approved validation scope.",
                          "required_tests": [{"test_id": "existing_family", "case_id": "case-1", "probe_set_version": "tests@1", "trials": 2}]}]}


def observation(trial=0, **updates):
    body = {"schema": OBSERVATION_SCHEMA, "assessment_id": "assessment-1", "scope": deepcopy(SCOPE),
            "plan_sha256": plan_digest(plan()),
            "test_id": "existing_family", "case_id": "case-1", "probe_set_version": "tests@1",
            "trial_index": trial, "verdict": "passed", "execution_kind": "scanner_mediated",
            "observed_at": "2026-10-26T12:00:00+00:00"}
    body.update(updates)
    raw = json.dumps(body).encode()
    digest = hashlib.sha256(raw).hexdigest()
    return body | {"artifact_uri": "evidence://" + digest, "sha256": digest}, raw


def bundle(refs, documents=()):
    return {"assessment_id": "assessment-1", "scope": deepcopy(SCOPE), "evidence_refs": refs, "documents": list(documents)}


def project(refs, *, artifacts=None, documents=(), selected_plan=None):
    if artifacts is None:
        artifacts = {ref["sha256"]: raw for ref, raw in refs}
        refs = [ref for ref, raw in refs]
    result = build_requirement_assessment(bundle(refs, documents), plan=selected_plan or plan(),
             registry=registry(), evaluated_at=NOW,
             artifact_reader=lambda ref: artifacts[ref["sha256"]])
    return result, result["frameworks"][0]["controls"][0]


class CompletenessTests(unittest.TestCase):
    def test_missing_plan_never_evaluates_requirements(self):
        result = build_requirement_assessment({}, registry=registry(), evaluated_at=NOW)
        self.assertEqual(result["scope_status"], "not verified")
        self.assertEqual(result["frameworks"][0]["controls"][0]["technical_status"], "not evaluated")

    def test_passing_subset_is_incomplete(self):
        result, row = project([observation()])
        self.assertEqual(row["technical_status"], "not evaluated")
        self.assertFalse(row["evidence_complete_for_plan"])
        self.assertEqual(row["missing_tests"][0]["trial_index"], 1)
        self.assertEqual(row["missing_documents"], ["validation-plan"])

    def test_all_tests_do_not_replace_documentary_evidence(self):
        _, row = project([observation(), observation(1)])
        self.assertEqual(row["technical_status"], "passed")
        self.assertFalse(row["evidence_complete_for_plan"])
        self.assertEqual(row["requirement_conclusion"], "not assessed")

    def test_complete_reviewed_plan_never_becomes_certification(self):
        refs = [observation(), observation(1)]
        document = {"document_id": "validation-plan", "artifact_uri": "evidence://document",
                    "sha256": hashlib.sha256(b"approved plan bytes").hexdigest(),
                    "review_status": "accepted", "reviewer": "human-reviewer", "reviewed_at": NOW,
                    "assessment_id": "assessment-1", "scope": deepcopy(SCOPE)}
        document["plan_sha256"] = plan_digest(plan())
        artifacts = {ref["sha256"]: raw for ref, raw in refs}
        artifacts[document["sha256"]] = b"approved plan bytes"
        result, row = project([r for r, _ in refs], artifacts=artifacts, documents=[document])
        self.assertTrue(row["evidence_complete_for_plan"])
        self.assertEqual(result["certification_status"], "not certification")
        self.assertEqual(row["requirement_conclusion"], "not assessed")
        for update in [{"reviewer": ""}, {"review_status": "claimed"}, {"scope": {}}, {"reviewed_at": "2026-10-27T00:00:00Z"}]:
            with self.subTest(update=update):
                _, partial = project([r for r, _ in refs], artifacts=artifacts, documents=[document | update])
                self.assertFalse(partial["evidence_complete_for_plan"])

    def test_failure_remains_visible_among_missing_trials(self):
        _, row = project([observation(verdict="failed")])
        self.assertEqual(row["technical_status"], "failed")
        self.assertTrue(row["missing_tests"])

    def test_duplicate_results_cannot_satisfy_trial_counts(self):
        item = observation()
        for refs in [[item, item], [item, item, observation(1)]]:
            with self.subTest(count=len(refs)):
                _, row = project(refs)
                self.assertEqual(row["technical_status"], "not evaluated")
                self.assertIn("duplicate_observation", row["issues"])

    def test_empty_selected_test_set_cannot_pass_technical_control(self):
        selected = plan()
        selected["controls"][0]["required_tests"] = []
        _, row = project([], selected_plan=selected)
        self.assertEqual(row["technical_status"], "not evaluated")
        self.assertFalse(row["evidence_complete_for_plan"])

    def test_inconclusive_and_per_test_na_never_count_as_pass(self):
        for verdict in ["inconclusive", "could_not_run", "n/a", "running", "error"]:
            with self.subTest(verdict=verdict):
                _, row = project([observation(), observation(1, verdict=verdict)])
                self.assertEqual(row["technical_status"], "not evaluated")


class BindingTests(unittest.TestCase):
    def test_fixture_stale_future_and_mismatched_evidence_are_rejected(self):
        for update in [
            {"execution_kind": "scripted_fixture"}, {"execution_kind": "attestation"},
            {"observed_at": "2026-10-26T08:00:00Z"}, {"observed_at": "2026-10-26T16:00:00Z"},
            {"observed_at": "2026-10-26T09:30:00Z"}, {"assessment_id": "another-assessment"},
            {"scope": SCOPE | {"deployment_id": "another-deployment"}},
            {"verdict": {"pass": True}}, {"trial_index": True},
        ]:
            with self.subTest(update=update):
                result, row = project([observation(**update), observation(1)])
                self.assertEqual(row["technical_status"], "not evaluated")
                self.assertEqual(len(result["rejected_evidence"]), 1)

    def test_reference_digest_does_not_validate_fabricated_or_substituted_bytes(self):
        ref, raw = observation()
        for artifacts in [{ref["sha256"]: b"substitution"}, {ref["sha256"]: b"{}"}]:
            result, row = project([ref], artifacts=artifacts)
            self.assertEqual(row["technical_status"], "not evaluated")
            self.assertTrue(result["rejected_evidence"])

    def test_reference_metadata_cannot_override_artifact(self):
        ref, raw = observation(verdict="failed")
        ref["verdict"] = "passed"
        result, row = project([ref], artifacts={ref["sha256"]: raw})
        self.assertEqual(row["technical_status"], "not evaluated")
        self.assertTrue(result["rejected_evidence"])

    def test_unreadable_artifact_and_missing_reader_fail_closed(self):
        ref, _ = observation()
        result = build_requirement_assessment(bundle([ref]), plan=plan(), registry=registry(), evaluated_at=NOW)
        self.assertTrue(result["rejected_evidence"])
        self.assertFalse(result["frameworks"][0]["controls"][0]["evidence_complete_for_plan"])

    def test_malformed_refs_and_document_entries_do_not_crash_or_pass(self):
        result = build_requirement_assessment(bundle([None, [], "text", {}], [None]),
                 plan=plan(), registry=registry(), evaluated_at=NOW)
        self.assertEqual(len(result["rejected_evidence"]), 4)
        self.assertEqual(result["frameworks"][0]["controls"][0]["technical_status"], "not evaluated")

    def test_scope_mismatch_prevents_even_valid_evidence_from_being_used(self):
        data = bundle([observation()[0]])
        data["scope"]["target_id"] = "other-agent"
        result = build_requirement_assessment(data, plan=plan(), registry=registry(), evaluated_at=NOW)
        self.assertEqual(result["scope_status"], "not verified")

    def test_evidence_cannot_be_reused_after_approved_plan_changes(self):
        selected = plan()
        selected["approval"]["basis"] = "A revised scope was approved."
        result, row = project([observation(), observation(1)], selected_plan=selected)
        self.assertEqual(len(result["rejected_evidence"]), 2)
        self.assertEqual(row["technical_status"], "not evaluated")

    def test_local_reader_uses_hash_filename_not_uri(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            item, raw = observation()
            (root / (item["sha256"] + ".json")).write_bytes(raw)
            item["artifact_uri"] = "https://untrusted.invalid/../../private"
            self.assertEqual(directory_reader(root)(item), raw)
            with self.assertRaises(ValueError):
                directory_reader(root)({"sha256": "../secret"})

    def test_projection_does_not_export_arbitrary_reference_fields(self):
        first, raw = observation()
        first["Authorization"] = "do-not-export"
        other, other_raw = observation(1)
        _, row = project([first, other], artifacts={first["sha256"]: raw, other["sha256"]: other_raw})
        self.assertNotIn("Authorization", row["evidence_refs"][0])


class PlanAndRegistryTests(unittest.TestCase):
    def test_invalid_scope_approval_mapping_and_applicability_fail_validation(self):
        changes = [
            ("invalid_snapshot", lambda p: p["scope"].update(snapshot_sha256="bad")),
            ("missing_reviewer", lambda p: p["approval"].update(reviewer="")),
            ("empty_validity_window", lambda p: p.update(valid_until=p["valid_from"])),
            ("old_mapping", lambda p: p["controls"][0].update(mapping_version="old")),
            ("missing_justification", lambda p: p["controls"][0].update(justification="")),
            ("duplicate_control", lambda p: p["controls"].append(deepcopy(p["controls"][0]))),
            ("boolean_trials", lambda p: p["controls"][0]["required_tests"][0].update(trials=True)),
            ("unmapped_test", lambda p: p["controls"][0]["required_tests"][0].update(test_id="unmapped")),
            ("zero_trials", lambda p: p["controls"][0]["required_tests"][0].update(trials=0)),
        ]
        for case_name, mutate in changes:
            with self.subTest(case=case_name):
                selected = plan()
                mutate(selected)
                with self.assertRaises(ValueError): validate_plan(selected, registry())

    def test_reviewed_exclusion_is_separate_from_missing_evidence(self):
        selected = plan()
        selected["controls"][0].update(applicability="not applicable", required_tests=[])
        result = build_requirement_assessment(bundle([]), plan=selected, registry=registry(), evaluated_at=NOW)
        row = result["frameworks"][0]["controls"][0]
        self.assertEqual(row["technical_status"], "not applicable")
        self.assertEqual(row["requirement_conclusion"], "not assessed")

    def test_registry_contains_all_38_annex_controls_without_duplicates(self):
        loaded = load_registry()
        iso = next(f for f in loaded["frameworks"] if f["framework_id"] == "iso-iec-42001")
        expected = {f"A.{g}.{i}" for g, end in [(2,4),(3,3),(4,6),(5,5),(7,6),(8,5),(9,4),(10,4)] for i in range(2,end+1)}
        expected |= {"A.6.1.2", "A.6.1.3"} | {f"A.6.2.{i}" for i in range(2,9)}
        actual = [c["control_id"] for c in iso["controls"] if c["control_id"].startswith("A.")]
        self.assertEqual(set(actual), expected)
        self.assertEqual(len(actual), 38)

    def test_existing_framework_projection_remains_additive_and_non_mutating(self):
        score = {"run_timestamp": NOW, "are_total": 84, "band": "B", "subscores": {"OPS": 21}, "evidence_refs": []}
        before = deepcopy(score)
        result = build_framework_envelope(score)
        self.assertEqual(score, before)
        self.assertEqual(result["backbond_score"]["total"], 84)
        self.assertEqual(result["requirements_evidence"]["conformity_status"], "not assessed")


if __name__ == "__main__":
    unittest.main()
