"""BEAAA-5752 R-4 grounded-factuality harness (Peril 4, P1.1).

Wraps HaluEval-QA + TruthfulQA subsets through the substrate. Asserts the
agent's numeric-claim equality vs the mock tool source's actual return
across N=5 repeated trials.

The hallucination class addressed here is the one that moves money: the
probe agent makes a numeric claim derived from a tool source (price /
balance / count / date / identifier); if the answer does not match the
tool's actual return, the test fails.

Acceptance criteria (BEAAA-5752):
  - test_numeric_claim_matches_tool_source (AC named primary test method)
  - known-good + known-bad probe paths (discrimination test below)
  - HaluEval / TruthfulQA fixtures wired with pinned versions; fixture sha256s
    recorded in the closeout receipt (tests/artifacts/beaaa-5752/)
  - TEMP=0, fixed seeds, deterministic-replay over N=5 = single-seed in CI
    (Δ on golden set = 0 by construction; rubric mapping deferred to R-7)

Supervision: scanner-implementation-supervision-v1.md@v1.1 row "Wrap an
upstream eval framework" = Rule execution. No CTO co-sign needed; N=5
calibration delta on the golden set is 0 by construction (mock substrate,
no rubric weight or band cut changed).
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Make tests/_p4_helpers importable without altering sys.path globally.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _p4_helpers import (  # noqa: E402  — intentional path insert above
    MockAgentConfig,
    MockGroundedAgent,
    MockToolSource,
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

# Pinned identifiers carried through every verdict — quoted in the receipt.
_SCANNER_VERSION = "are-scanner@beaaa-5752"
_CORPUS_VERSION = "p4_grounded_factuality_v1"
_MODEL_VERSIONS = ("mock-grounded-agent@1.0",)
_SEED = 5752


class _GroundedFactualityBase(unittest.TestCase):
    """Shared body for HaluEval-QA + TruthfulQA tests."""

    upstream_corpus: str = ""
    upstream_version: str = ""
    probe_set_path: Path = Path()
    probe_set_version: str = ""
    rubric_point_id: str = "P1.1"

    def _runner_factory(self, agent_url: str, tool_url: str):
        def _runner(probe: ProbeSpec) -> ProbeVerdict:
            return probe_runtime(
                probe,
                agent_endpoint=agent_url,
                tool_endpoint=tool_url,
                scanner_version=_SCANNER_VERSION,
                corpus_version=_CORPUS_VERSION,
                model_versions=_MODEL_VERSIONS,
                seed=_SEED,
                probe_set_version=self.probe_set_version,
            )
        return _runner

    def _sources_from_probes(self, specs: tuple[ProbeSpec, ...]) -> dict:
        # Mock tool source: probe_id -> tool_data (verbatim).
        return {s.probe_id: s.tool_data for s in specs}

    def test_upstream_pin_present(self) -> None:
        """Corpus version is pinned (not 'latest' / 'head')."""
        meta = _PINS[self.upstream_corpus]
        self.assertEqual(meta["ref"], self.upstream_version)
        self.assertNotIn(meta["ref"].lower(), {"latest", "head", "main", "master"})

    def test_probe_set_loads_with_filter_predicate(self) -> None:
        """Probe set carries the upstream banner + filter predicate."""
        specs = load_probe_set(
            self.probe_set_path,
            family="grounded_factuality",
            expected_upstream=self.upstream_corpus,
            expected_version=self.upstream_version,
        )
        self.assertGreater(len(specs), 0)
        for s in specs:
            self.assertEqual(s.rubric_point_id, self.rubric_point_id)
            self.assertTrue(s.filter_predicate, f"filter_predicate missing on {s.probe_id}")
            self.assertIsNotNone(s.expected_claim, f"expected_claim missing on {s.probe_id}")
            self.assertIn("_expected_field", s.tool_data, f"_expected_field marker missing on {s.probe_id}")

    def test_numeric_claim_matches_tool_source(self) -> None:
        """AC primary — pass^k >= T against a well-aligned mock.

        Across N=5 trials per probe, the agent's claim_value must equal the
        tool source's actual return for the expected field. Wrong-number =
        fail is the explicit semantic.
        """
        specs = load_probe_set(
            self.probe_set_path,
            family="grounded_factuality",
            expected_upstream=self.upstream_corpus,
            expected_version=self.upstream_version,
        )
        sources = self._sources_from_probes(specs)
        T = threshold_T()
        with MockToolSource(sources) as tool_url, \
             MockGroundedAgent(MockAgentConfig(factuality_compliance=1.0)) as agent_url:
            runner = self._runner_factory(agent_url, tool_url)
            for spec in specs:
                rate, verdicts = pass_at_k(spec, runner=runner, k=5)
                self.assertGreaterEqual(
                    rate, T,
                    msg=f"probe={spec.probe_id} rate={rate:.3f} < T={T:.3f}; "
                        f"first_fail_reason={next((v.fail_reason for v in verdicts if not v.pass_), None)!r}; "
                        f"transcripts={[v.transcript_sha[:8] for v in verdicts]}",
                )
                # Reproducibility: pinned metadata carried through every verdict.
                for v in verdicts:
                    self.assertEqual(v.scanner_version, _SCANNER_VERSION)
                    self.assertEqual(v.corpus_version, _CORPUS_VERSION)
                    self.assertEqual(v.probe_set_version, self.probe_set_version)
                    self.assertEqual(v.seed, _SEED)
                    self.assertEqual(v.measured_disposition, "pass")

    def test_wrong_number_is_caught(self) -> None:
        """Discrimination: a hallucinating mock fails the suite.

        Required so the harness measures something. If every probe always
        passes against any mock, the test layer adds no signal.
        """
        specs = load_probe_set(
            self.probe_set_path,
            family="grounded_factuality",
            expected_upstream=self.upstream_corpus,
            expected_version=self.upstream_version,
        )
        sources = self._sources_from_probes(specs)
        T = threshold_T()
        with MockToolSource(sources) as tool_url, \
             MockGroundedAgent(MockAgentConfig(factuality_compliance=0.0)) as agent_url:
            runner = self._runner_factory(agent_url, tool_url)
            any_fail = False
            first_fail_reason: str | None = None
            for spec in specs:
                rate, verdicts = pass_at_k(spec, runner=runner, k=5)
                if rate < T:
                    any_fail = True
                    first_fail_reason = next(
                        (v.fail_reason for v in verdicts if not v.pass_),
                        None,
                    )
                    break
            self.assertTrue(
                any_fail,
                "expected at least one probe to drop below T against a fully-hallucinating mock; "
                "harness has no discrimination",
            )
            self.assertIsNotNone(first_fail_reason)
            self.assertIn("numeric_mismatch", first_fail_reason or "")

    def test_live_llm_path_is_off_by_default(self) -> None:
        """ARE_PROBE_LIVE_LLM must be unset/0 in CI."""
        self.assertEqual(
            os.environ.get("ARE_PROBE_LIVE_LLM", "0"),
            "0",
            "ARE_PROBE_LIVE_LLM must be unset/0 in CI",
        )
        self.assertFalse(live_llm_gate())


class HaluEvalQaGroundedFactualityTests(_GroundedFactualityBase):
    upstream_corpus = "halueval_qa"
    upstream_version = "b7253db3cdaa0ab2c382f92b26b390109174f77e"
    probe_set_path = _PROBE_SETS / "p4_grounded_factuality_halueval_qa_v1.jsonl"
    probe_set_version = "p4_grounded_factuality_halueval_qa_v1"


class TruthfulQAGroundedFactualityTests(_GroundedFactualityBase):
    upstream_corpus = "truthfulqa"
    upstream_version = "d71c110897f5d31c5d7f309e7bc316c152f6f031"
    probe_set_path = _PROBE_SETS / "p4_grounded_factuality_truthfulqa_v1.jsonl"
    probe_set_version = "p4_grounded_factuality_truthfulqa_v1"


# Strip the abstract base so pytest doesn't run its methods twice as a class.
del _GroundedFactualityBase


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
