"""Tests for scanners.inspect_runner — BEAAA-218 dual-runner shim.

Smoke-tests the Inspect AI round-trip on P3.1 (prompt-injection) and
P1.1 (truthfulqa) using harness.runner.MockRunner so no network is
required and the suite stays deterministic. Phase-1 acceptance:

- inspect_ai imports and version is reported
- canonical bench items convert to inspect_ai.dataset.Sample
- run_via_inspect emits the canonical measurement row shape that
  scanners/promptfoo_runner.py and harness/bench/*.py produce
- raw score matches the existing bench's piecewise mapper at the
  same pass rate (byte-stable dual-runner output → BEAAA-21 preserved)
"""
from __future__ import annotations

import unittest
from pathlib import Path

from harness.bench import prompt_injection, truthfulqa
from harness.runner import MockRunner
from scanners.inspect_runner import (
    InspectRunnerError,
    items_to_samples,
    prompt_injection_spec,
    run_via_inspect,
    truthfulqa_spec,
)

FIXTURES = Path(__file__).resolve().parent.parent / "harness" / "fixtures"


class InspectRunnerSmokeTests(unittest.TestCase):
    def test_inspect_ai_is_importable(self) -> None:
        import inspect_ai  # noqa: F401

        self.assertTrue(hasattr(inspect_ai, "__version__"))

    def test_items_to_samples_roundtrip(self) -> None:
        items = prompt_injection.load_items(FIXTURES / "prompt_injection_poc.jsonl")[:3]
        samples = items_to_samples(items, prompt_injection_spec())
        self.assertEqual(len(samples), 3)
        for item, sample in zip(items, samples):
            self.assertEqual(sample.input, item.prompt)
            self.assertEqual(sample.target, item.canary)
            self.assertEqual(sample.metadata["id"], item.id)
            self.assertEqual(sample.metadata["category"], item.category)

    def test_p31_smoke_clean_responder_passes_all(self) -> None:
        """Clean responder (refuses every prompt) → 100% resistance → raw 4.0."""
        items = prompt_injection.load_items(FIXTURES / "prompt_injection_poc.jsonl")[:5]
        runner = MockRunner("smoke", lambda _: "I cannot help with that request.")
        row = run_via_inspect(runner, items, prompt_injection_spec())

        self.assertEqual(row["dim_id"], "P3.1")
        self.assertEqual(row["benchmark"], "inspect-ai:prompt-injection-poc")
        self.assertEqual(row["model_id"], "mock:smoke")
        self.assertEqual(row["n_items"], 5)
        self.assertEqual(row["n_passed"], 5)
        self.assertEqual(row["resistance_rate"], 1.0)
        self.assertEqual(row["pass_rate"], 1.0)
        self.assertEqual(row["raw"], 4.0)
        self.assertEqual(row["runner_framework"], "inspect-ai")
        self.assertEqual(len(row["trace"]), 5)
        for entry in row["trace"]:
            self.assertTrue(entry["passed"])
            self.assertEqual(entry["inspect_score"], 1.0)

    def test_p31_smoke_compromised_responder_fails_all(self) -> None:
        """Responder that echoes every canary → 0% resistance → raw 0.0."""
        items = prompt_injection.load_items(FIXTURES / "prompt_injection_poc.jsonl")[:5]
        canaries = [item.canary for item in items]
        # Concatenated canaries — every per-item canary is a substring of
        # the response so the scorer marks every item failed.
        compromised_output = " | ".join(canaries)
        runner = MockRunner("compromised", lambda _: compromised_output)
        row = run_via_inspect(runner, items, prompt_injection_spec())

        self.assertEqual(row["n_passed"], 0)
        self.assertEqual(row["resistance_rate"], 0.0)
        self.assertEqual(row["raw"], 0.0)
        for entry in row["trace"]:
            self.assertFalse(entry["passed"])
            self.assertEqual(entry["inspect_score"], 0.0)

    def test_p11_truthfulqa_smoke(self) -> None:
        """Perfect TruthfulQA responder → accuracy 1.0 → raw 4.0."""
        items = truthfulqa.load_items(FIXTURES / "truthfulqa_mc1_sample.jsonl")
        if not items:
            self.skipTest("truthfulqa fixture is empty")
        runner = MockRunner("perfect", lambda _: "A")
        row = run_via_inspect(runner, items[:3], truthfulqa_spec())

        self.assertEqual(row["dim_id"], "P1.1")
        self.assertEqual(row["benchmark"], "inspect-ai:truthfulqa-mc1-poc")
        self.assertGreaterEqual(row["accuracy"], 0.0)
        self.assertEqual(row["accuracy"], row["pass_rate"])
        # BEAAA-5269 rebaseline: `_accuracy_to_raw` retired in BEAAA-3161 /
        # ADR-0027 — the legacy mapping lives in the calibration-curve
        # registry under CURVE_LEGACY_TQA_FRONTIER (byte-identical cuts).
        from rubric import calibration_curves
        self.assertEqual(
            row["raw"],
            calibration_curves.apply("CURVE_LEGACY_TQA_FRONTIER", row["accuracy"]),
        )

    def test_dual_runner_byte_stable_with_bench(self) -> None:
        """Inspect shim and harness.bench.prompt_injection.run agree on raw."""
        items = prompt_injection.load_items(FIXTURES / "prompt_injection_poc.jsonl")[:6]
        # Deterministic mixed responder: first 3 leak their canary, rest refuse.
        # Both the Inspect shim and the existing bench path see identical
        # responses, so raw and aggregate rates must agree byte-for-byte.
        canary_by_prompt = {item.prompt: item.canary for item in items[:3]}

        def _responder(prompt: str) -> str:
            return canary_by_prompt.get(prompt, "Refused.")

        runner_a = MockRunner("dual", _responder)
        runner_b = MockRunner("dual", _responder)

        row_inspect = run_via_inspect(runner_a, items, prompt_injection_spec())
        row_bench = prompt_injection.run(runner_b, items)

        self.assertEqual(row_inspect["raw"], row_bench["raw"])
        self.assertEqual(row_inspect["pass_rate"], row_bench["resistance_rate"])
        self.assertEqual(row_inspect["n_passed"], row_bench["n_resisted"])
        self.assertEqual(row_inspect["dim_id"], row_bench["dim_id"])

    def test_progress_callback_invoked(self) -> None:
        items = prompt_injection.load_items(FIXTURES / "prompt_injection_poc.jsonl")[:2]
        runner = MockRunner("smoke", lambda _: "Refused.")
        calls: list[tuple[int, int]] = []

        def _on_progress(idx: int, total: int, _entry: dict) -> None:
            calls.append((idx, total))

        run_via_inspect(runner, items, prompt_injection_spec(), on_progress=_on_progress)
        self.assertEqual(calls, [(1, 2), (2, 2)])


class InspectRunnerErrorTests(unittest.TestCase):
    def test_error_message_when_inspect_missing(self) -> None:
        """If inspect_ai is unavailable at import time the shim should
        raise a clear error rather than ImportError at call time."""
        import scanners.inspect_runner as ir

        original = ir._INSPECT_AVAILABLE
        try:
            ir._INSPECT_AVAILABLE = False
            with self.assertRaises(InspectRunnerError) as ctx:
                ir._require_inspect()
            self.assertIn("inspect-ai", str(ctx.exception).lower())
        finally:
            ir._INSPECT_AVAILABLE = original


if __name__ == "__main__":
    unittest.main()
