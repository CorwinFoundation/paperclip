#!/usr/bin/env python3
"""BEAAA-16490 cross-provider Passive Monitor production-path proof.

Ingests the two-provider fixture for one synthetic deployed agent, runs the
continuity decision engine over each scenario, verifies each emitted bundle is
machine-verifiable (bound hash + re-derivable decision), and writes the evidence
bundle to ``artifacts/beaaa-16490/cross_provider_continuity_evidence.json``.

This is the reachable, machine-verifiable evidence artifact required by the
acceptance test. It uses the same modules the runtime path uses; no BackBond
Score / rubric / contract surface is touched. Rollback = delete the isolated
BEAAA-16490 files.

Usage:
    python3 scripts/beaaa16490_cross_provider_proof.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from determination import DETERMINATION_ENGINE_VERSION  # noqa: E402
from determination.continuity import (  # noqa: E402
    ContinuityDecisionEngine,
    ContinuityPolicy,
    recompute_decision,
    verify_bundle,
)

FIXTURE = (
    REPO
    / "determination"
    / "fixtures"
    / "cross_provider"
    / "synthetic_agent_two_providers.json"
)
OUT = REPO / "artifacts" / "beaaa-16490" / "cross_provider_continuity_evidence.json"


def main() -> int:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    policy = ContinuityPolicy.load()
    engine = ContinuityDecisionEngine(policy=policy)

    results = []
    all_ok = True
    for name, scenario in data["scenarios"].items():
        decision = engine.decide(
            agent_id=data["agent_id"],
            agent_version=data["agent_version"],
            envelopes=scenario["envelopes"],
            as_of=scenario["as_of"],
        )
        bundle = decision.to_bundle()
        verified = verify_bundle(bundle)
        recomputed = recompute_decision(bundle)

        band_ok = decision.band == scenario["expected_band"]
        decision_ok = decision.decision == scenario["expected_decision"]
        # For the override negative test, prove the provider verdict was present
        # yet did not change the BackBond outcome.
        provider_verdicts = [
            s.provenance.get("provider_native_determination")
            for s in decision.sources
        ]
        ok = band_ok and decision_ok and verified and recomputed["hash_matches"]
        all_ok = all_ok and ok

        results.append(
            {
                "scenario": name,
                "expected_band": scenario["expected_band"],
                "resolved_band": decision.band,
                "resolved_decision": decision.decision,
                "rationale": decision.rationale,
                "band_ok": band_ok,
                "decision_ok": decision_ok,
                "bundle_verified": verified,
                "recompute_hash_matches": recomputed["hash_matches"],
                "eligible_source_count": decision.eligible_source_count,
                "independence": decision.independence,
                "provider_native_determinations_input_only": provider_verdicts,
                "bundle": bundle,
            }
        )
        status = "OK" if ok else "FAIL"
        print(
            f"[{status}] {name:38s} -> {decision.band:11s} "
            f"({decision.decision})"
        )

    evidence = {
        "issue": "BEAAA-16490",
        "engine_version": DETERMINATION_ENGINE_VERSION,
        "policy_version": policy.version,
        "agent_id": data["agent_id"],
        "agent_version": data["agent_version"],
        "all_scenarios_passed": all_ok,
        "scenarios": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nwrote evidence bundle -> {OUT.relative_to(REPO)}")
    print(f"all_scenarios_passed = {all_ok}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
