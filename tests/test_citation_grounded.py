"""BEAAA-5752 R-4 citation-grounded harness (Peril 4, P1.2).

Wraps HaluEval-summarization + FActScore subsets through the substrate.
Asserts the agent emits a citation (URL / doc-id / KB-key) that resolves
to a real source AND the cited text supports the claim.

Two failure modes are explicit:
  1. Hallucinated citation — URL / doc-id that does not exist in the
     available_sources bundle. Fails immediately.
  2. Correct-source-wrong-claim — citation resolves but the agent's claim
     does not contain the expected keyword that ties the claim to the cited
     snippet. Fails immediately.

Acceptance criteria (BEAAA-5752):
  - test_citation_resolves_to_real_source (AC named primary test method)
  - known-good + known-bad probe paths (two discrimination tests below)
  - HaluEval / FActScore fixtures wired with pinned versions; fixture sha256s
    recorded in the closeout receipt (tests/artifacts/beaaa-5752/)
  - TEMP=0, fixed seeds, deterministic-replay over N=5 = single-seed in CI
    (Δ on golden set = 0 by construction; rubric mapping deferred to R-7)

Supervision: scanner-implementation-supervision-v1.md@v1.1 row "Wrap an
upstream eval framework" = Rule execution. No CTO co-sign needed.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _p4_helpers import (  # noqa: E402
    MockAgentConfig,
    MockGroundedAgent,
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

_SCANNER_VERSION = "are-scanner@beaaa-5752"
_CORPUS_VERSION = "p4_citation_grounded_v1"
_MODEL_VERSIONS = ("mock-grounded-agent@1.0",)
_SEED = 5752


class _CitationGroundedBase(unittest.TestCase):
    """Shared body for HaluEval-summ + FActScore tests."""

    upstream_corpus: str = ""
    upstream_version: str = ""
    probe_set_path: Path = Path()
    probe_set_version: str = ""
    rubric_point_id: str = "P1.2"

    def _runner_factory(self, agent_url: str):
        def _runner(probe: ProbeSpec) -> ProbeVerdict:
            return probe_runtime(
                probe,
                agent_endpoint=agent_url,
                tool_endpoint=None,
                scanner_version=_SCANNER_VERSION,
                corpus_version=_CORPUS_VERSION,
                model_versions=_MODEL_VERSIONS,
                seed=_SEED,
                probe_set_version=self.probe_set_version,
            )
        return _runner

    def test_upstream_pin_present(self) -> None:
        """Corpus version is pinned (not 'latest' / 'head')."""
        meta = _PINS[self.upstream_corpus]
        self.assertEqual(meta["ref"], self.upstream_version)
        self.assertNotIn(meta["ref"].lower(), {"latest", "head", "main", "master"})

    def test_probe_set_loads_with_filter_predicate(self) -> None:
        """Probe set carries the upstream banner + filter predicate."""
        specs = load_probe_set(
            self.probe_set_path,
            family="citation_grounded",
            expected_upstream=self.upstream_corpus,
            expected_version=self.upstream_version,
        )
        self.assertGreater(len(specs), 0)
        for s in specs:
            self.assertEqual(s.rubric_point_id, self.rubric_point_id)
            self.assertTrue(s.filter_predicate, f"filter_predicate missing on {s.probe_id}")
            self.assertIsNotNone(s.expected_citation_id, f"expected_citation_id missing on {s.probe_id}")
            self.assertIsNotNone(s.expected_snippet_substring, f"expected_snippet_substring missing on {s.probe_id}")
            self.assertIsNotNone(s.expected_claim_keyword, f"expected_claim_keyword missing on {s.probe_id}")
            self.assertIn(s.expected_citation_id, s.available_sources,
                          f"expected_citation_id not in available_sources for {s.probe_id}")
            self.assertIn(
                s.expected_snippet_substring,
                s.available_sources[s.expected_citation_id],
                f"expected_snippet_substring missing from cited source for {s.probe_id}",
            )

    def test_citation_resolves_to_real_source(self) -> None:
        """AC primary — pass^k >= T against a well-aligned mock.

        Across N=5 trials per probe, the agent's citation must (a) resolve
        to a real source in available_sources, (b) snippet must be a
        substring of the cited source, and (c) claim must contain the
        expected keyword tying it to the cited snippet.
        """
        specs = load_probe_set(
            self.probe_set_path,
            family="citation_grounded",
            expected_upstream=self.upstream_corpus,
            expected_version=self.upstream_version,
        )
        T = threshold_T()
        with MockGroundedAgent(MockAgentConfig(citation_compliance=1.0)) as agent_url:
            runner = self._runner_factory(agent_url)
            for spec in specs:
                rate, verdicts = pass_at_k(spec, runner=runner, k=5)
                self.assertGreaterEqual(
                    rate, T,
                    msg=f"probe={spec.probe_id} rate={rate:.3f} < T={T:.3f}; "
                        f"first_fail_reason={next((v.fail_reason for v in verdicts if not v.pass_), None)!r}",
                )
                for v in verdicts:
                    self.assertEqual(v.scanner_version, _SCANNER_VERSION)
                    self.assertEqual(v.corpus_version, _CORPUS_VERSION)
                    self.assertEqual(v.probe_set_version, self.probe_set_version)
                    self.assertEqual(v.seed, _SEED)
                    self.assertEqual(v.measured_disposition, "pass")

    def test_hallucinated_citation_is_caught(self) -> None:
        """Discrimination: hallucinated citation -> fail.

        Agent emits a citation_id that does not exist in available_sources.
        The grader must catch this; otherwise the harness has no discrimination.
        """
        specs = load_probe_set(
            self.probe_set_path,
            family="citation_grounded",
            expected_upstream=self.upstream_corpus,
            expected_version=self.upstream_version,
        )
        T = threshold_T()
        with MockGroundedAgent(MockAgentConfig(citation_compliance=0.0)) as agent_url:
            runner = self._runner_factory(agent_url)
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
            self.assertTrue(any_fail, "harness must detect hallucinated citations")
            self.assertIsNotNone(first_fail_reason)
            self.assertIn("hallucinated_citation", first_fail_reason or "")

    def test_real_source_wrong_claim_is_caught(self) -> None:
        """Discrimination: real source, but claim does not tie to cited snippet -> fail.

        Agent emits a real citation_id but the claim text does not contain
        the expected keyword. The grader must catch this; this is the
        "correct-source-wrong-claim" failure mode the AC names explicitly.
        """
        specs = load_probe_set(
            self.probe_set_path,
            family="citation_grounded",
            expected_upstream=self.upstream_corpus,
            expected_version=self.upstream_version,
        )
        T = threshold_T()
        with MockGroundedAgent(MockAgentConfig(citation_compliance=1.0, citation_partial=True)) as agent_url:
            runner = self._runner_factory(agent_url)
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
            self.assertTrue(any_fail, "harness must detect correct-source-wrong-claim")
            self.assertIsNotNone(first_fail_reason)
            self.assertIn("claim_keyword_missing", first_fail_reason or "")

    def test_live_llm_path_is_off_by_default(self) -> None:
        """ARE_PROBE_LIVE_LLM must be unset/0 in CI."""
        self.assertEqual(
            os.environ.get("ARE_PROBE_LIVE_LLM", "0"),
            "0",
            "ARE_PROBE_LIVE_LLM must be unset/0 in CI",
        )
        self.assertFalse(live_llm_gate())


class HaluEvalSummCitationGroundedTests(_CitationGroundedBase):
    upstream_corpus = "halueval_summ"
    upstream_version = "b7253db3cdaa0ab2c382f92b26b390109174f77e"
    probe_set_path = _PROBE_SETS / "p4_citation_grounded_halueval_summ_v1.jsonl"
    probe_set_version = "p4_citation_grounded_halueval_summ_v1"


class FActScoreCitationGroundedTests(_CitationGroundedBase):
    upstream_corpus = "factscore"
    upstream_version = "f28272deffcf33efc1f1117d5479c10bb75221a9"
    probe_set_path = _PROBE_SETS / "p4_citation_grounded_factscore_v1.jsonl"
    probe_set_version = "p4_citation_grounded_factscore_v1"


del _CitationGroundedBase


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
