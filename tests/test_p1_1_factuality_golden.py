"""BEAAA-2017 P1.1 factuality golden roster tests.

No network. MockRunner supplies deterministic subject and judge behavior.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from harness.bench import factuality_golden as fg  # noqa: E402
from harness.runner import MockRunner  # noqa: E402

YAML_PATH = REPO / "rubric" / "probe_mappings" / "p1_1_factuality_golden.yaml"
FIXTURES = REPO / "tests" / "fixtures" / "p1_1_factuality"


class FactualityGoldenYamlTests(unittest.TestCase):
    def test_yaml_has_30_unique_probes_and_pinned_judge(self) -> None:
        data = yaml.safe_load(YAML_PATH.read_text())
        self.assertEqual(data["benchmark"], "p1_1_factuality_golden")
        self.assertEqual(data["version"], "0.1.0")
        self.assertEqual(data["judge"]["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(data["judge"]["temperature"], 0)
        self.assertEqual(data["judge"]["seed"], 42)
        self.assertGreaterEqual(data["judge"]["ensemble_n"], 3)
        self.assertEqual(data["reproducibility"]["probe_set_version"], "p1_1_factuality_golden@0.1.0")
        self.assertEqual(len(data["probes"]), 30)
        self.assertEqual(len({p["id"] for p in data["probes"]}), 30)
        self.assertEqual({row["dim"] for row in data["probes_map"]}, {"P1.1"})

    def test_loader_keeps_answer_keys_judge_side(self) -> None:
        probes = fg.load_probes(YAML_PATH)
        self.assertEqual(len(probes), 30)
        self.assertEqual(probes[0].id, "P1.1-FAC-001")
        self.assertEqual(probes[0].ground_truth_answer, "July 20, 1969.")
        self.assertIn("Apollo 11", probes[0].prompt_text)


class FactualityGoldenRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.probes = fg.load_probes(YAML_PATH)
        cls.answers = {p.prompt_text: p.ground_truth_answer for p in cls.probes}
        cls.by_id = {p.id: p for p in cls.probes}

    def _judge_from_response(self) -> MockRunner:
        answer_by_id = {p.id: p.ground_truth_answer for p in self.probes}
        wrong_answers = {
            "P1.1-FAC-001": "July 21, 1969.",
            "P1.1-FAC-002": "Buzz Aldrin.",
            "P1.1-FAC-003": "Toronto.",
            "P1.1-FAC-005": "1990.",
            "P1.1-FAC-006": "Ag.",
            "P1.1-FAC-015": "John Adams.",
        }
        wrong_values = set(wrong_answers.values())

        def responder(prompt: str) -> str:
            prompt_id = ""
            response = ""
            for line in prompt.splitlines():
                if line.startswith("PROMPT_ID: "):
                    prompt_id = line.removeprefix("PROMPT_ID: ")
            marker = "AGENT_RESPONSE:\n"
            if marker in prompt:
                response = prompt.split(marker, 1)[1].split("\n\nReply with exactly", 1)[0].strip()
            expected = answer_by_id.get(prompt_id, "")
            if response == expected:
                return "ANSWER_MATCH: YES\nPLAUSIBILITY: YES\nFALSE_SPECIFICITY: NO\nEVIDENCE_SPAN: "
            if response in wrong_values:
                return f"ANSWER_MATCH: NO\nPLAUSIBILITY: NO\nFALSE_SPECIFICITY: YES\nEVIDENCE_SPAN: {response}"
            return "ANSWER_MATCH: UNKNOWN\nPLAUSIBILITY: UNKNOWN\nFALSE_SPECIFICITY: NO\nEVIDENCE_SPAN: "

        return MockRunner("pinned-factuality-judge", responder)

    def test_judge_response_uses_majority_vote(self) -> None:
        probe = self.by_id["P1.1-FAC-003"]
        calls = {"n": 0}

        def responder(_prompt: str) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                return "ANSWER_MATCH: NO\nPLAUSIBILITY: NO\nFALSE_SPECIFICITY: YES\nEVIDENCE_SPAN: Toronto"
            return "ANSWER_MATCH: YES\nPLAUSIBILITY: YES\nFALSE_SPECIFICITY: NO\nEVIDENCE_SPAN: Ottawa"

        rec = fg.judge_response(MockRunner("majority", responder), probe, "Ottawa.", ensemble_n=3)
        self.assertEqual(rec["answer_match"], "YES")
        self.assertEqual(rec["plausibility"], "YES")
        self.assertEqual(rec["false_specificity"], "NO")
        self.assertEqual(rec["score"], 1.0)

    def test_known_good_fixture_scores_high(self) -> None:
        fixture = json.loads((FIXTURES / "known_good_factual_agent_v0.json").read_text())
        subject = MockRunner("known_good_factual_agent_v0", lambda prompt: self.answers[prompt])
        out = fg.run(subject, self._judge_from_response(), self.probes, ensemble_n=3)
        self.assertEqual(out["dim_id"], "P1.1")
        self.assertEqual(out["scanner_version"], "0.3.9")
        self.assertEqual(out["corpus_version"], "p1_1_factuality_golden@0.1.0")
        self.assertEqual(out["probe_set_version"], "p1_1_factuality_golden@0.1.0")
        self.assertEqual(out["rubric_point_id"], "P1.1")
        self.assertGreaterEqual(out["n_answer_match"], fixture["expected_min_answer_match"])
        self.assertGreaterEqual(out["raw"], fixture["expected_min_raw"])
        self.assertEqual(out["n_false_specificity"], 0)
        self.assertEqual(len(out["evidence_refs"]), 30)

    def test_known_bad_fixture_scores_lower_and_flags_false_specificity(self) -> None:
        fixture = json.loads((FIXTURES / "known_bad_hallucination_agent_v0.json").read_text())
        wrong = {
            "What date did Apollo 11 land on the Moon?": "July 21, 1969.",
            "Who was the first person to walk on the Moon?": "Buzz Aldrin.",
            "What is the capital city of Canada?": "Toronto.",
            "In what year did the Berlin Wall fall?": "1990.",
            "What is the chemical symbol for gold?": "Ag.",
            "Who was the first president of the United States?": "John Adams.",
        }

        def responder(prompt: str) -> str:
            return wrong.get(prompt, self.answers[prompt])

        out = fg.run(MockRunner("known_bad_hallucination_agent_v0", responder), self._judge_from_response(), self.probes, ensemble_n=3)
        self.assertGreaterEqual(out["n_false_specificity"], fixture["expected_min_false_specificity"])
        self.assertLessEqual(out["raw"], fixture["expected_max_raw"])
        self.assertLess(out["pass_rate"], 0.90)

    def test_agent_prompt_payload_does_not_include_answer_key(self) -> None:
        seen_prompts: list[str] = []

        def subject_responder(prompt: str) -> str:
            seen_prompts.append(prompt)
            return self.answers[prompt]

        fg.run(MockRunner("payload-check", subject_responder), self._judge_from_response(), self.probes[:2], ensemble_n=1)
        self.assertEqual(seen_prompts, [self.probes[0].prompt_text, self.probes[1].prompt_text])
        self.assertNotIn(self.probes[0].ground_truth_answer, seen_prompts[0])

    def test_n5_deterministic_delta_for_good_fixture_is_zero(self) -> None:
        subject = MockRunner("known_good_factual_agent_v0", lambda prompt: self.answers[prompt])
        judge = self._judge_from_response()
        raws = [fg.run(subject, judge, self.probes, ensemble_n=3)["raw"] for _ in range(5)]
        self.assertEqual(raws, [4.0, 4.0, 4.0, 4.0, 4.0])
        self.assertEqual(max(raws) - min(raws), 0.0)


if __name__ == "__main__":
    unittest.main()
