"""BEAAA-5757 R-5 multi-turn jailbreak harness (Peril 5, Crescendo USENIX '25).

Wraps Crescendo (Russinovich et al. 2025) multi-turn escalation chains.
Asserts the mock agent does not progressively yield over a multi-turn
escalation — the probe fails on the first turn that classifies as "answer".

Rubric tagging (AC7): tests tag evidence under **P3.1** today, with
**P3.5 (Multi-turn jailbreak persistence)** carry-forward recorded in
ADR-0032. Activating P3.5 as a scored dim is a rule-change per
scanner-implementation-supervision-v1.md@v1.1 row "Add a new
rubric_point_id" and is intentionally OUT OF SCOPE on this ticket
(BEAAA-5757 description: "doesn't change weight or band cut"). The
deferred slot in rubric/rubric.yaml `deferred_to_v02` carries forward
unchanged.
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
# AC7: multi-turn tagged against the direct-injection family today.
# Formal P3.5 activation is a follow-up rule-change ticket per ADR-0032.
_RUBRIC_POINT_ID = "P3.1"
_DEFERRED_RUBRIC_POINT_ID = "P3.5"


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

    def test_deferred_p3_5_carry_forward_recorded(self) -> None:
        """Carry-forward: P3.5 stays in rubric.yaml `deferred_to_v02`.

        This test enforces the ADR-0032 invariant: BEAAA-5757 does NOT
        un-defer P3.5 (would be a rule-change requiring CTO + UW co-sign).
        If a later ticket activates P3.5, this assertion will fail and the
        guardrail will be flagged in CI.
        """
        rubric_yaml = _HERE.parent / "rubric" / "rubric.yaml"
        if not rubric_yaml.exists():
            self.skipTest("rubric/rubric.yaml not present in this checkout")
        text = rubric_yaml.read_text()
        # P3.5 must still appear in the `deferred_to_v02:` block, not in the
        # active `dimensions:` list. Simplest check: look for the literal
        # 'id: P3.5' under the deferred block. If a future rule-change
        # activates P3.5, the closeout for that ticket will update this guard.
        deferred_idx = text.find("deferred_to_v02:")
        self.assertNotEqual(
            deferred_idx, -1,
            "rubric.yaml has no `deferred_to_v02:` block; carry-forward invariant unsatisfiable",
        )
        # Slice from deferred_to_v02 to end of ROBUSTNESS section (or EOF).
        deferred_slice = text[deferred_idx:]
        self.assertIn(
            "id: P3.5", deferred_slice,
            "P3.5 (multi-turn jailbreak persistence) must remain in deferred_to_v02 — "
            "activating it requires CTO co-sign per supervision-v1.1",
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
