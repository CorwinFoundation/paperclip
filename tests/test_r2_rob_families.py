"""R2.ROB Path B family tests — BEAAA-5208 / BEAAA-5237.

Asserts for each of the 5 ROBUSTNESS families:
  - probe_mapping YAML loads, declares the expected dim_id + corpus_version
  - known_good fixture -> raw 4.0 + placeholder=False
  - known_bad fixture  -> raw <= 1.0 + placeholder=False
  - N=5 replay produces a byte-identical envelope (raw spread 0.0)

Banned-term grep is a separate test in
``tests/test_r2_rob_banned_terms.py`` so the family unit tests stay
self-contained.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.bench import (  # noqa: E402
    p3_2_indirect_injection_rob,
    p3_3_secret_exfiltration_rob,
    pii_scan_time_detection,
    tool_misuse_param_tamper,
    tool_misuse_scope_escape,
    tool_misuse_unauthorized_call,
)
from harness.bench.r2_rob_common import (  # noqa: E402
    CORPUS_VERSION,
    envelope_fingerprint,
)


FAMILY_CASES = [
    (
        "P3.2",
        p3_2_indirect_injection_rob,
        "p3_2_indirect_injection_rob",
    ),
    (
        "P3.3",
        p3_3_secret_exfiltration_rob,
        "p3_3_secret_exfiltration_rob",
    ),
    (
        "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL",
        tool_misuse_unauthorized_call,
        "tool_misuse_unauthorized_call",
    ),
    (
        "ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER",
        tool_misuse_param_tamper,
        "tool_misuse_param_tamper",
    ),
    (
        "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL",
        tool_misuse_scope_escape,
        "tool_misuse_scope_escape",
    ),
    (
        "ROBUSTNESS.PII.SCAN_TIME_DETECTION",
        pii_scan_time_detection,
        "pii_scan_time_detection",
    ),
]


def _fixture_dir(family_id: str) -> Path:
    return REPO / "tests" / "fixtures" / "r2_rob" / family_id


class R2RobFamiliesTest(unittest.TestCase):
    def test_corpus_version_pin(self) -> None:
        self.assertEqual(CORPUS_VERSION, "r2-rob-2026.06.14")

    def test_each_family_known_good_emits_real_score(self) -> None:
        for dim_id, mod, family_id in FAMILY_CASES:
            with self.subTest(dim=dim_id):
                envelope = mod.run(
                    fixture_path=_fixture_dir(family_id) / "known_good_v0.json",
                )
                self.assertEqual(envelope["dim_id"], dim_id)
                self.assertEqual(envelope["family_id"], family_id)
                self.assertEqual(envelope["corpus_version"], CORPUS_VERSION)
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
                self.assertLessEqual(envelope["raw"], 1.0, f"{dim_id} raw should be at floor or below")
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
                    f"r2-rob-probe:{family_id}@{CORPUS_VERSION}",
                )

    def test_fixture_dirs_layout(self) -> None:
        for _, _, family_id in FAMILY_CASES:
            with self.subTest(family=family_id):
                d = _fixture_dir(family_id)
                self.assertTrue((d / "known_good_v0.json").is_file())
                self.assertTrue((d / "known_bad_v0.json").is_file())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
