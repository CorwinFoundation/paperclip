"""BEAAA-203 Tier A: p2_paired_attr runner tests.

Covers all three modes — parity (P2.1), equal_opportunity (P2.2),
sentiment (P2.5):

  - load_pairs: same 60-pair contract as counterfactual_pairs.
  - load_eo_labeled_subset: filters to UW-signed scenario labels.
  - judge parsing & ensemble majority on bool / float.
  - sig-test floor (minimum_n) emits `inconclusive` for under-n cells.
  - aggregate math: parity gap, TPR gap, sentiment delta.
  - run() end-to-end on MockRunner subject + judge for all three modes.

All tests use deterministic MockRunner; no LLM calls. Aggregation math is
locked here so byte-stable golden-set gate behavior holds without API spend.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.bench import p2_paired_attr as pa  # noqa: E402
from harness.runner import MockRunner  # noqa: E402

CORPUS_YAML = REPO / "rubric" / "probe_mappings" / "counterfactual_stability.yaml"
EO_YAML = REPO / "rubric" / "probe_mappings" / "equal_opportunity.yaml"


class LoadPairsTests(unittest.TestCase):
    def test_loads_v011_60_pair_corpus(self):
        pairs = pa.load_pairs(CORPUS_YAML)
        self.assertEqual(len(pairs), 60)

    def test_eo_labeled_subset_filters_to_signed_scenarios(self):
        all_pairs = pa.load_pairs(CORPUS_YAML)
        labeled = pa.load_eo_labeled_subset(all_pairs, EO_YAML)
        # Every labeled pair must carry an expected_outcome.
        self.assertTrue(labeled)
        for p in labeled:
            self.assertIsNotNone(p.expected_outcome)
            self.assertGreater(len(p.expected_outcome or ""), 0)
        # Subset is a strict subset of all_pairs.
        self.assertLess(len(labeled), len(all_pairs))


class JudgeParseTests(unittest.TestCase):
    def test_parity_judge_majority_bool(self):
        # Two True votes, one False → majority True.
        judge = MockRunner("test", lambda p: next(_parity_seq))
        global _parity_seq
        _parity_seq = iter([
            '{"routed_to_specialist": true, "urgent_flag": true, '
            '"free_consult_offered": false, "escalation_offered": true}',
            '{"routed_to_specialist": true, "urgent_flag": false, '
            '"free_consult_offered": false, "escalation_offered": true}',
            '{"routed_to_specialist": false, "urgent_flag": true, '
            '"free_consult_offered": false, "escalation_offered": true}',
        ])
        outcomes = pa._judge_parity(judge, "anything", ensemble_n=3)
        self.assertTrue(outcomes["routed_to_specialist"])  # 2T/1F
        self.assertTrue(outcomes["urgent_flag"])  # 2T/1F
        self.assertFalse(outcomes["free_consult_offered"])  # 3F
        self.assertTrue(outcomes["escalation_offered"])  # 3T

    def test_parity_judge_tie_returns_none(self):
        global _parity_seq
        _parity_seq = iter([
            '{"routed_to_specialist": true, "urgent_flag": true, '
            '"free_consult_offered": true, "escalation_offered": true}',
            '{"routed_to_specialist": false, "urgent_flag": false, '
            '"free_consult_offered": false, "escalation_offered": false}',
        ])
        judge = MockRunner("test", lambda p: next(_parity_seq))
        outcomes = pa._judge_parity(judge, "anything", ensemble_n=2)
        self.assertIsNone(outcomes["routed_to_specialist"])

    def test_sentiment_judge_mean_clamps_range(self):
        global _sent_seq
        _sent_seq = iter([
            '{"warmth": 0.8, "urgency": 0.3, "autonomy_respect": 1.4}',
            '{"warmth": 0.6, "urgency": 0.5, "autonomy_respect": 0.8}',
            '{"warmth": 0.7, "urgency": 0.4, "autonomy_respect": 0.6}',
        ])
        judge = MockRunner("test", lambda p: next(_sent_seq))
        s = pa._judge_sentiment(judge, "anything", ensemble_n=3)
        self.assertAlmostEqual(s["warmth"], 0.7)
        # autonomy_respect 1.4 clamped to 1.0 → mean of (1.0, 0.8, 0.6) = 0.8
        self.assertAlmostEqual(s["autonomy_respect"], 0.8)


class AggregateParityTests(unittest.TestCase):
    def _per_pair(self, n: int, *, axis: str, gap: float = 0.0) -> list[dict]:
        """Synthesize n pairs on `axis` with a controlled per-outcome gap.

        Group A always True; Group B True at rate (1 - gap).
        """
        out = []
        b_true = round(n * (1 - gap))
        for i in range(n):
            out.append({
                "id": f"p.{i:02d}", "axis": axis, "scenario_id": "x",
                "outcomes_a": {o: True for o in pa._PARITY_OUTCOMES},
                "outcomes_b": {o: i < b_true for o in pa._PARITY_OUTCOMES},
            })
        return out

    def test_inconclusive_when_pooled_n_below_floor(self):
        # 12-pair axis: well below default minimum_n=30.
        per_pair = self._per_pair(12, axis="age_signal", gap=0.0)
        agg = pa._aggregate_parity(per_pair, minimum_n=30)
        self.assertEqual(agg["status"], "inconclusive")
        self.assertIsNone(agg["raw"])

    def test_pooled_gap_maps_to_raw_via_cuts(self):
        # 5 axes × 12 pairs each, all with gap=0.0 → max_gap=0.0 → raw=4.0.
        per_pair: list[dict] = []
        for axis in ("name_ethnicity", "age_signal", "dialect_register",
                     "pronoun", "religious_marker"):
            per_pair.extend(self._per_pair(12, axis=axis, gap=0.0))
        agg = pa._aggregate_parity(per_pair, minimum_n=30)
        self.assertEqual(agg["status"], "ok")
        self.assertEqual(agg["max_gap"], 0.0)
        self.assertEqual(agg["raw"], 4.0)

    def test_high_gap_maps_to_low_raw(self):
        per_pair: list[dict] = []
        for axis in ("name_ethnicity", "age_signal", "dialect_register",
                     "pronoun", "religious_marker"):
            per_pair.extend(self._per_pair(12, axis=axis, gap=0.50))
        agg = pa._aggregate_parity(per_pair, minimum_n=30)
        self.assertEqual(agg["status"], "ok")
        self.assertGreaterEqual(agg["max_gap"], 0.49)
        self.assertEqual(agg["raw"], 0.0)


class AggregateEoTests(unittest.TestCase):
    def test_tpr_gap_inconclusive_below_floor(self):
        per_pair = [{
            "id": f"p.{i}", "axis": "age_signal", "scenario_id": "x",
            "matches_a": True, "matches_b": True,
        } for i in range(10)]
        agg = pa._aggregate_eo(per_pair, minimum_n=30)
        self.assertEqual(agg["status"], "inconclusive")

    def test_tpr_gap_pooled_pass(self):
        # 40 pairs, 100% match both groups → gap 0.0 → raw 4.0
        per_pair = []
        for axis in ("age_signal", "name_ethnicity"):
            for i in range(20):
                per_pair.append({
                    "id": f"{axis}.{i}", "axis": axis, "scenario_id": "x",
                    "matches_a": True, "matches_b": True,
                })
        agg = pa._aggregate_eo(per_pair, minimum_n=30)
        self.assertEqual(agg["status"], "ok")
        self.assertEqual(agg["tpr_gap"], 0.0)
        self.assertEqual(agg["raw"], 4.0)


class AggregateSentimentTests(unittest.TestCase):
    def test_zero_delta_maps_to_raw_4(self):
        per_pair = []
        for axis in ("name_ethnicity", "age_signal", "dialect_register",
                     "pronoun", "religious_marker"):
            for i in range(12):
                per_pair.append({
                    "id": f"{axis}.{i}", "axis": axis, "scenario_id": "x",
                    "sent_a": {"warmth": 0.7, "urgency": 0.5, "autonomy_respect": 0.6},
                    "sent_b": {"warmth": 0.7, "urgency": 0.5, "autonomy_respect": 0.6},
                })
        agg = pa._aggregate_sentiment(per_pair, minimum_n=30)
        self.assertEqual(agg["status"], "ok")
        self.assertEqual(agg["max_std_dev"], 0.0)
        self.assertEqual(agg["raw"], 4.0)

    def test_large_delta_maps_to_low_raw(self):
        per_pair = []
        for axis in ("name_ethnicity", "age_signal", "dialect_register",
                     "pronoun", "religious_marker"):
            for i in range(12):
                per_pair.append({
                    "id": f"{axis}.{i}", "axis": axis, "scenario_id": "x",
                    "sent_a": {"warmth": 1.0, "urgency": 0.5, "autonomy_respect": 0.6},
                    "sent_b": {"warmth": 0.5, "urgency": 0.5, "autonomy_respect": 0.6},
                })
        agg = pa._aggregate_sentiment(per_pair, minimum_n=30)
        self.assertEqual(agg["status"], "ok")
        self.assertAlmostEqual(agg["max_std_dev"], 0.5, places=4)
        self.assertEqual(agg["raw"], 0.0)


# --- Stateful counters for end-to-end run() tests ---


class _Stateful:
    """Holds iterators so the test-scoped mock judges can yield deterministic
    JSON one call at a time without global state."""

    def __init__(self):
        self.subject_calls = 0
        self.judge_calls = 0


_state = _Stateful()


def _balanced_subject(prompt: str) -> str:
    _state.subject_calls += 1
    return f"[mock subject response #{_state.subject_calls}]"


def _balanced_parity_judge(prompt: str) -> str:
    """Same outcomes regardless of response → zero gap."""
    _state.judge_calls += 1
    return ('{"routed_to_specialist": true, "urgent_flag": true, '
            '"free_consult_offered": false, "escalation_offered": true}')


def _balanced_eo_judge(prompt: str) -> str:
    _state.judge_calls += 1
    return '{"matches_expected": true, "evidence": "ok"}'


def _balanced_sent_judge(prompt: str) -> str:
    _state.judge_calls += 1
    return '{"warmth": 0.7, "urgency": 0.5, "autonomy_respect": 0.6}'


class RunEndToEndTests(unittest.TestCase):
    def setUp(self):
        _state.subject_calls = 0
        _state.judge_calls = 0

    def test_parity_e2e_on_60_pair_corpus(self):
        pairs = pa.load_pairs(CORPUS_YAML)
        subject = MockRunner("subj", _balanced_subject)
        judge = MockRunner("judge", _balanced_parity_judge)
        result = pa.run("parity", subject, judge, pairs, ensemble_n=1)
        self.assertEqual(result["dim_id"], "P2.1")
        self.assertEqual(result["benchmark"], "demographic_parity-v0.1.1")
        self.assertEqual(result["dim_status"], "ok")
        self.assertEqual(result["headline"]["max_gap"], 0.0)
        self.assertEqual(result["dim_raw"], 4.0)
        # Subject called twice per pair, judge called twice per pair * ensemble_n.
        self.assertEqual(_state.subject_calls, 120)

    def test_eo_e2e_on_labeled_subset(self):
        all_pairs = pa.load_pairs(CORPUS_YAML)
        labeled = pa.load_eo_labeled_subset(all_pairs, EO_YAML)
        subject = MockRunner("subj", _balanced_subject)
        judge = MockRunner("judge", _balanced_eo_judge)
        # Labeled subset is small in v0.1.1 (4 scenarios × few pairs each);
        # use minimum_n=2 to validate aggregation math without contriving.
        result = pa.run("equal_opportunity", subject, judge, labeled,
                        ensemble_n=1, minimum_n=2)
        self.assertEqual(result["dim_id"], "P2.2")
        self.assertEqual(result["benchmark"], "equal_opportunity-v0.1.1")
        # All true on both groups → 0 gap → raw 4.0.
        self.assertEqual(result["dim_status"], "ok")
        self.assertEqual(result["dim_raw"], 4.0)

    def test_eo_e2e_inconclusive_at_default_minimum_n(self):
        """Labeled subset is small in v0.1.1 — at default minimum_n=30
        this dim should emit inconclusive (which is a valid state per
        BEAAA-203 acceptance, not a bug)."""
        all_pairs = pa.load_pairs(CORPUS_YAML)
        labeled = pa.load_eo_labeled_subset(all_pairs, EO_YAML)
        subject = MockRunner("subj", _balanced_subject)
        judge = MockRunner("judge", _balanced_eo_judge)
        result = pa.run("equal_opportunity", subject, judge, labeled,
                        ensemble_n=1)  # default minimum_n=30
        # If the labeled subset is >=30, this still passes; otherwise we
        # get an inconclusive headline — that is the legitimate state.
        self.assertIn(result["dim_status"], ("ok", "inconclusive"))
        if result["dim_status"] == "inconclusive":
            self.assertIsNone(result["dim_raw"])

    def test_sentiment_e2e_on_60_pair_corpus(self):
        pairs = pa.load_pairs(CORPUS_YAML)
        subject = MockRunner("subj", _balanced_subject)
        judge = MockRunner("judge", _balanced_sent_judge)
        result = pa.run("sentiment", subject, judge, pairs, ensemble_n=1)
        self.assertEqual(result["dim_id"], "P2.5")
        self.assertEqual(result["dim_status"], "ok")
        self.assertEqual(result["headline"]["max_std_dev"], 0.0)
        self.assertEqual(result["dim_raw"], 4.0)


class CurveSyncTests(unittest.TestCase):
    """Pin PARITY_CUTS and SENTIMENT_CUTS to the YAML spec — byte-stable.

    Drift between the runner's vendored cuts and the YAML cuts breaks the
    promise that the YAML is source-of-truth. This test fails loudly so
    any spec edit also touches the runner.
    """

    def test_parity_cuts_match_yaml(self):
        import yaml as _yaml
        dp = _yaml.safe_load(
            (REPO / "rubric/probe_mappings/demographic_parity.yaml").read_text()
        )
        yaml_cuts = dp["raw_mapping"]["cuts"]
        # Normalise types for compare (yaml may emit ints).
        self.assertEqual(
            [(float(c["max_gap"]), float(c["raw"])) for c in yaml_cuts],
            [(float(c["max_gap"]), float(c["raw"])) for c in pa.PARITY_CUTS],
        )

    def test_sentiment_cuts_match_yaml(self):
        import yaml as _yaml
        ss = _yaml.safe_load(
            (REPO / "rubric/probe_mappings/sentiment_skew.yaml").read_text()
        )
        yaml_cuts = ss["raw_mapping"]["cuts"]
        self.assertEqual(
            [(float(c["max_std_dev"]), float(c["raw"])) for c in yaml_cuts],
            [(float(c["max_std_dev"]), float(c["raw"])) for c in pa.SENTIMENT_CUTS],
        )


if __name__ == "__main__":
    unittest.main()
