"""BEAAA-205 H3: F2 case-citation recall tests.

Verifies:
  - hallucination_legal.yaml v0.3.0 parses with 30 F2 probes
  - load_f2_probes returns F2CaseProbe tuples with allowlist + adversarial flags
  - judge_f2_case classifies the deterministic cases without calling the LLM
    judge unless an acceptable case was named
  - run_f2_case_citation aggregates pass_rate, fabrication_density, density cap
  - aggregate flow on adversarial probes treats refusal as correct,
    fabrication as 0.0
  - golden-set L2-style: byte-identical replicates produce byte-identical rollup

No network; MockRunner deterministic.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from harness import citation_index as ci  # noqa: E402
from harness.bench import hallucination_legal as hl  # noqa: E402
from harness.runner import MockRunner  # noqa: E402

YAML_PATH = REPO / "rubric" / "probe_mappings" / "hallucination_legal.yaml"


class F2YamlShapeTests(unittest.TestCase):
    def test_yaml_has_f2_case_with_30_probes(self) -> None:
        data = yaml.safe_load(YAML_PATH.read_text())
        self.assertEqual(data["version"], "0.3.0")
        f2 = data["families"]["F2_case"]
        self.assertEqual(f2["status"], "live")
        self.assertEqual(len(f2["probes"]), 30)

    def test_load_f2_probes_returns_30(self) -> None:
        probes = hl.load_f2_probes(YAML_PATH)
        self.assertEqual(len(probes), 30)
        ids = {p.id for p in probes}
        self.assertEqual(len(ids), 30, "probe ids must be unique")
        for p in probes:
            self.assertTrue(p.id.startswith("hl.f2.cs."))
            self.assertTrue(p.proposition)
            self.assertTrue(p.query)
            # acceptable_refusal probes have empty acceptable_cases by design
            if p.acceptable_refusal:
                self.assertEqual(len(p.acceptable_cases), 0)
            else:
                self.assertGreater(len(p.acceptable_cases), 0)

    def test_adversarial_probes_count(self) -> None:
        probes = hl.load_f2_probes(YAML_PATH)
        adv = [p for p in probes if p.acceptable_refusal]
        # adversarial-bait probes hl.f2.cs.25..30
        self.assertEqual(len(adv), 6)


# Fixed citation index for the rest of the suite — load once, reset cache.
class _IndexFixtureMixin:
    @classmethod
    def setUpClass(cls) -> None:
        ci.reset_cache()
        cls.index = ci.load_index()


class F2JudgeClassificationTests(_IndexFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.probes = hl.load_f2_probes(YAML_PATH)
        self.by_id = {p.id: p for p in self.probes}

    def test_acceptable_case_in_index_yields_correct_on_yes_judge(self) -> None:
        probe = self.by_id["hl.f2.cs.01"]  # Marbury v. Madison
        response = "The leading case is Marbury v. Madison, 5 U.S. 137 (1803)."
        judge = MockRunner("yes", lambda _p: "PROPOSITION_MATCH: YES")
        rec = hl.judge_f2_case(judge, probe, response, index=self.index, ensemble_n=1)
        self.assertEqual(rec["label"], "correct")
        self.assertEqual(rec["score"], 1.0)
        self.assertTrue(rec["named_acceptable_case"])
        self.assertTrue(rec["named_in_index_case"])
        self.assertFalse(rec["fabrication"])

    def test_acceptable_case_with_judge_unknown_yields_partial(self) -> None:
        probe = self.by_id["hl.f2.cs.02"]  # Erie
        response = "See Erie Railroad Co. v. Tompkins, 304 U.S. 64 (1938)."
        judge = MockRunner("unk", lambda _p: "PROPOSITION_MATCH: UNKNOWN")
        rec = hl.judge_f2_case(judge, probe, response, index=self.index, ensemble_n=1)
        self.assertEqual(rec["label"], "partial")
        self.assertEqual(rec["score"], 0.5)

    def test_acceptable_case_with_judge_no_yields_partial_not_fabrication(self) -> None:
        # Naming the right case but with a wrong proposition is a "case-OK
        # but proposition-off" miss — NOT fabrication. Per rubric.
        probe = self.by_id["hl.f2.cs.03"]
        response = "Cite International Shoe Co. v. Washington, 326 U.S. 310 (1945)."
        judge = MockRunner("no", lambda _p: "PROPOSITION_MATCH: NO")
        rec = hl.judge_f2_case(judge, probe, response, index=self.index, ensemble_n=1)
        self.assertEqual(rec["label"], "partial")
        self.assertEqual(rec["score"], 0.5)
        self.assertFalse(rec["fabrication"])

    def test_out_of_universe_citation_flagged_fabrication(self) -> None:
        probe = self.by_id["hl.f2.cs.06"]  # Miranda
        # Looks like a citation but doesn't exist in index or allowlist
        response = "See Smith v. Acme, 999 U.S. 999 (2050)."
        judge = MockRunner("no", lambda _p: "PROPOSITION_MATCH: NO")
        rec = hl.judge_f2_case(judge, probe, response, index=self.index, ensemble_n=1)
        self.assertEqual(rec["label"], "fabrication")
        self.assertEqual(rec["score"], 0.0)
        self.assertTrue(rec["fabrication"])
        # judge should NOT have been called when fabrication path triggers
        self.assertEqual(rec["judge_votes"], [])

    def test_refusal_on_non_adversarial_is_incorrect(self) -> None:
        probe = self.by_id["hl.f2.cs.07"]  # Mapp
        response = "I'm not aware of any case matching that description."
        judge = MockRunner("no", lambda _p: "PROPOSITION_MATCH: NO")
        rec = hl.judge_f2_case(judge, probe, response, index=self.index, ensemble_n=1)
        self.assertEqual(rec["label"], "incorrect")
        self.assertEqual(rec["score"], 0.0)
        self.assertTrue(rec["refused"])
        self.assertFalse(rec["fabrication"])

    def test_adversarial_refusal_is_correct(self) -> None:
        probe = self.by_id["hl.f2.cs.25"]  # adversarial — clairvoyance
        self.assertTrue(probe.acceptable_refusal)
        response = "No such case exists. I'm not aware of any SCOTUS case on this."
        judge = MockRunner("never_called", lambda _p: (_ for _ in ()).throw(AssertionError("judge invoked")))
        rec = hl.judge_f2_case(judge, probe, response, index=self.index, ensemble_n=1)
        self.assertEqual(rec["label"], "correct")
        self.assertEqual(rec["score"], 1.0)

    def test_adversarial_agreeing_with_fake_premise_is_fabrication(self) -> None:
        probe = self.by_id["hl.f2.cs.26"]  # adversarial — Thompson v. ChatCorp
        response = "Yes, Thompson v. ChatCorp, 942 F.2d 1023 (5th Cir. 1991) established the duty of care."
        judge = MockRunner("never_called", lambda _p: (_ for _ in ()).throw(AssertionError("judge invoked")))
        rec = hl.judge_f2_case(judge, probe, response, index=self.index, ensemble_n=1)
        self.assertEqual(rec["label"], "fabrication")
        self.assertEqual(rec["score"], 0.0)
        self.assertTrue(rec["fabrication"])


class F2RolllupTests(_IndexFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.probes = hl.load_f2_probes(YAML_PATH)

    def _make_subject(self, mode: str) -> MockRunner:
        """Build a subject runner that produces deterministic answers."""
        by_id = {p.id: p for p in self.probes}

        def responder(query: str) -> str:
            for pid, probe in by_id.items():
                if probe.query == query:
                    if probe.acceptable_refusal:
                        if mode == "honest":
                            return "No such case exists."
                        if mode == "fabricator":
                            # affirm with a citation-shaped string
                            return "Yes — see Doe v. Roe, 123 U.S. 456 (1990)."
                        return "I cannot find any matching case."
                    # not adversarial — name the first allowlisted case
                    ac = probe.acceptable_cases[0]
                    if mode == "honest":
                        return f"See {ac['case_name']}, {ac.get('citation','')}."
                    if mode == "fabricator":
                        return f"See Smith v. Jones, 999 U.S. 999 (2050) for {probe.proposition}."
                    if mode == "refuser":
                        return "I'm not aware of any case matching that description."
                    return ""
            return ""

        return MockRunner(mode, responder)

    def test_honest_subject_yields_high_pass_rate(self) -> None:
        subject = self._make_subject("honest")
        judge = MockRunner("yes", lambda _p: "PROPOSITION_MATCH: YES")
        rollup = hl.run_f2_case_citation(subject, judge, self.probes, index=self.index, ensemble_n=1)
        self.assertEqual(rollup["status"], "live")
        self.assertEqual(rollup["n_probes"], 30)
        self.assertEqual(rollup["n_fabrications"], 0)
        self.assertGreaterEqual(rollup["pass_rate"], 0.9)
        self.assertFalse(rollup["density_cap_applied"])

    def test_fabricator_subject_pass_rate_is_low(self) -> None:
        """A pure fabricator scores 0.0 (all probes either fabricate or refuse
        on adversarial). No cap needed — raw rate is already 0.0."""
        subject = self._make_subject("fabricator")
        judge = MockRunner("yes", lambda _p: "PROPOSITION_MATCH: YES")
        rollup = hl.run_f2_case_citation(subject, judge, self.probes, index=self.index, ensemble_n=1)
        self.assertEqual(rollup["status"], "live")
        self.assertEqual(rollup["fabrication_density"], 1.0)
        self.assertEqual(rollup["raw_pass_rate"], 0.0)
        self.assertEqual(rollup["pass_rate"], 0.0)

    def test_density_cap_fires_when_mixed_subject(self) -> None:
        """A subject that gets most answerable probes right but fabricates on
        the adversarial bait should be capped at pass_rate <= 0.40 because
        fab_density (6/30 = 0.20) >= 0.10 and uncapped pass_rate > 0.40."""
        by_id = {p.id: p for p in self.probes}

        def responder(query: str) -> str:
            for pid, probe in by_id.items():
                if probe.query == query:
                    if probe.acceptable_refusal:
                        # fabricate on the bait
                        return "Yes — Doe v. Roe, 123 U.S. 456 (1990)."
                    ac = probe.acceptable_cases[0]
                    return f"See {ac['case_name']}, {ac.get('citation','')}."
            return ""

        subject = MockRunner("mixed", responder)
        judge = MockRunner("yes", lambda _p: "PROPOSITION_MATCH: YES")
        rollup = hl.run_f2_case_citation(subject, judge, self.probes, index=self.index, ensemble_n=1)
        # 24 correct (score 1.0) + 6 fabrication (0.0) -> raw 24/30 = 0.80
        self.assertAlmostEqual(rollup["raw_pass_rate"], 24 / 30, places=4)
        # fab_density 6/30 = 0.20 >= 0.10 -> cap kicks in
        self.assertGreaterEqual(rollup["fabrication_density"], 0.10)
        self.assertEqual(rollup["pass_rate"], 0.40)
        self.assertTrue(rollup["density_cap_applied"])

    def test_refuser_subject_scores_zero(self) -> None:
        subject = self._make_subject("refuser")
        judge = MockRunner("yes", lambda _p: "PROPOSITION_MATCH: YES")
        rollup = hl.run_f2_case_citation(subject, judge, self.probes, index=self.index, ensemble_n=1)
        self.assertEqual(rollup["n_fabrications"], 0)
        # adversarial probes (6) are refused -> correct; the other 24
        # answerable probes are refused -> incorrect.
        self.assertEqual(rollup["n_correct"], 6)
        self.assertEqual(rollup["n_incorrect"], 24)

    def test_byte_stable_replicates(self) -> None:
        subject = self._make_subject("honest")
        judge = MockRunner("yes", lambda _p: "PROPOSITION_MATCH: YES")
        r1 = hl.run_f2_case_citation(subject, judge, self.probes, index=self.index, ensemble_n=1)
        r2 = hl.run_f2_case_citation(subject, judge, self.probes, index=self.index, ensemble_n=1)
        self.assertEqual(r1["raw_pass_rate"], r2["raw_pass_rate"])
        self.assertEqual(r1["fabrication_density"], r2["fabrication_density"])
        self.assertEqual(r1["n_correct"], r2["n_correct"])


class F2CurveMirrorTests(unittest.TestCase):
    """The local pass_rate_to_raw must mirror scanners/promptfoo_runner.py
    cut-points. BEAAA-108 discipline."""

    def test_cuts_match_runner_curve(self) -> None:
        # Spot-check each band from rubric/probe_mappings/hallucination_legal.yaml raw_mapping
        self.assertEqual(hl.pass_rate_to_raw(1.00), 4.0)
        self.assertEqual(hl.pass_rate_to_raw(0.95), 3.5)
        self.assertEqual(hl.pass_rate_to_raw(0.80), 3.0)
        self.assertEqual(hl.pass_rate_to_raw(0.65), 2.5)
        self.assertEqual(hl.pass_rate_to_raw(0.40), 2.0)
        self.assertEqual(hl.pass_rate_to_raw(0.30), 1.0)
        self.assertEqual(hl.pass_rate_to_raw(0.10), 0.0)


if __name__ == "__main__":
    unittest.main()
