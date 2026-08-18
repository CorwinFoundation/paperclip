"""BEAAA-24002: P1.3 and P1.5 must emit the pin of the corpus they loaded.

BEAAA-23968 closed this for P1.1 and P1.4. These two families still restated
their pin as a literal, which is the mechanism that let the P1.1 and P1.4
envelopes label a corpus they had not run since BEAAA-22121. The pins agree
with the YAML today, so the positive assertions below pass before and after the
fix; the temp-copy cases are the real before/after — they repoint each runner at
an edited copy of its own map and require the envelope to follow the copy, which
a hardcoded default cannot do.

Expected values are read out of the loaded YAML rather than restated here, so
the next corpus bump cannot desynchronise the envelope again.

No network. MockRunner supplies deterministic subject and judge behavior.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from harness.bench import p1_3_consistency_golden as cg  # noqa: E402
from harness.bench import p1_5_calibration_golden as cal  # noqa: E402
from harness.runner import MockRunner  # noqa: E402

MAPPINGS = REPO / "rubric" / "probe_mappings"
P1_3_YAML = MAPPINGS / "p1_3_consistency_golden.yaml"
P1_5_YAML = MAPPINGS / "p1_5_calibration_golden.yaml"


def _yaml_pin(yaml_path: Path) -> str:
    return yaml.safe_load(yaml_path.read_text())["reproducibility"]["probe_set_version"]


def _copy_with_pin(yaml_path: Path, pin: str, dest_dir: Path) -> Path:
    """Copy a probe map and rewrite only `reproducibility.probe_set_version`.

    The probe bodies are copied verbatim, so a runner pointed at the copy scores
    the same corpus and can only differ on the pin it reports.
    """
    copy = dest_dir / yaml_path.name
    shutil.copyfile(yaml_path, copy)
    data = yaml.safe_load(copy.read_text())
    data["reproducibility"]["probe_set_version"] = pin
    copy.write_text(yaml.safe_dump(data, sort_keys=False))
    return copy


def _p1_3_envelope(load_from: Path, *, corpus_yaml: Path | None = None, **kwargs) -> dict:
    """Run P1.3 over `load_from`, passing `corpus_yaml` only when one is given.

    Leaving `corpus_yaml` unset exercises the runner's own default, which is the
    form that has to keep working on both sides of this change.
    """
    probes = cg.load_probes(load_from)[:2]
    subject = MockRunner("pin-probe-subject", lambda prompt: f"answer for {prompt[:24]}")

    def judge(prompt: str) -> str:
        return "CONSISTENCY: MATCH\nFAILURE_MODE: none\nEVIDENCE_SPAN_A: \nEVIDENCE_SPAN_B: \n"

    if corpus_yaml is not None:
        kwargs["corpus_yaml"] = corpus_yaml
    return cg.run(subject, MockRunner("pin-probe-judge", judge), probes, ensemble_n=1, **kwargs)


def _p1_5_envelope(load_from: Path, *, corpus_yaml: Path | None = None, **kwargs) -> dict:
    probes = cal.load_probes(load_from)[:2]
    by_prompt = {p.prompt + cal.ELICITATION_SUFFIX: f"{p.gold_answer}.\n0.92" for p in probes}
    subject = MockRunner("pin-probe-subject", lambda prompt: by_prompt[prompt])

    def judge(prompt: str) -> str:
        return "CORRECTNESS: CORRECT\nFAILURE_MODE: none\nEVIDENCE_SPAN: \n"

    if corpus_yaml is not None:
        kwargs["corpus_yaml"] = corpus_yaml
    return cal.run(subject, MockRunner("pin-probe-judge", judge), probes, ensemble_n=1, **kwargs)


FAMILIES = (
    ("P1.3", P1_3_YAML, _p1_3_envelope),
    ("P1.5", P1_5_YAML, _p1_5_envelope),
)


class CorpusPinEmissionTests(unittest.TestCase):
    """The acceptance criterion on BEAAA-24002, asserted for both dims.

    Runs on the runner's own default corpus so it holds before and after the
    fix: the pins already agree, so this is the no-regression half.
    """

    def test_envelope_pin_matches_loaded_corpus(self) -> None:
        for dim_id, yaml_path, envelope in FAMILIES:
            with self.subTest(dim=dim_id):
                expected = _yaml_pin(yaml_path)
                out = envelope(yaml_path)
                self.assertEqual(out["dim_id"], dim_id)
                self.assertEqual(out["corpus_version"], expected)
                self.assertEqual(out["probe_set_version"], expected)

    def test_benchmark_label_tracks_the_same_pin(self) -> None:
        for dim_id, yaml_path, envelope in FAMILIES:
            with self.subTest(dim=dim_id):
                name, _, version = _yaml_pin(yaml_path).partition("@")
                out = envelope(yaml_path)
                self.assertEqual(out["benchmark"], f"{name}-v{version}")


class CorpusPinFollowsTheCorpusTests(unittest.TestCase):
    """The negative case: this is what fails on master and passes on the fix."""

    def test_envelope_follows_an_edited_copy_of_the_map(self) -> None:
        for dim_id, yaml_path, envelope in FAMILIES:
            with self.subTest(dim=dim_id):
                family = _yaml_pin(yaml_path).partition("@")[0]
                bumped = f"{family}@9.9.9"
                self.assertNotEqual(bumped, _yaml_pin(yaml_path))
                with tempfile.TemporaryDirectory() as tmp:
                    copy = _copy_with_pin(yaml_path, bumped, Path(tmp))
                    out = envelope(copy, corpus_yaml=copy)
                self.assertEqual(out["corpus_version"], bumped)
                self.assertEqual(out["probe_set_version"], bumped)
                self.assertEqual(out["benchmark"], f"{family}-v9.9.9")

    def test_missing_pin_in_the_map_fails_closed(self) -> None:
        for dim_id, yaml_path, envelope in FAMILIES:
            with self.subTest(dim=dim_id):
                with tempfile.TemporaryDirectory() as tmp:
                    copy = Path(tmp) / yaml_path.name
                    shutil.copyfile(yaml_path, copy)
                    data = yaml.safe_load(copy.read_text())
                    del data["reproducibility"]["probe_set_version"]
                    copy.write_text(yaml.safe_dump(data, sort_keys=False))
                    with self.assertRaises(ValueError):
                        envelope(copy, corpus_yaml=copy)


class ExplicitPinArgumentTests(unittest.TestCase):
    def test_explicit_pin_argument_still_wins(self) -> None:
        """Callers replaying an archived corpus can still pass the pin they mean."""
        for dim_id, yaml_path, envelope in FAMILIES:
            with self.subTest(dim=dim_id):
                family = _yaml_pin(yaml_path).partition("@")[0]
                archived = f"{family}@0.0.1"
                out = envelope(
                    yaml_path,
                    corpus_version=archived,
                    probe_set_version=archived,
                )
                self.assertEqual(out["corpus_version"], archived)
                self.assertEqual(out["probe_set_version"], archived)
                self.assertEqual(out["benchmark"], f"{family}-v0.0.1")


class DefaultCorpusYamlTests(unittest.TestCase):
    def test_default_points_at_the_family_map(self) -> None:
        self.assertEqual(cg.DEFAULT_CORPUS_YAML, P1_3_YAML)
        self.assertEqual(cal.DEFAULT_CORPUS_YAML, P1_5_YAML)


if __name__ == "__main__":
    unittest.main()
