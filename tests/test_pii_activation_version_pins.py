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

    def test_rubric_at_1_1_0(self):
        self.assertEqual(_read_first_version(RUBRIC), "1.1.0")

    def test_applicability_at_0_1_2(self):
        self.assertEqual(_read_first_version(APPLICABILITY), "0.1.2")

    def test_scanner_version_at_0_4_0(self):
        self.assertEqual(SCANNER_VERSION, "0.4.0")

    def test_applicability_anchors_rubric_1_1_0(self):
        text = APPLICABILITY.read_text(encoding="utf-8")
        self.assertIn('applies_to_rubric: "1.1.0"', text)

    def test_active_dim_count_is_25(self):
        text = RUBRIC.read_text(encoding="utf-8")
        self.assertIn(
            "Total dim count check: 6 + 6 + 6 + 7 = 25 active dims",
            text,
            "rubric.yaml total-dim-count comment must reflect 25 active dims "
            "(6 OPS + 6 BIAS_FAIRNESS + 6 ROBUSTNESS + 7 GOVERNANCE)",
        )

    def test_two_new_dim_ids_present(self):
        text = RUBRIC.read_text(encoding="utf-8")
        self.assertIn('"ROBUSTNESS.PII.SCAN_TIME_DETECTION"', text)
        self.assertIn('"GOVERNANCE.PII.HANDLING_ATTESTATION"', text)


if __name__ == "__main__":
    unittest.main()
