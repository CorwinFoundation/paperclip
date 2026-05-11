"""BEAAA-198 Track 2 Phase 4: BEAAA-182 adapter migration tests.

Asserts the LawConnect Atticus + AI Legal Assistant scan adapters declare
architecture_flags conformant to the rubric/applicability_policy.yaml
contract, and that the adapter's hard-coded N/A blocks have been retired in
favor of policy-sourced decisions.

These tests run without network — they import the adapter modules and only
inspect the module-level ARCHITECTURE_FLAGS dict against the live policy.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.applicability import decide, load_policy  # noqa: E402
from scripts import scan_lawconnect_ailawyer, scan_lawconnect_atticus  # noqa: E402


class AtticusAdapterTests(unittest.TestCase):
    def test_arch_flags_present(self):
        flags = scan_lawconnect_atticus.ARCHITECTURE_FLAGS
        # All 8 declared, all bool — applicability.decide() will raise if not.
        policy = load_policy()
        d = decide(policy, flags)
        # Atticus is consumer-chat: P3.1 fires, P3.2/P3.3 N/A.
        self.assertTrue(d["P3.1"].fires)
        self.assertFalse(d["P3.2"].fires)
        self.assertFalse(d["P3.3"].fires)

    def test_arch_flags_match_documented_baseline(self):
        # BEAAA-182 baseline: matter-summary stage, no tool/RAG/system-prompt.
        # Every flag is False by design. If a future architecture change makes
        # any flag True, this test must be updated alongside.
        flags = scan_lawconnect_atticus.ARCHITECTURE_FLAGS
        for k, v in flags.items():
            self.assertFalse(v, f"flag {k} unexpectedly True for Atticus baseline")

    def test_na_justification_keys_resolve(self):
        policy = load_policy()
        flags = scan_lawconnect_atticus.ARCHITECTURE_FLAGS
        d = decide(policy, flags)
        # The keys the adapter will emit MUST be present in policy strings.
        self.assertEqual(
            d["P3.2"].na_justification_key,
            "P3.2.no_external_content_channel",
        )
        self.assertEqual(
            d["P3.3"].na_justification_key,
            "P3.3.no_secret_bearing_surface",
        )
        # Justifications byte-identical to policy.
        self.assertEqual(
            d["P3.2"].na_justification,
            policy.na_justification_strings["P3.2.no_external_content_channel"].strip(),
        )


class AILawyerAdapterTests(unittest.TestCase):
    def test_arch_flags_present(self):
        flags = scan_lawconnect_ailawyer.ARCHITECTURE_FLAGS
        policy = load_policy()
        d = decide(policy, flags)
        self.assertTrue(d["P3.1"].fires)
        self.assertFalse(d["P3.2"].fires)
        self.assertFalse(d["P3.3"].fires)

    def test_arch_flags_match_documented_baseline(self):
        flags = scan_lawconnect_ailawyer.ARCHITECTURE_FLAGS
        for k, v in flags.items():
            self.assertFalse(v, f"flag {k} unexpectedly True for AI Legal Assistant baseline")

    def test_na_justification_keys_resolve(self):
        policy = load_policy()
        flags = scan_lawconnect_ailawyer.ARCHITECTURE_FLAGS
        d = decide(policy, flags)
        self.assertEqual(
            d["P3.3"].na_justification_key,
            "P3.3.no_secret_bearing_surface",
        )
        self.assertEqual(
            d["P3.3"].na_justification,
            policy.na_justification_strings["P3.3.no_secret_bearing_surface"].strip(),
        )


class HardCodedNALiteralRetiredTests(unittest.TestCase):
    """Regression guard: the pre-Track-2 hard-coded `"reason": "no tool use ..."`
    literals must be gone. If they reappear, the adapter has regressed."""

    def test_atticus_no_hard_coded_p32_reason(self):
        src = Path(scan_lawconnect_atticus.__file__).read_text()
        self.assertNotIn(
            '"reason": "no tool use / external content ingestion"',
            src,
            "Atticus adapter regressed to hard-coded P3.2 reason — should source from policy",
        )

    def test_atticus_no_hard_coded_p33_reason(self):
        src = Path(scan_lawconnect_atticus.__file__).read_text()
        self.assertNotIn(
            '"reason": "no exposed system prompt or credentials"',
            src,
            "Atticus adapter regressed to hard-coded P3.3 reason — should source from policy",
        )

    def test_ailawyer_no_hard_coded_p32_reason(self):
        src = Path(scan_lawconnect_ailawyer.__file__).read_text()
        self.assertNotIn(
            '"reason": "no tool use / external content"',
            src,
            "AI Legal Assistant adapter regressed to hard-coded P3.2 reason",
        )

    def test_ailawyer_no_hard_coded_p33_reason(self):
        src = Path(scan_lawconnect_ailawyer.__file__).read_text()
        self.assertNotIn(
            '"reason": "no exposed system prompt"',
            src,
            "AI Legal Assistant adapter regressed to hard-coded P3.3 reason",
        )

    def test_atticus_imports_applicability(self):
        src = Path(scan_lawconnect_atticus.__file__).read_text()
        self.assertIn("from harness import applicability", src)

    def test_ailawyer_imports_applicability(self):
        src = Path(scan_lawconnect_ailawyer.__file__).read_text()
        self.assertIn("from harness import applicability", src)


if __name__ == "__main__":
    unittest.main()
