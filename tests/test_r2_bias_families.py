"""R2.BIAS Path B family tests — BEAAA-5209 / BEAAA-5238.

Asserts for each of the 6 BIAS_FAIRNESS families:
  - probe_mapping YAML loads, declares the expected dim_id + corpus_version
  - known_good fixture -> raw 4.0 + placeholder=False
  - known_bad fixture  -> raw <= 1.0 + placeholder=False
  - N=5 replay produces a byte-identical envelope (raw spread 0.0)
  - evidence_uri shape and model_versions_detectors_entry pin are correct
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.bench import (  # noqa: E402
    p2_1_demographic_parity_gap,
    p2_2_equal_opportunity_gap,
    p2_3_counterfactual_stability,
    p2_4_eval_representation,
    p2_5_sentiment_skew_by_attribute,
    p2_6_mitigation_evidence,
)
from harness.bench.r2_bias_common import (  # noqa: E402
    CORPUS_VERSION,
    TAXONOMY_PIN,
    envelope_fingerprint,
)


FAMILY_CASES = [
    ("P2.1", p2_1_demographic_parity_gap, "p2_1_demographic_parity_gap"),
    ("P2.2", p2_2_equal_opportunity_gap, "p2_2_equal_opportunity_gap"),
    ("P2.3", p2_3_counterfactual_stability, "p2_3_counterfactual_stability"),
    ("P2.4", p2_4_eval_representation, "p2_4_eval_representation"),
    ("P2.5", p2_5_sentiment_skew_by_attribute, "p2_5_sentiment_skew_by_attribute"),
    ("P2.6", p2_6_mitigation_evidence, "p2_6_mitigation_evidence"),
]


def _fixture_dir(family_id: str) -> Path:
    return REPO / "tests" / "fixtures" / "r2_bias" / family_id


class R2BiasFamiliesTest(unittest.TestCase):
    def test_corpus_version_pin(self) -> None:
        self.assertEqual(CORPUS_VERSION, "r2-bias-2026.06.14")

    def test_taxonomy_pin(self) -> None:
        self.assertEqual(TAXONOMY_PIN, "ifa-protected-attributes-v2026.06.14")

    def test_each_family_known_good_emits_real_score(self) -> None:
        for dim_id, mod, family_id in FAMILY_CASES:
            with self.subTest(dim=dim_id):
                envelope = mod.run(
                    fixture_path=_fixture_dir(family_id) / "known_good_v0.json",
                )
                self.assertEqual(envelope["dim_id"], dim_id)
                self.assertEqual(envelope["family_id"], family_id)
                self.assertEqual(envelope["corpus_version"], CORPUS_VERSION)
                self.assertEqual(envelope["taxonomy_pin"], TAXONOMY_PIN)
                self.assertFalse(envelope["placeholder"], dim_id)
                self.assertEqual(envelope["raw"], 4.0)
                self.assertTrue(envelope["evidence_uri"].startswith("yaml://"))
                self.assertGreaterEqual(len(envelope["evidence_refs"]), 12)

    def test_each_family_known_bad_emits_real_score_under_floor(self) -> None:
        for dim_id, mod, family_id in FAMILY_CASES:
            with self.subTest(dim=dim_id):
                envelope = mod.run(
                    fixture_path=_fixture_dir(family_id) / "known_bad_v0.json",
                )
                self.assertEqual(envelope["dim_id"], dim_id)
                self.assertFalse(envelope["placeholder"], dim_id)
                self.assertLessEqual(
                    envelope["raw"], 1.0, f"{dim_id} raw should be at floor or below"
                )
                self.assertGreaterEqual(envelope["raw"], 0.0)

    def test_n5_replay_byte_identical(self) -> None:
        for dim_id, mod, family_id in FAMILY_CASES:
            with self.subTest(dim=dim_id):
                fixture = _fixture_dir(family_id) / "known_good_v0.json"
                fingerprints = {
                    envelope_fingerprint(mod.run(fixture_path=fixture))
                    for _ in range(5)
                }
                self.assertEqual(
                    len(fingerprints),
                    1,
                    f"{dim_id} N=5 replay produced drift",
                )

    def test_evidence_uri_format(self) -> None:
        for dim_id, mod, family_id in FAMILY_CASES:
            with self.subTest(dim=dim_id):
                envelope = mod.run(
                    fixture_path=_fixture_dir(family_id) / "known_good_v0.json",
                )
                for ref in envelope["evidence_refs"]:
                    self.assertTrue(
                        ref.startswith(
                            f"yaml://rubric/probe_mappings/{family_id}.yaml#"
                        ),
                        ref,
                    )

    def test_model_versions_detectors_entry(self) -> None:
        for dim_id, mod, family_id in FAMILY_CASES:
            with self.subTest(dim=dim_id):
                envelope = mod.run(
                    fixture_path=_fixture_dir(family_id) / "known_good_v0.json",
                )
                self.assertEqual(
                    envelope["model_versions_detectors_entry"],
                    f"r2-bias-probe:{family_id}@{CORPUS_VERSION}",
                )

    def test_fixture_dirs_layout(self) -> None:
        for _, _, family_id in FAMILY_CASES:
            with self.subTest(family=family_id):
                d = _fixture_dir(family_id)
                self.assertTrue((d / "known_good_v0.json").is_file())
                self.assertTrue((d / "known_bad_v0.json").is_file())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
