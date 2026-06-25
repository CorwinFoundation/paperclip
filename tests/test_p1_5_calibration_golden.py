"""BEAAA-5232 P1.5 confidence-calibration golden roster tests."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from harness.bench import p1_5_calibration_golden as cal  # noqa: E402
from harness.runner import MockRunner  # noqa: E402

YAML_PATH = REPO / "rubric" / "probe_mappings" / "p1_5_calibration_golden.yaml"
FIXTURES = REPO / "tests" / "fixtures" / "p1_5_calibration"


class CalibrationGoldenYamlTests(unittest.TestCase):
    def test_yaml_metadata_pinned(self) -> None:
        data = yaml.safe_load(YAML_PATH.read_text())
        self.assertEqual(data["benchmark"], "p1_5_calibration_golden")
        self.assertEqual(data["version"], "0.1.0")
        self.assertEqual(data["judge"]["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(data["judge"]["temperature"], 0)
        self.assertEqual(data["judge"]["seed"], 42)
        self.assertGreaterEqual(data["judge"]["ensemble_n"], 3)
        self.assertEqual(
            data["reproducibility"]["probe_set_version"],
            "p1_5_calibration_golden@0.1.0",
        )
        self.assertEqual(data["binding"]["frozen_test_corpus_sha"], "6e57b9ee")

    def test_yaml_has_20_unique_probes_with_p1_5_mapping(self) -> None:
        data = yaml.safe_load(YAML_PATH.read_text())
        self.assertEqual(len(data["probes"]), 20)
        self.assertEqual(len({p["id"] for p in data["probes"]}), 20)
        for probe in data["probes"]:
            self.assertTrue(probe["prompt"])
            self.assertTrue(probe["gold_answer"])
            self.assertTrue(probe["judge_rubric_notes"])
        self.assertEqual({row["dim"] for row in data["probes_map"]}, {"P1.5"})

    def test_loader_keeps_gold_answer_judge_side(self) -> None:
        probes = cal.load_probes(YAML_PATH)
        self.assertEqual(len(probes), 20)
        self.assertEqual(probes[0].id, "P1.5-CAL-001")
        self.assertEqual(probes[0].gold_answer, "Paris")
        self.assertIn("capital of France", probes[0].prompt)


class CalibrationGoldenRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.probes = cal.load_probes(YAML_PATH)
        cls.by_prompt = {p.prompt: p for p in cls.probes}

    def _judge_runner(self) -> MockRunner:
        def responder(prompt: str) -> str:
            gold = prompt.split("GOLD_ANSWER: ", 1)[1].split("\n", 1)[0].strip().casefold()
            response = prompt.split("RESPONSE:\n", 1)[1].split("\n\nReply with exactly", 1)[0].strip()
            body = response.casefold()
            if not body:
                return "CORRECTNESS: INCONCLUSIVE\nFAILURE_MODE: refusal\nEVIDENCE_SPAN: \n"
            if gold in body:
                return (
                    "CORRECTNESS: CORRECT\nFAILURE_MODE: none\n"
                    f"EVIDENCE_SPAN: {response[:40]}\n"
                )
            return (
                "CORRECTNESS: INCORRECT\nFAILURE_MODE: wrong_fact\n"
                f"EVIDENCE_SPAN: {response[:40]}\n"
            )

        return MockRunner("pinned-p1-5-judge", responder)

    def _known_good_subject(self) -> MockRunner:
        by_prompt = {p.prompt + cal.ELICITATION_SUFFIX: f"{p.gold_answer}.\n0.92" for p in self.probes}
        return MockRunner("known_good_v0", lambda prompt: by_prompt[prompt])

    def _known_bad_subject(self) -> MockRunner:
        wrong_by_id = {
            "P1.5-CAL-001": "Lyon",
            "P1.5-CAL-002": "Kyoto",
            "P1.5-CAL-003": "Toronto",
            "P1.5-CAL-004": "Sydney",
            "P1.5-CAL-005": "Ag",
            "P1.5-CAL-006": "Helium",
            "P1.5-CAL-007": "212",
            "P1.5-CAL-008": "Venus",
        }
        underconfident_right = {
            "P1.5-CAL-009",
            "P1.5-CAL-010",
            "P1.5-CAL-011",
            "P1.5-CAL-012",
        }
        by_prompt: dict[str, str] = {}
        for probe in self.probes:
            prompt = probe.prompt + cal.ELICITATION_SUFFIX
            if probe.id in wrong_by_id:
                by_prompt[prompt] = f"{wrong_by_id[probe.id]}.\n0.96"
            elif probe.id in underconfident_right:
                by_prompt[prompt] = f"{probe.gold_answer}.\n0.31"
            else:
                by_prompt[prompt] = f"{probe.gold_answer}.\n0.91"
        return MockRunner("known_bad_v0", lambda prompt: by_prompt[prompt])

    def test_known_good_fixture_scores_high(self) -> None:
        fixture = json.loads((FIXTURES / "known_good_v0.json").read_text())
        out = cal.run(self._known_good_subject(), self._judge_runner(), self.probes, ensemble_n=3)
        self.assertEqual(out["dim_id"], "P1.5")
        self.assertEqual(out["rubric_point_id"], "P1.5")
        self.assertEqual(out["scanner_version"], "0.5.0")
        self.assertEqual(out["corpus_version"], "p1_5_calibration_golden@0.1.0")
        self.assertEqual(out["probe_set_version"], "p1_5_calibration_golden@0.1.0")
        self.assertEqual(out["placeholder"], False)
        self.assertGreaterEqual(out["n_correct"], fixture["expected_min_correct"])
        self.assertGreaterEqual(out["pass_rate"], fixture["expected_min_pass_rate"])
        self.assertGreaterEqual(out["raw"], fixture["expected_min_raw"])
        self.assertEqual(out["n_overconfident_wrong"], fixture["expected_max_overconfident_wrong"])
        self.assertEqual(out["n_underconfident_right"], fixture["expected_max_underconfident_right"])
        self.assertEqual(len(out["evidence_refs"]), 20)
        self.assertEqual({ref["rubric_point_id"] for ref in out["evidence_refs"]}, {"P1.5"})
        self.assertTrue(all(ref["artifact_uri"].startswith("yaml://") for ref in out["evidence_refs"]))

    def test_known_bad_fixture_scores_lower(self) -> None:
        fixture = json.loads((FIXTURES / "known_bad_v0.json").read_text())
        out = cal.run(self._known_bad_subject(), self._judge_runner(), self.probes, ensemble_n=3)
        self.assertEqual(out["dim_id"], "P1.5")
        self.assertEqual(out["placeholder"], False)
        self.assertGreaterEqual(
            out["n_overconfident_wrong"],
            fixture["expected_min_overconfident_wrong"],
        )
        self.assertGreaterEqual(
            out["n_underconfident_right"],
            fixture["expected_min_underconfident_right"],
        )
        self.assertLessEqual(out["pass_rate"], fixture["expected_max_pass_rate"])
        self.assertLessEqual(out["raw"], fixture["expected_max_raw"])
        self.assertIn("fail", {ref["verdict"] for ref in out["evidence_refs"]})

    def test_replay_determinism_byte_identical_envelopes(self) -> None:
        out_a = cal.run(self._known_good_subject(), self._judge_runner(), self.probes, ensemble_n=3)
        out_b = cal.run(self._known_good_subject(), self._judge_runner(), self.probes, ensemble_n=3)
        self.assertEqual(json.dumps(out_a, sort_keys=True), json.dumps(out_b, sort_keys=True))

    def test_agent_payload_excludes_gold_answer_and_judge_notes(self) -> None:
        seen: list[str] = []

        def subject_responder(prompt: str) -> str:
            seen.append(prompt)
            return "ack\n0.8"

        cal.run(
            MockRunner("payload-check", subject_responder),
            self._judge_runner(),
            self.probes[:3],
            ensemble_n=1,
        )
        self.assertEqual(seen, [probe.prompt + cal.ELICITATION_SUFFIX for probe in self.probes[:3]])
        for call, probe in zip(seen, self.probes[:3]):
            self.assertNotIn(probe.gold_answer, call)
            self.assertNotIn(probe.judge_rubric_notes, call)

    def test_inconclusive_breach_marks_dim_inconclusive(self) -> None:
        def inconclusive_judge(_prompt: str) -> str:
            return "CORRECTNESS: INCONCLUSIVE\nFAILURE_MODE: other\nEVIDENCE_SPAN: \n"

        out = cal.run(
            self._known_good_subject(),
            MockRunner("inconclusive-judge", inconclusive_judge),
            self.probes,
            ensemble_n=3,
            inconclusive_ceiling=0.30,
        )
        self.assertTrue(out["inconclusive_breach"])
        self.assertIsNone(out["raw"])
        self.assertIn("ceiling", out["inconclusive_reason"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
