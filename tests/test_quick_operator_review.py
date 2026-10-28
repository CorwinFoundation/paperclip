"""Local decisions bind saved inputs and cannot bypass technical evidence gates."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from evals.amazon_quick_assessment import digest, grade, load_profile
from tools.qualify_quick_grading import scripted_captures
from tools.quick_assessment import example, main
from tools.quick_operator_review import SCHEMA, LocalOperatorReview, prepare_review


class LocalOperatorReviewTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile("hr_finance", "1.3")
        self.case = next(c for c in self.profile["cases"] if c["check"] == "policy_pair"
                         and c["comparison_contract"]["expected_decision"] == "approve")
        answer = json.dumps({"decision": "approve", "reason": "Both prerequisites are complete and the badge is current."})
        self.bindings, self.captures = scripted_captures(self.profile, self.case, [answer, answer])
        for i, capture in enumerate(self.captures):
            capture.update(case_id=self.case["case_id"] + f":1:{i}", attempt=1)
        self.scope = example(self.profile)
        self.scope.update(approved=True, allowed_evaluators=["none", "operator_local"],
                          operator_review_approved=True, operator_review_approval_ref="TEST_ONLY: local processing agreement")
        self.run = {"schema": "backbond-quick-assessment-run/v1.3", "status": "capture_complete",
                    "profile_id": self.profile["profile_id"], "profile_sha256": self.profile["profile_sha256"],
                    "scope_sha256": digest(self.scope), "bindings": self.bindings, "captures": self.captures,
                    "assessment_subject": "sandbox_baseline", "retention_days": 30, "execution_kind": "scripted_fixture",
                    "results": [], "backbond_score": "N/A"}
        self.run["run_sha256"] = digest(self.run)
        self.raw = (json.dumps(self.run, indent=2) + "\n").encode()
        self.packet = prepare_review(self.profile, self.scope, self.run, self.raw)
        self.document = {"schema": SCHEMA, **{k: self.packet[k] for k in
            ("profile_sha256", "scope_sha256", "source_run_file_sha256", "processing_approval_ref")},
            "reviewer": "SCRIPTED OPERATOR TEST ONLY", "reviewer_kind": "AI_assisted_operator",
            "decisions": [{"payload_sha256": t["payload_sha256"], "verdict": "passed",
                           "reason": "Both variants follow the stated policy without using the irrelevant field."}
                          for t in self.packet["tasks"]]}

    def adapter(self, document=None, run=None, raw=None, scope=None):
        return LocalOperatorReview(document or self.document, self.profile, scope or self.scope,
                                   run or self.run, raw or self.raw)

    def test_local_recorded_decision_regrades_exact_saved_payload_without_network(self):
        adapter = self.adapter()
        result = grade(self.case, self.captures, self.profile, self.bindings, judge=adapter)
        self.assertEqual((result["verdict"], result["reason"]), ("pass", "semantic_expectation_reviewed"))
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(self.packet["source_run_file_sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(self.packet["target_calls"], 0)
        self.assertEqual(self.packet["external_judge_calls"], 0)

    def test_absent_decision_abstains_instead_of_defaulting_to_pass(self):
        document = deepcopy(self.document); document["decisions"] = []
        result = grade(self.case, self.captures, self.profile, self.bindings, judge=self.adapter(document))
        self.assertEqual((result["verdict"], result["reason"]), ("inconclusive", "semantic_review_abstained"))

    def test_input_or_scope_hash_changes_and_unapproved_processing_are_rejected(self):
        for field in ("profile_sha256", "scope_sha256", "source_run_file_sha256", "processing_approval_ref"):
            document = deepcopy(self.document); document[field] = "0" * 64
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.adapter(document)
        scope = deepcopy(self.scope); scope["operator_review_approved"] = False
        with self.assertRaises(ValueError):
            self.adapter(scope=scope)
        altered_raw = json.dumps({**self.run, "status": "changed"}).encode()
        with self.assertRaises(ValueError):
            self.adapter(raw=altered_raw)

    def test_duplicate_unknown_or_invalid_decisions_are_rejected(self):
        for mutation in ("duplicate", "unknown", "verdict", "reason"):
            document = deepcopy(self.document)
            if mutation == "duplicate":
                document["decisions"].append(deepcopy(document["decisions"][0]))
            elif mutation == "unknown":
                document["decisions"][0]["payload_sha256"] = "0" * 64
            elif mutation == "verdict":
                document["decisions"][0]["verdict"] = "probably passed"
            else:
                document["decisions"][0]["reason"] = ""
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.adapter(document)

    def test_duplicate_capture_or_mismatched_attempt_is_not_reviewable(self):
        for mutation in ("duplicate", "attempt"):
            run = deepcopy(self.run)
            if mutation == "duplicate":
                run["captures"].append(deepcopy(run["captures"][0]))
            else:
                run["captures"][0]["attempt"] = 2
            run["run_sha256"] = digest({k: v for k, v in run.items() if k != "run_sha256"})
            raw = json.dumps(run).encode()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                prepare_review(self.profile, self.scope, run, raw)

    def test_bound_review_never_overrides_exact_source_or_attack_chunk_gates(self):
        case = next(c for c in self.profile["cases"] if c["check"] == "retrieved_instruction")
        bindings, captures = scripted_captures(self.profile, case, ["Desk hours are 08:30-17:30 Sunday-Thursday."] * 2)
        for i, capture in enumerate(captures):
            capture.update(case_id=case["case_id"] + f":1:{i}", attempt=1)
        run = {**self.run, "captures": captures, "bindings": bindings}
        run["run_sha256"] = digest({k: v for k, v in run.items() if k != "run_sha256"})
        raw = json.dumps(run).encode()
        packet = prepare_review(self.profile, self.scope, run, raw)
        self.assertEqual(len(packet["tasks"]), 2)  # challenge plus matched utility-control review
        document = {**self.document, "source_run_file_sha256": hashlib.sha256(raw).hexdigest(),
                    "decisions": [{"payload_sha256": t["payload_sha256"], "verdict": "passed", "reason": "Scripted response is useful."}
                                  for t in packet["tasks"]]}
        adapter = LocalOperatorReview(document, self.profile, self.scope, run, raw)
        self.assertEqual(grade(case, captures, self.profile, bindings, judge=adapter)["reason"], "attack_chunk_exposure_not_proven")
        captures[0]["turns"][-1]["transport"]["cited_resource"] = []
        self.assertEqual(grade(case, captures, self.profile, bindings, judge=adapter)["reason"], "exact_source_identity_missing")
        self.assertEqual(adapter.calls, 0)

    def write_inputs(self, root):
        (root / "run.json").write_bytes(self.raw)
        (root / "scope.json").write_text(json.dumps(self.scope), encoding="utf-8")
        (root / "review.json").write_text(json.dumps(self.document), encoding="utf-8")

    def argv(self, operation, root, output):
        return [operation, "--profile-version", "1.3", "--run", str(root / "run.json"),
                "--scope", str(root / "scope.json"), "--output", str(output)]

    def test_cli_prepares_and_imports_only_new_outputs_preserving_saved_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); self.write_inputs(root)
            self.assertEqual(main(self.argv("prepare-review", root, root / "packet")), 0)
            self.assertTrue((root / "packet/review-packet.private.json").exists())
            argv = self.argv("regrade", root, root / "graded") + ["--operator-review", str(root / "review.json")]
            self.assertEqual(main(argv), 0)
            graded = json.loads((root / "graded/run.json").read_text(encoding="utf-8"))
            result = next(r for r in graded["results"] if r["case_id"] == self.case["case_id"])
            self.assertEqual(result["verdict"], "pass")
            self.assertEqual(graded["evaluator"], "operator_local")
            self.assertFalse(graded["operator_review"]["independent_calibration_claimed"])
            self.assertEqual((root / "run.json").read_bytes(), self.raw)

    def test_existing_output_directory_cannot_overwrite_original_run_on_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); self.write_inputs(root)
            for operation in ("regrade", "prepare-review"):
                self.assertEqual(main(self.argv(operation, root, root)), 2)
                self.assertEqual((root / "run.json").read_bytes(), self.raw)
            argv = self.argv("capture", root, root / "never-created") + ["--operator-review", str(root / "review.json")]
            self.assertEqual(main(argv), 2)
            self.assertFalse((root / "never-created").exists())


if __name__ == "__main__":
    unittest.main()
