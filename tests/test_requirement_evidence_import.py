"""Existing Scanner artifact import; no target traffic or reinterpretation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from requirement_assessment import (
    build_requirement_assessment, directory_reader, import_registered_case, load_registry, PLAN_SCHEMA,
)

NOW = "2026-10-26T15:00:00+00:00"
SCOPE = {"target_id": "agent-1", "deployment_id": "test-configuration-1", "snapshot_sha256": "a" * 64}


def plan():
    return {"schema": PLAN_SCHEMA, "assessment_id": "import-assessment", "scope": deepcopy(SCOPE),
            "approval": {"reviewer": "scope-reviewer", "basis": "Reviewed historical evidence attribution.",
                         "approved_at": "2026-10-26T10:00:00+00:00"},
            "valid_from": "2026-10-26T09:00:00+00:00", "valid_until": "2026-10-27T10:00:00+00:00",
            "controls": [{"applicability": "applicable", "justification": "Reviewed original evidence scope."}]}


def source():
    return {"schema": "registered-case-evidence/v1", "job_id": "original-job", "agent_id": SCOPE["target_id"],
            "test_id": "grounded_policy", "case_id": "original-case", "version_pin": "tests@1", "seed": 0,
            "verdict": "pass", "responses": ["synthetic observed response"],
            "budget": {"case_delta": {"target_calls": 1}}, "execution_mode": "measured"}


def approved(native):
    raw = json.dumps(native).encode()
    selected = plan()
    eu = next(f for f in load_registry()["frameworks"] if f["framework_id"] == "eu-ai-act")
    selected["controls"][0].update(framework_id="eu-ai-act", control_id="ART9", mapping_version=eu["mapping_version"],
        required_tests=[{"test_id": "grounded_policy", "case_id": "original-case", "probe_set_version": "tests@1", "trials": 1}])
    selected["existing_evidence_bindings"] = [{
        **{key: native[key] for key in ("job_id", "agent_id", "test_id", "case_id", "version_pin", "seed")},
        "source_sha256": hashlib.sha256(raw).hexdigest(), "artifact_uri": "evidence://original/case.json",
        "trial_index": 0, "observed_at": "2026-10-26T09:30:00+00:00",
        "reviewer": "evidence-reviewer", "basis": "Reviewed original run identity, configuration and time record."}]
    return raw, selected


def evaluate(ref, selected, root):
    result = build_requirement_assessment({"assessment_id": selected["assessment_id"], "scope": selected["scope"],
                "evidence_refs": [ref], "documents": []}, plan=selected, evaluated_at=NOW, artifact_reader=directory_reader(root))
    eu = next(f for f in result["frameworks"] if f["framework_id"] == "eu-ai-act")
    return result, next(c for c in eu["controls"] if c["control_id"] == "ART9")


class RegisteredEvidenceImportTests(unittest.TestCase):
    def test_same_native_artifact_cannot_satisfy_two_trials(self):
        raw, selected = approved(source())
        selected["controls"][0]["required_tests"][0]["trials"] = 2
        duplicate = deepcopy(selected["existing_evidence_bindings"][0])
        duplicate["trial_index"] = 1
        selected["existing_evidence_bindings"].append(duplicate)
        with TemporaryDirectory() as directory, self.assertRaises(ValueError):
            import_registered_case(raw, plan=selected, binding_index=0, artifact_directory=Path(directory))

    def test_original_bytes_and_verdict_are_reused_with_reviewed_attribution(self):
        raw, selected = approved(source())
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ref = import_registered_case(raw, plan=selected, binding_index=0, artifact_directory=root)
            self.assertEqual(ref["verdict"], "pass")
            self.assertEqual((root / (hashlib.sha256(raw).hexdigest() + ".json")).read_bytes(), raw)
            result, row = evaluate(ref, selected, root)
            self.assertEqual(row["technical_status"], "passed")
            self.assertFalse(row["evidence_complete_for_plan"])
            self.assertEqual(row["requirement_conclusion"], "not assessed")

    def test_source_is_reverified_during_projection(self):
        raw, selected = approved(source())
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ref = import_registered_case(raw, plan=selected, binding_index=0, artifact_directory=root)
            (root / (hashlib.sha256(raw).hexdigest() + ".json")).write_bytes(b"substituted source")
            result, row = evaluate(ref, selected, root)
            self.assertEqual(row["technical_status"], "not evaluated")
            self.assertTrue(result["rejected_evidence"])

    def test_fixture_and_non_measured_native_rows_cannot_be_promoted(self):
        for update in [{"execution_mode": "offline_fixture"}, {"fixture": True},
                       {"budget": {"case_delta": {"target_calls": 0}}}, {"responses": []},
                       {"verdict": "unresolved"}]:
            with self.subTest(update=update), TemporaryDirectory() as directory:
                raw, selected = approved(source() | update)
                with self.assertRaises(ValueError):
                    import_registered_case(raw, plan=selected, binding_index=0, artifact_directory=Path(directory))

    def test_wrong_binding_and_missing_review_cannot_import(self):
        raw, selected = approved(source())
        for update in [{"job_id": "wrong"}, {"version_pin": "wrong"}, {"seed": 9}, {"reviewer": ""}]:
            with self.subTest(update=update), TemporaryDirectory() as directory:
                changed = deepcopy(selected)
                changed["existing_evidence_bindings"][0].update(update)
                with self.assertRaises(ValueError):
                    import_registered_case(raw, plan=changed, binding_index=0, artifact_directory=Path(directory))

    def test_failure_is_preserved_without_rejudging(self):
        raw, selected = approved(source() | {"verdict": "fail"})
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ref = import_registered_case(raw, plan=selected, binding_index=0, artifact_directory=root)
            _, row = evaluate(ref, selected, root)
            self.assertEqual(row["technical_status"], "failed")


if __name__ == "__main__":
    unittest.main()
