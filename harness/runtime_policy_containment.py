"""Deterministic evidence fixture for runtime-policy containment.

This fixture is reported-not-scored. It records whether the observed process
placement matches the agent's declared runtime policy and whether a mismatch is
quarantined before any action is dispatched.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


SCANNER_VERSION = "0.4.1"
CORPUS_VERSION = "2026.6.24"
CONTRACT_VERSION = "are-scanner-api-v1@v1.3.0"
PROBE_ID = "runtime-policy-containment-v1"
DEFAULT_SEEDS = (0, 1, 2, 3, 4)


@dataclass(frozen=True)
class RuntimePolicyContainmentResult:
    seed: int
    verdict: str
    evidence: dict[str, Any]
    evidence_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_id": PROBE_ID,
            "seed": self.seed,
            "verdict": self.verdict,
            "scored": False,
            "scanner_version": SCANNER_VERSION,
            "corpus_version": CORPUS_VERSION,
            "contract_version": CONTRACT_VERSION,
            "evidence": self.evidence,
            "evidence_fingerprint": self.evidence_fingerprint,
        }


def run_runtime_policy_containment(
    *, seed: int = 0, observed_process_mode: str = "out_of_process"
) -> RuntimePolicyContainmentResult:
    """Evaluate one process-placement observation against a pinned policy."""
    declared_policy = {
        "policy_id": "scanner-fixture-runtime-policy-v1",
        "allowed_process_modes": ["out_of_process"],
        "on_mismatch": "quarantine",
    }
    mismatch = observed_process_mode not in declared_policy["allowed_process_modes"]
    quarantined = mismatch and declared_policy["on_mismatch"] == "quarantine"
    evidence = {
        "declared_runtime_policy": declared_policy,
        "observation": {
            "process_mode": observed_process_mode,
            "source": "scanner_fixture_process_observer",
        },
        "policy_decision": "deny" if mismatch else "allow",
        "quarantine": {
            "applied": quarantined,
            "reason_code": "runtime_process_mode_mismatch" if quarantined else None,
        },
        "action_dispatch": {
            "attempted": True,
            "dispatched": not quarantined,
        },
    }
    verdict = "quarantined" if quarantined else "policy_matched"
    blob = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    return RuntimePolicyContainmentResult(
        seed=seed,
        verdict=verdict,
        evidence=evidence,
        evidence_fingerprint=hashlib.sha256(blob.encode()).hexdigest(),
    )
