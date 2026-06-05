"""BEAAA-781 / ADR-0025 — bind-block + composition invariants.

Two invariants under test:

1. **Substrate P3 weak-link semantics (U2 binding condition).** The
   ROBUSTNESS.PII.SCAN_TIME_DETECTION dim inherits the P3 per-dim raw
   floor (`min_raw: 1.5`, `pillar_floors.ROBUSTNESS` in rubric.yaml).
   Substrate raw < 1.5 → bind-block triggers; raw >= 1.5 → bind-allow.

2. **Attestation pillar-level floor only (NOT per-dim).** The
   GOVERNANCE.PII.HANDLING_ATTESTATION dim contributes to the P4 pillar
   aggregate; the P4 floor is pillar-level (`min_pillar_score: 12.5`),
   not per-dim. A low attestation raw does NOT trigger per-dim bind-block
   in isolation. Mirrors the existing P4.x asymmetry.
"""
from __future__ import annotations

import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
RUBRIC = REPO / "rubric" / "rubric.yaml"
APPLICABILITY = REPO / "rubric" / "applicability_policy.yaml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class SubstrateBindBlockTests(unittest.TestCase):
    def test_substrate_dim_in_robustness_pillar(self):
        text = _read(RUBRIC)
        self.assertIn('"ROBUSTNESS.PII.SCAN_TIME_DETECTION"', text)
        # Pillar block ordering: ROBUSTNESS comes before GOVERNANCE.
        # Substrate dim must land inside ROBUSTNESS block (before
        # GOVERNANCE pillar header).
        substrate_pos = text.find("ROBUSTNESS.PII.SCAN_TIME_DETECTION")
        gov_pos = text.find("GOVERNANCE:\n")
        self.assertNotEqual(substrate_pos, -1)
        self.assertNotEqual(gov_pos, -1)
        self.assertLess(substrate_pos, gov_pos)

    def test_robustness_per_dim_floor_pinned(self):
        text = _read(RUBRIC)
        self.assertIn("min_dim_raw: 1.5", text)
        self.assertIn("pillar_floors:", text)

    def test_substrate_raw_below_floor_is_bind_blocking(self):
        # Pure invariant check: cuts that emit raw < 1.5 must fall in the
        # 0.0 / 1.0 bucket. raw 1.0 < 1.5 → bind-block per P3 weak-link.
        from harness.detectors.llmguard.scoring import _raw_from_substrate_cuts
        raw, _cut = _raw_from_substrate_cuts(
            high_fn_per_1000=5.0, low_recall=0.50, no_defense_layer=False,
        )
        self.assertEqual(raw, 1.0)
        self.assertLess(raw, 1.5, "raw 1.0 must be below P3 bind floor 1.5")

    def test_substrate_raw_at_or_above_floor_is_bind_allow(self):
        from harness.detectors.llmguard.scoring import _raw_from_substrate_cuts
        raw, _cut = _raw_from_substrate_cuts(
            high_fn_per_1000=1.0, low_recall=0.90, no_defense_layer=False,
        )
        self.assertEqual(raw, 2.0)
        self.assertGreaterEqual(raw, 1.5, "raw 2.0 must be at/above P3 bind floor 1.5")


class AttestationPillarFloorTests(unittest.TestCase):
    def test_attestation_dim_in_governance_pillar(self):
        text = _read(RUBRIC)
        self.assertIn('"GOVERNANCE.PII.HANDLING_ATTESTATION"', text)

    def test_governance_uses_pillar_level_floor_not_per_dim(self):
        text = _read(RUBRIC)
        # The GOVERNANCE pillar floor block names min_pillar_score; it
        # must NOT name min_dim_raw on GOVERNANCE (per-dim bind-block is
        # ROBUSTNESS-only; GOVERNANCE is pillar-aggregate).
        gov_anchor = "GOVERNANCE: { min_pillar_score: 12.5"
        self.assertIn(gov_anchor, text)
        gov_line_start = text.index(gov_anchor)
        gov_line_end = text.index("\n", gov_line_start)
        gov_line = text[gov_line_start:gov_line_end]
        self.assertNotIn("min_dim_raw", gov_line)

    def test_attestation_raw_below_1_5_does_not_block_in_isolation(self):
        from harness.detectors.llmguard.scoring import (
            EvidencePack,
            grade_attestation,
        )
        # Low attestation raw alone does not trigger per-dim bind-block.
        pack = EvidencePack(dpa_signed=True, policy_text_uri="s3://x.pdf")
        result = grade_attestation(pack, substrate_raw=0.0)
        self.assertEqual(result.raw, 1.0)


if __name__ == "__main__":
    unittest.main()
