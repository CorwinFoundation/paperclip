"""BEAAA-23968: golden runners must emit the pin of the corpus they loaded.

BEAAA-22121 grew the P1.1 and P1.4 probe maps and bumped both to 0.2.0, but the
runners kept a hardcoded `@0.1.0` default, so every envelope since 1a1ce3c0
reported a corpus_version that does not identify its corpus. These tests read
the expected pin out of the YAML instead of restating it as a literal, so the
next corpus bump cannot desynchronise the envelope again.

No network. MockRunner supplies deterministic subject and judge behavior.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from harness.bench import corpus_pin  # noqa: E402
from harness.bench import factuality_golden as fg  # noqa: E402
from harness.bench import p1_4_refusal_golden as rg  # noqa: E402
from harness.runner import MockRunner  # noqa: E402

MAPPINGS = REPO / "rubric" / "probe_mappings"
P1_1_YAML = MAPPINGS / "p1_1_factuality_golden.yaml"
P1_4_YAML = MAPPINGS / "p1_4_refusal_golden.yaml"

_JUDGE_YES = (
    "ANSWER_MATCH: YES\nPLAUSIBILITY: YES\nFALSE_SPECIFICITY: NO\n"
    "ACTION: ANSWERED\nFAILURE_MODE: none\nEVIDENCE_SPAN: \n"
)


def _yaml_pin(yaml_path: Path) -> str:
    return yaml.safe_load(yaml_path.read_text())["reproducibility"]["probe_set_version"]


class CorpusPinEmissionTests(unittest.TestCase):
    """The acceptance criterion on BEAAA-23968, asserted for both dims."""

    def _run_envelope(self, module, yaml_path: Path) -> dict:
        probes = module.load_probes(yaml_path)[:2]
        subject = MockRunner("pin-probe-subject", lambda _prompt: "placeholder response")
        judge = MockRunner("pin-probe-judge", lambda _prompt: _JUDGE_YES)
        return module.run(subject, judge, probes, ensemble_n=1, corpus_yaml=yaml_path)

    def test_p1_1_envelope_pin_matches_loaded_corpus(self) -> None:
        expected = _yaml_pin(P1_1_YAML)
        out = self._run_envelope(fg, P1_1_YAML)
        self.assertEqual(out["dim_id"], "P1.1")
        self.assertEqual(out["corpus_version"], expected)
        self.assertEqual(out["probe_set_version"], expected)

    def test_p1_4_envelope_pin_matches_loaded_corpus(self) -> None:
        expected = _yaml_pin(P1_4_YAML)
        out = self._run_envelope(rg, P1_4_YAML)
        self.assertEqual(out["dim_id"], "P1.4")
        self.assertEqual(out["corpus_version"], expected)
        self.assertEqual(out["probe_set_version"], expected)

    def test_benchmark_label_tracks_the_same_pin(self) -> None:
        for module, yaml_path in ((fg, P1_1_YAML), (rg, P1_4_YAML)):
            with self.subTest(corpus=yaml_path.name):
                name, _, version = _yaml_pin(yaml_path).partition("@")
                out = self._run_envelope(module, yaml_path)
                self.assertEqual(out["benchmark"], f"{name}-v{version}")

    def test_explicit_pin_argument_still_wins(self) -> None:
        """Callers scanning an archived corpus can still pass the pin they mean."""
        probes = fg.load_probes(P1_1_YAML)[:2]
        subject = MockRunner("pin-probe-subject", lambda _prompt: "placeholder response")
        judge = MockRunner("pin-probe-judge", lambda _prompt: _JUDGE_YES)
        out = fg.run(
            subject,
            judge,
            probes,
            ensemble_n=1,
            corpus_version="p1_1_factuality_golden@0.1.0",
            probe_set_version="p1_1_factuality_golden@0.1.0",
        )
        self.assertEqual(out["corpus_version"], "p1_1_factuality_golden@0.1.0")
        self.assertEqual(out["probe_set_version"], "p1_1_factuality_golden@0.1.0")


class CorpusPinLoaderTests(unittest.TestCase):
    def test_reads_reproducibility_probe_set_version(self) -> None:
        for yaml_path in (P1_1_YAML, P1_4_YAML):
            with self.subTest(corpus=yaml_path.name):
                self.assertEqual(
                    corpus_pin.load_probe_set_version(yaml_path),
                    _yaml_pin(yaml_path),
                )

    def test_missing_pin_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            unpinned = Path(tmp) / "unpinned.yaml"
            unpinned.write_text(yaml.safe_dump({"benchmark": "x", "probes": []}))
            with self.assertRaises(ValueError):
                corpus_pin.load_probe_set_version(unpinned)

    def test_blank_pin_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blank = Path(tmp) / "blank.yaml"
            blank.write_text(yaml.safe_dump({"reproducibility": {"probe_set_version": "  "}}))
            with self.assertRaises(ValueError):
                corpus_pin.load_probe_set_version(blank)

    def test_benchmark_label_derivation(self) -> None:
        self.assertEqual(corpus_pin.benchmark_label("p1_1_factuality_golden@0.2.0"), "p1_1_factuality_golden-v0.2.0")
        self.assertEqual(corpus_pin.benchmark_label("p1_4_refusal_golden"), "p1_4_refusal_golden")


if __name__ == "__main__":
    unittest.main()
