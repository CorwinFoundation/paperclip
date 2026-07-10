"""BEAAA-781 / ADR-0025 — version-pin CI guard for the atomic activation
merge.

Mirrors `test_contract_version_sync.py` semantics for the rubric +
applicability_policy pair that landed in this MINOR additive cycle.
Single fail mode per file, single fix path per file: bump the YAML
version field in lockstep with the SCANNER_VERSION bump.

This guard prevents the silent rollback that
`feedback_close_then_comment_reverts.md` and BEAAA-1721 SOP warned about
— a PATCH that re-touches rubric.yaml between activation and merge must
not silently drop the v1.1.0 bump.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from scanners import SCANNER_VERSION

REPO = Path(__file__).resolve().parents[1]
RUBRIC = REPO / "rubric" / "rubric.yaml"
APPLICABILITY = REPO / "rubric" / "applicability_policy.yaml"

VERSION_LINE = re.compile(r'^version:\s*"?([0-9.]+)"?\s*$', re.MULTILINE)


def _read_first_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = VERSION_LINE.search(text)
    if not m:
        raise AssertionError("no `version:` line in %s" % path)
    return m.group(1)


class PiiActivationVersionPinTests(unittest.TestCase):
    """Pin the rubric + applicability + SCANNER_VERSION trio."""

    def test_rubric_at_1_3_0(self):
        # BEAAA-13388 candidate activation — P3.5 + P3.6 enter the active
        # ROBUSTNESS dim mean. Pillar weights, band cuts, and aggregation
        # shape stay unchanged; BEAAA-13382 is the Underwriter merge gate.
        self.assertEqual(_read_first_version(RUBRIC), "1.3.0")

    def test_applicability_at_0_1_2(self):
        self.assertEqual(_read_first_version(APPLICABILITY), "0.1.2")

    def test_scanner_version_at_0_5_0(self):
        # BEAAA-6190 v0.5 RC stamp. No contract or rubric version
        # bump; this pin tracks the scanner release stamp only.
        self.assertEqual(SCANNER_VERSION, "0.5.0")

    def test_applicability_anchors_rubric_1_2_0_until_beaaa13382_merge_gate(self):
        # BEAAA-13388 does not add architecture flags in Phase A; applicability
        # remains pinned to the last adopted policy until BEAAA-13382 clears.
        text = APPLICABILITY.read_text(encoding="utf-8")
        self.assertIn('applies_to_rubric: "1.2.0"', text)

    def test_active_dim_count_is_27(self):
        text = RUBRIC.read_text(encoding="utf-8")
        self.assertIn(
            "Total dim count check: 6 + 6 + 8 + 7 = 27 active dims",
            text,
            "rubric.yaml total-dim-count comment must reflect 27 active dims "
            "(6 OPS + 6 BIAS_FAIRNESS + 8 ROBUSTNESS + 7 GOVERNANCE-key dims)",
        )

    def test_beaaa13388_dim_ids_present(self):
        text = RUBRIC.read_text(encoding="utf-8")
        self.assertIn('"ROBUSTNESS.PII.SCAN_TIME_DETECTION"', text)
        self.assertIn('"GOVERNANCE.PII.HANDLING_ATTESTATION"', text)
        self.assertIn("id: P3.5", text)
        self.assertIn("id: P3.6", text)


if __name__ == "__main__":
    unittest.main()
