import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from harness.skill_inspection import inspect_skill


class AdvisoryInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.skill = Path(self.temp.name) / "skill"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text("A plain greeting skill", encoding="utf-8")

    def engine(self, report, callback=None):
        def run(argv, **kwargs):
            self.assertNotIn("ANTHROPIC_API_KEY", kwargs["env"])
            self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
            self.assertEqual(argv[1:3], ["-m", "harness.skill_inspection_worker"])
            if callback:
                callback()
            kwargs["stdout"].write(json.dumps(report).encode())
            return SimpleNamespace(returncode=1)
        return run

    @staticmethod
    def complete():
        return {"metadata": {"skillspector_version": "2.12.0", "llm_requested": False, "inference_usage": []},
                "execution_successful": True, "analysis_completeness": {"is_complete": True},
                "issues": [{"id": "P2", "severity": "HIGH"}],
                "risk_assessment": {"recommendation": "DO_NOT_INSTALL"}}

    def test_high_finding_is_unscored_evidence_and_never_admission(self):
        with patch("harness.skill_inspection.subprocess.run", self.engine(self.complete())):
            result = inspect_skill(self.skill)
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["review_required"])
        self.assertTrue(result["advisory_only"])
        self.assertIs(result["scored"], False)
        self.assertEqual(result["score_contribution"], 0)
        self.assertEqual(result["admission_effect"], "none")
        self.assertNotIn("are_total", result)
        self.assertNotIn("backbond_score", result)

    def test_incomplete_engine_cannot_be_clean(self):
        raw = self.complete()
        raw["analysis_completeness"]["is_complete"] = False
        raw["issues"] = []
        with patch("harness.skill_inspection.subprocess.run", self.engine(raw)):
            result = inspect_skill(self.skill)
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(result["review_required"])

    def test_missing_engine_cannot_be_clean(self):
        with patch("harness.skill_inspection.subprocess.run", return_value=SimpleNamespace(returncode=2)):
            result = inspect_skill(self.skill)
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(result["review_required"])

    def test_llm_execution_is_rejected(self):
        raw = self.complete()
        raw["metadata"]["llm_requested"] = True
        with patch("harness.skill_inspection.subprocess.run", self.engine(raw)):
            self.assertEqual(inspect_skill(self.skill)["status"], "unavailable")

    def test_added_source_file_invalidates_receipt(self):
        with patch("harness.skill_inspection.subprocess.run", self.engine(self.complete(),
                   lambda: (self.skill / "new.py").write_text("print('new')"))):
            result = inspect_skill(self.skill)
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("source_inventory_changed_during_inspection", result["limitations"])
