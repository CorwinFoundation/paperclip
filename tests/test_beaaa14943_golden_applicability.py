"""Regression for trusted golden applicability sidecars (BEAAA-14943)."""

from __future__ import annotations

import json
from pathlib import Path

from harness.applicability import applicability_record, load_policy


REPO = Path(__file__).resolve().parents[1]
GOLDENS = REPO / "artifacts" / "beaaa-387"
ROSTER = ("G1", "G2", "G3", "M1", "M2", "M3", "B1", "B3")
ALWAYS_APPLICABLE_PII = (
    "GOVERNANCE.PII.HANDLING_ATTESTATION",
    "ROBUSTNESS.PII.SCAN_TIME_DETECTION",
)


def test_canonical_golden_sidecars_derive_always_applicable_pii_from_trusted_flags():
    """F11: golden sidecars cannot waive always-applicable PII dimensions."""

    policy = load_policy()
    for agent_id in ROSTER:
        path = GOLDENS / agent_id / "applicability.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        trusted_path = GOLDENS / agent_id / "trusted_architecture_flags.json"
        trusted = json.loads(trusted_path.read_text(encoding="utf-8"))
        assert trusted["evidence_kind"] == "scanner_observed_architecture_flags"
        assert trusted["provenance"] == {
            "issue": "BEAAA-14943",
            "source": "frozen_golden_replay",
            "trusted_for": "BACKBOND_TRUSTED_ARCHITECTURE_FLAGS_JSON",
        }
        assert trusted["architecture_flags"] == payload["architecture_flags"]
        derived = applicability_record(policy, trusted["architecture_flags"])

        for dim_id, decision in payload["applicability_decisions"].items():
            if decision["fires"] is False:
                engine_decision = derived["applicability_decisions"][dim_id]
                assert engine_decision["fires"] is False
                assert decision["na_justification_key"] == engine_decision["na_justification_key"]

        for dim_id in ALWAYS_APPLICABLE_PII:
            decision = payload["applicability_decisions"][dim_id]
            assert derived["applicability_decisions"][dim_id]["fires"] is True
            assert decision == derived["applicability_decisions"][dim_id]
            assert dim_id in payload["measured_dims"]
            assert dim_id not in payload["na_dims"]
