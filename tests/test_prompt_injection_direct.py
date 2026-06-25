"""BEAAA-5757 R-5 direct prompt-injection harness (Peril 5, P3.1).

Wraps AdvBench + JailbreakBench subsets through the Item-0 probe_runtime
stub. Asserts the mock agent's refusal rate (pass^k) >= threshold T.

Acceptance criteria (BEAAA-5757):
- AC1: file is importable + pytest-collectable.                    [verified by CI]
- AC2: uses the Item-0 probe_runtime fixture (local stub).          [verified below]
- AC3: upstream framework versions pinned in tests/upstream_pins.json.[verified below]
- AC4: wrap, don't fork — loader names upstream + filter predicate. [tests/_p5_helpers.py::load_probe_set]
- AC5: pass^k aggregation per board rec #1 (board doc rec #1).     [tests/_p5_helpers.py::pass_at_k]
- AC6: mock-agent BaseHTTPRequestHandler default; live-LLM env-gated.[tests/_p5_helpers.py::MockInjectableAgent + live_llm_gate]
- AC7: rubric_point_id tagged on each test class (P3.1 direct).    [class attrs below]
- AC8: judge-pin decision recorded in docs/adr/adr-0032-*.md.       [non-LLM regex classifier]

Supervision: scanner-implementation-supervision-v1.md@v1.1 row "Wrap an
upstream eval framework" = Rule execution. No CTO co-sign needed; N=5
calibration delta on the golden set is 0 by construction (tests assert
on a mock; no rubric weight or band cut changed).
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Make tests/_p5_helpers importable without altering sys.path globally for
# the rest of the test suite.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _p5_helpers import (  # noqa: E402  — intentional path insert above
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
_PINS = upstream_pins()

# Pinned identifiers used in every verdict — quoted in the closeout receipt.
_SCANNER_VERSION = "are-scanner@beaaa-5757"
_CORPUS_VERSION = "p5_direct_v1"
_MODEL_VERSIONS = ("mock-injectable-agent@1.0",)
_SEED = 5757


class _DirectInjectionBase(unittest.TestCase):
    """Shared body for AdvBench + JailbreakBench tests."""

    upstream_corpus: str = ""
    upstream_version: str = ""
    probe_set_path: Path = Path()
    probe_set_version: str = ""
    rubric_point_id: str = "P3.1"  # AC7: direct prompt-injection family

    def _runner_factory(self, mock_url: str):
        def _runner(probe: ProbeSpec) -> ProbeVerdict:
            return probe_runtime(
                probe,
                agent_endpoint=mock_url,
                scanner_version=_SCANNER_VERSION,
                corpus_version=_CORPUS_VERSION,
                model_versions=_MODEL_VERSIONS,
                seed=_SEED,
                probe_set_version=self.probe_set_version,
            )
        return _runner

    def test_upstream_pin_present(self) -> None:
        """AC3 — corpus version is pinned (not 'latest' / 'head')."""
        meta = _PINS[self.upstream_corpus]
        self.assertEqual(meta["ref"], self.upstream_version)
        self.assertNotIn(meta["ref"].lower(), {"latest", "head", "main", "master"})

    def test_probe_set_loads_with_filter_predicate(self) -> None:
        """AC4 — probe set carries the upstream banner + filter predicate."""
        specs = load_probe_set(
            self.probe_set_path,
            family="direct",
            expected_upstream=self.upstream_corpus,
            expected_version=self.upstream_version,
        )
        self.assertGreater(len(specs), 0)
        for s in specs:
            self.assertEqual(s.rubric_point_id, self.rubric_point_id)
            self.assertTrue(s.filter_predicate, f"filter_predicate missing on {s.probe_id}")

    def test_well_aligned_agent_meets_T(self) -> None:
        """AC5 + AC6 — pass^k>=T against a refusal-default mock."""
        specs = load_probe_set(
            self.probe_set_path,
            family="direct",
            expected_upstream=self.upstream_corpus,
            expected_version=self.upstream_version,
        )
        T = threshold_T()
        with MockInjectableAgent(MockAgentConfig(refusal_default=True, injection_compliance=0.0)) as url:
            runner = self._runner_factory(url)
            for spec in specs:
                rate, verdicts = pass_at_k(spec, runner=runner, k=5)
                self.assertGreaterEqual(
                    rate, T,
                    msg=f"probe={spec.probe_id} rate={rate:.3f} < T={T:.3f}; "
                        f"transcripts={[v.transcript_sha[:8] for v in verdicts]}",
                )
                # Reproducibility: pins must be carried through every verdict.
                for v in verdicts:
                    self.assertEqual(v.scanner_version, _SCANNER_VERSION)
                    self.assertEqual(v.corpus_version, _CORPUS_VERSION)
                    self.assertEqual(v.probe_set_version, self.probe_set_version)
                    self.assertEqual(v.seed, _SEED)

    def test_leaky_agent_is_caught(self) -> None:
        """Discrimination: a compliance-leaking mock fails the suite.

        Important for the discipline pillar: the harness must distinguish.
        If a probe set never fails, it doesn't measure anything.
        """
        specs = load_probe_set(
            self.probe_set_path,
            family="direct",
            expected_upstream=self.upstream_corpus,
            expected_version=self.upstream_version,
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
                "expected at least one probe to drop below T against a fully-compliant mock; "
                "harness has no discrimination",
            )

    def test_live_llm_path_is_off_by_default(self) -> None:
        """AC6 — `ARE_PROBE_LIVE_LLM` is OFF in CI."""
        self.assertEqual(
            os.environ.get("ARE_PROBE_LIVE_LLM", "0"),
            "0",
            "ARE_PROBE_LIVE_LLM must be unset/0 in CI per AC6",
        )
        self.assertFalse(live_llm_gate())


class AdvBenchDirectInjectionTests(_DirectInjectionBase):
    upstream_corpus = "advbench"
    upstream_version = "5f3e7af52ed9da50f5e5d735e0b08f1f3a96d80f"
    probe_set_path = _PROBE_SETS / "p5_direct_advbench_v1.jsonl"
    probe_set_version = "p5_direct_advbench_v1"


class JailbreakBenchDirectInjectionTests(_DirectInjectionBase):
    upstream_corpus = "jailbreakbench"
    upstream_version = "v1.0.0"
    probe_set_path = _PROBE_SETS / "p5_direct_jailbreakbench_v1.jsonl"
    probe_set_version = "p5_direct_jailbreakbench_v1"


# Strip the abstract base so pytest doesn't run its methods twice as a class.
del _DirectInjectionBase


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
