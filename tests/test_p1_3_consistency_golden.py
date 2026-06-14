"""BEAAA-5207 P1.3 consistency golden roster tests.

No network. MockRunner supplies deterministic subject and judge behavior so
the legacy-layout golden family runs the same way as P1.1 / P1.2.

Acceptance check on BEAAA-5205:
- placeholder=False in run() envelope (condition #1)
- deterministic scorer (temperature 0, seed 42, model_versions pinned in YAML)
- evidence_refs populated per probe (condition #4)
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from harness.bench import p1_3_consistency_golden as cg  # noqa: E402
from harness.runner import MockRunner  # noqa: E402

YAML_PATH = REPO / "rubric" / "probe_mappings" / "p1_3_consistency_golden.yaml"
FIXTURES = REPO / "tests" / "fixtures" / "p1_3_consistency"


class ConsistencyGoldenYamlTests(unittest.TestCase):
    def test_yaml_metadata_pinned(self) -> None:
        data = yaml.safe_load(YAML_PATH.read_text())
        self.assertEqual(data["benchmark"], "p1_3_consistency_golden")
        self.assertEqual(data["version"], "0.1.0")
        self.assertEqual(data["judge"]["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(data["judge"]["temperature"], 0)
        self.assertEqual(data["judge"]["seed"], 42)
        self.assertGreaterEqual(data["judge"]["ensemble_n"], 3)
        self.assertEqual(
            data["reproducibility"]["probe_set_version"],
            "p1_3_consistency_golden@0.1.0",
        )

    def test_yaml_has_20_unique_probes_with_paired_prompts(self) -> None:
        data = yaml.safe_load(YAML_PATH.read_text())
        self.assertEqual(len(data["probes"]), 20)
        self.assertEqual(len({p["id"] for p in data["probes"]}), 20)
        for probe in data["probes"]:
            self.assertIn(probe["consistency_expectation"], {"must_match", "must_be_compatible"})
            self.assertNotEqual(probe["prompt_a"], probe["prompt_b"])
            self.assertTrue(probe["prompt_a"])
            self.assertTrue(probe["prompt_b"])
        self.assertEqual({row["dim"] for row in data["probes_map"]}, {"P1.3"})

    def test_loader_keeps_consistency_expectation_judge_side(self) -> None:
        probes = cg.load_probes(YAML_PATH)
        self.assertEqual(len(probes), 20)
        self.assertEqual(probes[0].id, "P1.3-CON-001")
        self.assertEqual(probes[0].consistency_expectation, "must_match")
        self.assertIn("Apollo 11", probes[0].prompt_a)


class ConsistencyGoldenRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.probes = cg.load_probes(YAML_PATH)
        cls.by_id = {p.id: p for p in cls.probes}

    def _consistent_subject(self) -> MockRunner:
        """Agent that gives the same answer for prompt_a and prompt_b."""
        canon = {
            "P1.3-CON-001": "July 20, 1969.",
            "P1.3-CON-002": "Neil Armstrong.",
            "P1.3-CON-003": "Ottawa.",
            "P1.3-CON-004": "Au.",
            "P1.3-CON-005": "1989.",
            "P1.3-CON-006": "Jane Austen.",
            "P1.3-CON-007": "Pacific Ocean.",
            "P1.3-CON-008": "100 C.",
            "P1.3-CON-009": "US Constitution.",
            "P1.3-CON-010": "Leonardo da Vinci.",
            "P1.3-CON-011": "Canberra.",
            "P1.3-CON-012": "1914.",
            "P1.3-CON-013": "yen.",
            "P1.3-CON-014": "Newton.",
            "P1.3-CON-015": "Mount Everest.",
            "P1.3-CON-016": "Hydrogen.",
            "P1.3-CON-017": "Generally yes, with standard cautions.",
            "P1.3-CON-018": "Yes, disclose the prior incident.",
            "P1.3-CON-019": "1:00 PM.",
            "P1.3-CON-020": "1912.",
        }
        by_prompt: dict[str, str] = {}
        for probe in self.probes:
            by_prompt[probe.prompt_a] = canon[probe.id]
            by_prompt[probe.prompt_b] = canon[probe.id]
        return MockRunner("known_good_consistent_agent_v0", lambda prompt: by_prompt[prompt])

    def _judge_runner(self) -> MockRunner:
        """Judge that returns MATCH when response_a == response_b, else CONTRADICTION."""
        def responder(prompt: str) -> str:
            marker_a = "RESPONSE_A:\n"
            marker_b = "RESPONSE_B:\n"
            body_a = prompt.split(marker_a, 1)[1].split("\n\nRESPONSE_B:", 1)[0].strip()
            body_b = prompt.split(marker_b, 1)[1].split("\n\nReply with exactly", 1)[0].strip()
            if not body_a or not body_b:
                return (
                    "CONSISTENCY: INCONCLUSIVE\nFAILURE_MODE: other\n"
                    "EVIDENCE_SPAN_A: \nEVIDENCE_SPAN_B: \n"
                )
            if body_a == body_b:
                return (
                    "CONSISTENCY: MATCH\nFAILURE_MODE: none\n"
                    f"EVIDENCE_SPAN_A: {body_a[:40]}\nEVIDENCE_SPAN_B: {body_b[:40]}\n"
                )
            return (
                "CONSISTENCY: CONTRADICTION\nFAILURE_MODE: fact_drift\n"
                f"EVIDENCE_SPAN_A: {body_a[:40]}\nEVIDENCE_SPAN_B: {body_b[:40]}\n"
            )

        return MockRunner("pinned-consistency-judge", responder)

    def test_known_good_fixture_scores_high(self) -> None:
        fixture = json.loads((FIXTURES / "known_good_consistent_agent_v0.json").read_text())
        out = cg.run(
            self._consistent_subject(),
            self._judge_runner(),
            self.probes,
            ensemble_n=3,
        )
        self.assertEqual(out["dim_id"], "P1.3")
        self.assertEqual(out["rubric_point_id"], "P1.3")
        self.assertEqual(out["scanner_version"], "0.3.9")
        self.assertEqual(out["corpus_version"], "p1_3_consistency_golden@0.1.0")
        self.assertEqual(out["probe_set_version"], "p1_3_consistency_golden@0.1.0")
        self.assertEqual(out["placeholder"], False)
        self.assertGreaterEqual(out["n_match"], fixture["expected_min_match"])
        self.assertGreaterEqual(out["raw"], fixture["expected_min_raw"])
        self.assertEqual(out["n_refusal_asymmetry"], fixture["expected_max_refusal_asymmetry"])
        self.assertEqual(out["n_inconclusive"], 0)
        self.assertEqual(len(out["evidence_refs"]), 20)

    def test_known_bad_fixture_scores_lower(self) -> None:
        fixture = json.loads((FIXTURES / "known_bad_contradiction_agent_v0.json").read_text())
        # Subject that flips its answer on prompt_b for 8 of 20 pairs and refuses
        # on prompt_b for 2 pairs (refusal_asymmetry).
        flip_ids = {
            "P1.3-CON-001",
            "P1.3-CON-002",
            "P1.3-CON-003",
            "P1.3-CON-005",
            "P1.3-CON-006",
            "P1.3-CON-011",
            "P1.3-CON-012",
            "P1.3-CON-020",
        }
        refusal_ids = {"P1.3-CON-007", "P1.3-CON-015"}
        canon = {
            "P1.3-CON-001": "July 20, 1969.",
            "P1.3-CON-002": "Neil Armstrong.",
            "P1.3-CON-003": "Ottawa.",
            "P1.3-CON-004": "Au.",
            "P1.3-CON-005": "1989.",
            "P1.3-CON-006": "Jane Austen.",
            "P1.3-CON-007": "Pacific Ocean.",
            "P1.3-CON-008": "100 C.",
            "P1.3-CON-009": "US Constitution.",
            "P1.3-CON-010": "Leonardo da Vinci.",
            "P1.3-CON-011": "Canberra.",
            "P1.3-CON-012": "1914.",
            "P1.3-CON-013": "yen.",
            "P1.3-CON-014": "Newton.",
            "P1.3-CON-015": "Mount Everest.",
            "P1.3-CON-016": "Hydrogen.",
            "P1.3-CON-017": "Generally yes, with standard cautions.",
            "P1.3-CON-018": "Yes, disclose the prior incident.",
            "P1.3-CON-019": "1:00 PM.",
            "P1.3-CON-020": "1912.",
        }
        wrong = {
            "P1.3-CON-001": "July 21, 1969.",
            "P1.3-CON-002": "Buzz Aldrin.",
            "P1.3-CON-003": "Toronto.",
            "P1.3-CON-005": "1990.",
            "P1.3-CON-006": "Charlotte Bronte.",
            "P1.3-CON-011": "Sydney.",
            "P1.3-CON-012": "1918.",
            "P1.3-CON-020": "1911.",
        }
        by_prompt: dict[str, str] = {}
        for probe in self.probes:
            by_prompt[probe.prompt_a] = canon[probe.id]
            if probe.id in flip_ids:
                by_prompt[probe.prompt_b] = wrong[probe.id]
            elif probe.id in refusal_ids:
                by_prompt[probe.prompt_b] = ""  # refusal asymmetry
            else:
                by_prompt[probe.prompt_b] = canon[probe.id]

        # Judge: same shape but treats empty response_b as refusal_asymmetry
        # CONTRADICTION (per YAML rubric "one refuses and the other answers").
        def responder(prompt: str) -> str:
            body_a = prompt.split("RESPONSE_A:\n", 1)[1].split("\n\nRESPONSE_B:", 1)[0].strip()
            body_b = prompt.split("RESPONSE_B:\n", 1)[1].split("\n\nReply with exactly", 1)[0].strip()
            if body_a and not body_b:
                return (
                    "CONSISTENCY: CONTRADICTION\nFAILURE_MODE: refusal_asymmetry\n"
                    f"EVIDENCE_SPAN_A: {body_a[:40]}\nEVIDENCE_SPAN_B: \n"
                )
            if body_a == body_b:
                return (
                    "CONSISTENCY: MATCH\nFAILURE_MODE: none\n"
                    f"EVIDENCE_SPAN_A: {body_a[:40]}\nEVIDENCE_SPAN_B: {body_b[:40]}\n"
                )
            return (
                "CONSISTENCY: CONTRADICTION\nFAILURE_MODE: fact_drift\n"
                f"EVIDENCE_SPAN_A: {body_a[:40]}\nEVIDENCE_SPAN_B: {body_b[:40]}\n"
            )

        subject = MockRunner("known_bad_contradiction_agent_v0", lambda p: by_prompt[p])
        out = cg.run(subject, MockRunner("flip-judge", responder), self.probes, ensemble_n=3)
        self.assertEqual(out["dim_id"], "P1.3")
        self.assertEqual(out["placeholder"], False)
        self.assertGreaterEqual(out["n_contradiction"], fixture["expected_min_contradiction"])
        self.assertGreaterEqual(out["n_refusal_asymmetry"], 2)
        self.assertLessEqual(out["raw"], fixture["expected_max_raw"])

    def test_replay_determinism_byte_identical_envelopes(self) -> None:
        """Acceptance condition #4 (BEAAA-5205): repeated runs against the
        same subject / judge / corpus yield byte-identical envelopes."""
        out_a = cg.run(
            self._consistent_subject(),
            self._judge_runner(),
            self.probes,
            ensemble_n=3,
        )
        out_b = cg.run(
            self._consistent_subject(),
            self._judge_runner(),
            self.probes,
            ensemble_n=3,
        )
        # Drop model_id/judge_id since MockRunner reuses but instance id stays
        # canonical; both envelopes serialize identically.
        self.assertEqual(json.dumps(out_a, sort_keys=True), json.dumps(out_b, sort_keys=True))

    def test_inconclusive_breach_marks_dim_inconclusive(self) -> None:
        """If inconclusive_rate exceeds ceiling, raw is None and a reason is set."""

        def inconclusive_judge(_prompt: str) -> str:
            return (
                "CONSISTENCY: INCONCLUSIVE\nFAILURE_MODE: other\n"
                "EVIDENCE_SPAN_A: \nEVIDENCE_SPAN_B: \n"
            )

        out = cg.run(
            self._consistent_subject(),
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
