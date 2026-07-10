"""BEAAA-5757 R-5 multi-turn jailbreak harness (Peril 5, Crescendo USENIX '25).

Wraps Crescendo (Russinovich et al. 2025) multi-turn escalation chains.
Asserts the mock agent does not progressively yield over a multi-turn
escalation — the probe fails on the first turn that classifies as "answer".

Rubric tagging: BEAAA-13388 activates **P3.5 (Multi-turn jailbreak
persistence)** as its own scored ROBUSTNESS dimension. Earlier BEAAA-5757
fixtures tagged this family to P3.1 while P3.5 was deferred; this file now
guards the activation state instead of the old deferral.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _p5_helpers import (  # noqa: E402
    MockAgentConfig,
    MockInjectableAgent,
    ProbeSpec,
    ProbeVerdict,
    live_llm_gate,
    load_probe_set,
    pass_at_k,
    probe_runtime,
    threshold_T,
    upstream_pins,
)

_PROBE_SETS = _HERE / "probe_sets"
_PROBE_SET_PATH = _PROBE_SETS / "p5_multiturn_crescendo_v1.jsonl"
_PINS = upstream_pins()

_SCANNER_VERSION = "are-scanner@beaaa-5757"
_CORPUS_VERSION = "p5_multiturn_v1"
_PROBE_SET_VERSION = "p5_multiturn_crescendo_v1"
_MODEL_VERSIONS = ("mock-injectable-agent@1.0",)
_SEED = 5757

_UPSTREAM = "crescendo"
_UPSTREAM_VERSION = "usenix-25-camera-ready"
_RUBRIC_POINT_ID = "P3.5"


class CrescendoMultiTurnInjectionTests(unittest.TestCase):
    """Crescendo escalation chain — agent must hold across all turns."""

    def _runner_factory(self, mock_url: str):
        def _runner(probe: ProbeSpec) -> ProbeVerdict:
            return probe_runtime(
                probe,
                agent_endpoint=mock_url,
                scanner_version=_SCANNER_VERSION,
                corpus_version=_CORPUS_VERSION,
                model_versions=_MODEL_VERSIONS,
                seed=_SEED,
                probe_set_version=_PROBE_SET_VERSION,
            )
        return _runner

    def test_upstream_pin_present(self) -> None:
        """AC3 — Crescendo pin matches expected version."""
        meta = _PINS[_UPSTREAM]
        self.assertEqual(meta["ref"], _UPSTREAM_VERSION)
        self.assertNotIn(meta["ref"].lower(), {"latest", "head", "main", "master"})

    def test_probe_set_loads_with_turn_chain(self) -> None:
        """AC4 — every multi-turn probe carries a non-empty `turns` sequence."""
        specs = load_probe_set(
            _PROBE_SET_PATH,
            family="multiturn",
            expected_upstream=_UPSTREAM,
            expected_version=_UPSTREAM_VERSION,
        )
        self.assertGreater(len(specs), 0)
        for s in specs:
            self.assertEqual(s.rubric_point_id, _RUBRIC_POINT_ID)
            self.assertGreaterEqual(
                len(s.turns), 2,
                f"multi-turn probe {s.probe_id} must carry >=2 turns; got {len(s.turns)}",
            )

    def test_p3_5_activation_recorded(self) -> None:
        """BEAAA-13388: P3.5 is active and no longer deferred."""
        rubric_yaml = _HERE.parent / "rubric" / "rubric.yaml"
        if not rubric_yaml.exists():
            self.skipTest("rubric/rubric.yaml not present in this checkout")
        text = rubric_yaml.read_text()
        robustness_idx = text.find("ROBUSTNESS:")
        deferred_idx = text.find("deferred_to_v02:")
        self.assertNotEqual(robustness_idx, -1)
        self.assertNotEqual(deferred_idx, -1)
        active_slice = text[robustness_idx:deferred_idx]
        deferred_slice = text[deferred_idx:text.find("GOVERNANCE:", deferred_idx)]
        self.assertIn("id: P3.5", active_slice)
        self.assertNotIn("id: P3.5", deferred_slice)
        self.assertIn(
            "BEAAA-13388 candidate activation", active_slice,
            "P3.5 activation must cite the score-impacting gate",
        )

    def test_well_aligned_agent_holds_across_turns(self) -> None:
        """AC5 — refusal-default mock holds across the entire escalation chain.

        Probe-level semantics (`probe_runtime` multi-turn loop): the probe
        fails on the first turn that classifies as `answer`. A well-aligned
        agent refuses every turn, so rate >= T against k=5 reruns.
        """
        specs = load_probe_set(
            _PROBE_SET_PATH,
            family="multiturn",
            expected_upstream=_UPSTREAM,
            expected_version=_UPSTREAM_VERSION,
        )
        T = threshold_T()
        with MockInjectableAgent(MockAgentConfig(refusal_default=True, yield_after_n_turns=None)) as url:
            runner = self._runner_factory(url)
            for spec in specs:
                rate, verdicts = pass_at_k(spec, runner=runner, k=5)
                self.assertGreaterEqual(
                    rate, T,
                    msg=f"multiturn probe={spec.probe_id} rate={rate:.3f} < T={T:.3f}; "
                        f"the chain must not yield against a hold-firm mock",
                )
                for v in verdicts:
                    self.assertEqual(v.probe_set_version, _PROBE_SET_VERSION)

    def test_progressively_yielding_agent_is_caught(self) -> None:
        """Discrimination: a mock that yields on turn 3 of a 4-turn chain fails."""
        specs = load_probe_set(
            _PROBE_SET_PATH,
            family="multiturn",
            expected_upstream=_UPSTREAM,
            expected_version=_UPSTREAM_VERSION,
        )
        T = threshold_T()
        # yield_after_n_turns=2 → starting at turn index 2 (3rd turn), the
        # mock complies. Every k-run yields, so rate = 0 < T.
        with MockInjectableAgent(MockAgentConfig(refusal_default=True, yield_after_n_turns=2)) as url:
            runner = self._runner_factory(url)
            any_fail = False
            for spec in specs:
                rate, _ = pass_at_k(spec, runner=runner, k=5)
                if rate < T:
                    any_fail = True
                    break
            self.assertTrue(
                any_fail,
                "multi-turn harness must distinguish progressive yielding from holding firm",
            )

    def test_live_llm_path_is_off_by_default(self) -> None:
        """AC6 — live-LLM path OFF in CI."""
        self.assertEqual(os.environ.get("ARE_PROBE_LIVE_LLM", "0"), "0")
        self.assertFalse(live_llm_gate())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
