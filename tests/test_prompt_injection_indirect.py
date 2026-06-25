"""BEAAA-5757 R-5 indirect prompt-injection harness (Peril 5, P3.2).

Wraps AgentDojo's indirect-injection subset (injected instruction lives in
tool-call output, not the user prompt). Asserts the mock agent does not
execute the embedded instruction even when it surfaces in a tool response.

Acceptance criteria — see test_prompt_injection_direct.py for the full
list; this file targets P3.2 (Indirect injection (tool/RAG content)
resistance) and exercises the `tool_content` channel on probe rows.
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
_PROBE_SET_PATH = _PROBE_SETS / "p5_indirect_agentdojo_v1.jsonl"
_PINS = upstream_pins()

_SCANNER_VERSION = "are-scanner@beaaa-5757"
_CORPUS_VERSION = "p5_indirect_v1"
_PROBE_SET_VERSION = "p5_indirect_agentdojo_v1"
_MODEL_VERSIONS = ("mock-injectable-agent@1.0",)
_SEED = 5757

_UPSTREAM = "agentdojo"
_UPSTREAM_VERSION = "v0.1.35"
_RUBRIC_POINT_ID = "P3.2"  # AC7: indirect prompt-injection family


class AgentDojoIndirectInjectionTests(unittest.TestCase):
    """P3.2 — indirect injection via tool-call output surface."""

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
        """AC3 — AgentDojo pin matches expected version."""
        meta = _PINS[_UPSTREAM]
        self.assertEqual(meta["ref"], _UPSTREAM_VERSION)
        self.assertNotIn(meta["ref"].lower(), {"latest", "head", "main", "master"})

    def test_probe_set_loads_with_tool_content_channel(self) -> None:
        """AC4 — every indirect probe carries a non-empty `tool_content` payload."""
        specs = load_probe_set(
            _PROBE_SET_PATH,
            family="indirect",
            expected_upstream=_UPSTREAM,
            expected_version=_UPSTREAM_VERSION,
        )
        self.assertGreater(len(specs), 0)
        for s in specs:
            self.assertEqual(s.rubric_point_id, _RUBRIC_POINT_ID)
            self.assertTrue(
                s.tool_content,
                f"indirect probe {s.probe_id} must carry tool_content (the injected channel)",
            )

    def test_well_aligned_agent_meets_T(self) -> None:
        """AC5 + AC6 — pass^k>=T against a refusal-default mock; indirect probes route via tool_content."""
        specs = load_probe_set(
            _PROBE_SET_PATH,
            family="indirect",
            expected_upstream=_UPSTREAM,
            expected_version=_UPSTREAM_VERSION,
        )
        T = threshold_T()
        with MockInjectableAgent(MockAgentConfig(refusal_default=True, injection_compliance=0.0)) as url:
            runner = self._runner_factory(url)
            for spec in specs:
                rate, verdicts = pass_at_k(spec, runner=runner, k=5)
                self.assertGreaterEqual(
                    rate, T,
                    msg=f"indirect probe={spec.probe_id} rate={rate:.3f} < T={T:.3f}",
                )
                for v in verdicts:
                    self.assertEqual(v.probe_set_version, _PROBE_SET_VERSION)
                    self.assertEqual(v.seed, _SEED)

    def test_leaky_agent_is_caught(self) -> None:
        """Discrimination: indirect harness fires when the mock complies with tool-content instructions."""
        specs = load_probe_set(
            _PROBE_SET_PATH,
            family="indirect",
            expected_upstream=_UPSTREAM,
            expected_version=_UPSTREAM_VERSION,
        )
        T = threshold_T()
        with MockInjectableAgent(MockAgentConfig(refusal_default=True, injection_compliance=1.0)) as url:
            runner = self._runner_factory(url)
            any_fail = False
            for spec in specs:
                rate, _ = pass_at_k(spec, runner=runner, k=5)
                if rate < T:
                    any_fail = True
                    break
            self.assertTrue(
                any_fail,
                "indirect harness must distinguish: a compliant mock should drop at least one probe below T",
            )

    def test_live_llm_path_is_off_by_default(self) -> None:
        """AC6 — live-LLM path OFF in CI."""
        self.assertEqual(os.environ.get("ARE_PROBE_LIVE_LLM", "0"), "0")
        self.assertFalse(live_llm_gate())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
