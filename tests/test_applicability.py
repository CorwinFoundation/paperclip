"""BEAAA-198 Track 2 Phase 2: applicability gate tests.

Decision-independent of N/A scoring math (Phase 3 — UW co-sign gated). These
tests only exercise the per-dim fires-vs-N/A decision + the adapter contract
on `architecture_flags`.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.applicability import (  # noqa: E402
    ApplicabilityError,
    DimDecision,
    applicability_record,
    decide,
    load_policy,
)


def _flags(**overrides: bool) -> dict[str, bool]:
    """All-false baseline; overrides flip individual flags to true."""
    base = {
        "has_tool_calling": False,
        "has_rag_retrieval": False,
        "has_document_attachments": False,
        "has_inline_html_rendering": False,
        "has_system_prompt_in_response_channel": False,
        "has_credentials_in_prompt": False,
        "has_honeytoken_canary_planted": False,
        "has_multi_tenant_context": False,
        # P2.2 governance flag (BEAAA-199 Phase 2): default false = P2.2 emits N/A.
        "has_uw_signed_labels": False,
    }
    base.update(overrides)
    return base


class PolicyLoadTests(unittest.TestCase):
    def test_default_policy_loads(self):
        # BEAAA-5269 rebaseline: applicability policy bumped to v0.1.2
        # (P4 N/A justification family added), applies_to_rubric pinned to
        # rubric v1.2.0 (25 active dims).
        p = load_policy()
        self.assertEqual(p.version, "0.1.2")
        self.assertEqual(p.applies_to_rubric, "1.2.0")

    def test_arch_flags_declared(self):
        p = load_policy()
        # 4 P3.2 inputs + 4 P3.3 inputs + 1 P2.2 governance flag.
        self.assertEqual(len(p.architecture_flag_names), 9)
        for name in (
            "has_tool_calling",
            "has_rag_retrieval",
            "has_document_attachments",
            "has_inline_html_rendering",
            "has_system_prompt_in_response_channel",
            "has_credentials_in_prompt",
            "has_honeytoken_canary_planted",
            "has_multi_tenant_context",
            "has_uw_signed_labels",
        ):
            self.assertIn(name, p.architecture_flag_names)

    def test_p32_p33_have_na_justification_keys(self):
        p = load_policy()
        self.assertEqual(
            p.dim_applicability["P3.2"]["na_justification_key"],
            "P3.2.no_external_content_channel",
        )
        self.assertEqual(
            p.dim_applicability["P3.3"]["na_justification_key"],
            "P3.3.no_secret_bearing_surface",
        )

    def test_na_strings_byte_stable_substring(self):
        p = load_policy()
        # Byte-stability check: known anchor substrings must appear verbatim.
        self.assertIn(
            "Indirect-injection attack surface does not exist",
            p.na_justification_strings["P3.2.no_external_content_channel"],
        )
        self.assertIn(
            "Secret-bearing surface does not exist",
            p.na_justification_strings["P3.3.no_secret_bearing_surface"],
        )


class FlagValidationTests(unittest.TestCase):
    def test_missing_flag_raises(self):
        p = load_policy()
        flags = _flags()
        flags.pop("has_tool_calling")
        with self.assertRaisesRegex(ApplicabilityError, "did not provide"):
            decide(p, flags)

    def test_unknown_flag_raises(self):
        p = load_policy()
        flags = _flags()
        flags["has_quantum_entanglement"] = True
        with self.assertRaisesRegex(ApplicabilityError, "unknown architecture_flags"):
            decide(p, flags)

    def test_non_bool_flag_raises(self):
        p = load_policy()
        flags = _flags()
        flags["has_tool_calling"] = "yes"  # type: ignore[assignment]
        with self.assertRaisesRegex(ApplicabilityError, "must be bool"):
            decide(p, flags)


class DecisionTests(unittest.TestCase):
    def test_p31_always_fires(self):
        p = load_policy()
        d = decide(p, _flags())
        self.assertTrue(d["P3.1"].fires)
        self.assertIsNone(d["P3.1"].na_justification)

    def test_consumer_chat_p32_p33_na(self):
        # BEAAA-182 Atticus / LawConnect AI Legal Assistant baseline:
        # consumer-chat, no tool/RAG/attachment, no system-prompt-bearing.
        p = load_policy()
        d = decide(p, _flags())
        self.assertFalse(d["P3.2"].fires)
        self.assertFalse(d["P3.3"].fires)
        self.assertIn(
            "Indirect-injection attack surface does not exist",
            d["P3.2"].na_justification or "",
        )
        self.assertIn(
            "Secret-bearing surface does not exist",
            d["P3.3"].na_justification or "",
        )

    def test_p32_fires_on_tool_calling_alone(self):
        p = load_policy()
        d = decide(p, _flags(has_tool_calling=True))
        self.assertTrue(d["P3.2"].fires)
        self.assertFalse(d["P3.3"].fires)

    def test_p32_fires_on_rag_alone(self):
        p = load_policy()
        d = decide(p, _flags(has_rag_retrieval=True))
        self.assertTrue(d["P3.2"].fires)

    def test_p32_fires_on_attachments_alone(self):
        p = load_policy()
        d = decide(p, _flags(has_document_attachments=True))
        self.assertTrue(d["P3.2"].fires)

    def test_p32_fires_on_inline_html_alone(self):
        p = load_policy()
        d = decide(p, _flags(has_inline_html_rendering=True))
        self.assertTrue(d["P3.2"].fires)

    def test_p33_fires_on_system_prompt_alone(self):
        p = load_policy()
        d = decide(p, _flags(has_system_prompt_in_response_channel=True))
        self.assertTrue(d["P3.3"].fires)
        self.assertFalse(d["P3.2"].fires)

    def test_p33_fires_on_credentials_alone(self):
        p = load_policy()
        d = decide(p, _flags(has_credentials_in_prompt=True))
        self.assertTrue(d["P3.3"].fires)

    def test_p33_fires_on_canary_planted_alone(self):
        p = load_policy()
        d = decide(p, _flags(has_honeytoken_canary_planted=True))
        self.assertTrue(d["P3.3"].fires)

    def test_p33_fires_on_multi_tenant_alone(self):
        p = load_policy()
        d = decide(p, _flags(has_multi_tenant_context=True))
        self.assertTrue(d["P3.3"].fires)

    def test_full_exposure_both_fire(self):
        p = load_policy()
        d = decide(p, _flags(
            has_tool_calling=True,
            has_rag_retrieval=True,
            has_document_attachments=True,
            has_inline_html_rendering=True,
            has_system_prompt_in_response_channel=True,
            has_credentials_in_prompt=True,
            has_honeytoken_canary_planted=True,
            has_multi_tenant_context=True,
        ))
        self.assertTrue(d["P3.1"].fires)
        self.assertTrue(d["P3.2"].fires)
        self.assertTrue(d["P3.3"].fires)


class RecordTests(unittest.TestCase):
    def test_record_shape_matches_adapter_contract(self):
        # adapter_contract in applicability_policy.yaml requires these fields.
        p = load_policy()
        rec = applicability_record(p, _flags())
        for key in (
            "policy_version",
            "applies_to_rubric",
            "architecture_flags",
            "applicability_decisions",
            "na_dims",
            "measured_dims",
        ):
            self.assertIn(key, rec)
        # Consumer-chat baseline -> P3.2 + P3.3 in na_dims, P3.1 in measured_dims.
        self.assertIn("P3.1", rec["measured_dims"])
        self.assertIn("P3.2", rec["na_dims"])
        self.assertIn("P3.3", rec["na_dims"])
        # JSON-serializable end-to-end (evidence bundle requirement).
        json.dumps(rec)

    def test_record_na_justifications_byte_stable(self):
        # Justifications in record == justifications in policy (no mutation).
        p = load_policy()
        rec = applicability_record(p, _flags())
        self.assertEqual(
            rec["applicability_decisions"]["P3.2"]["na_justification"],
            decide(p, _flags())["P3.2"].na_justification,
        )


if __name__ == "__main__":
    unittest.main()
