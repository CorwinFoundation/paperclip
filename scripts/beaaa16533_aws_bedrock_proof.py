#!/usr/bin/env python3
"""BEAAA-16533 AWS Bedrock/CloudWatch next-provider cross-provider proof.

Extends the BEAAA-16490 production-path proof with AWS Bedrock/CloudWatch as a
third, distinct provider stack. Ingests the AWS fixture for one synthetic deployed
agent, runs the UNCHANGED continuity decision engine over each scenario (AWS
ingested alongside each existing independent stack), verifies each emitted bundle
is machine-verifiable (bound hash + re-derivable decision via verify_bundle /
recompute_decision), and runs the complete scenario set five times to prove the
serialized decisions and bundle hashes are identical across all five runs
(Delta 0).

Reuses the same modules the runtime path uses; no BackBond Score / rubric / band-
cut / contract surface is touched. Rollback = delete the isolated BEAAA-16533
files (this proof, the AWS fixture, the AWS test).

Usage:
    python3 scripts/beaaa16533_aws_bedrock_proof.py
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
    / "synthetic_agent_aws_bedrock.json"
)
OUT = REPO / "artifacts" / "beaaa-16533" / "aws_bedrock_cross_provider_evidence.json"

N_RUNS = 5


def _run_once(data, engine):
    """One full pass over every scenario; returns per-scenario result rows."""
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
        provider_verdicts = [
            {
                "source_provider_id": s.provenance.get("source_provider_id"),
                "provider_native_determination": s.provenance.get(
                    "provider_native_determination"
                ),
                "backbond_signal": s.signal,
            }
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
                "bundle_sha256": decision.bundle_sha256,
                "bundle": bundle,
            }
        )
    return results, all_ok


def main() -> int:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    policy = ContinuityPolicy.load()
    engine = ContinuityDecisionEngine(policy=policy)

    # Primary pass (full evidence rows).
    results, all_ok = _run_once(data, engine)
    for row in results:
        status = "OK" if (
            row["band_ok"]
            and row["decision_ok"]
            and row["bundle_verified"]
            and row["recompute_hash_matches"]
        ) else "FAIL"
        print(
            f"[{status}] {row['scenario']:40s} -> {row['resolved_band']:11s} "
            f"({row['resolved_decision']})"
        )

    # N=5 determinism: re-run the full set and compare per-scenario decision +
    # bundle hash across all runs. Delta 0 required.
    print(f"\nN={N_RUNS} determinism (serialized decision + bundle hash):")
    per_run_signatures = []
    for _ in range(N_RUNS):
        run_results, _ = _run_once(data, engine)
        signature = {
            r["scenario"]: (r["resolved_band"], r["resolved_decision"], r["bundle_sha256"])
            for r in run_results
        }
        per_run_signatures.append(signature)

    n5_table = []
    n5_delta_zero = True
    for name in (r["scenario"] for r in results):
        hashes = {sig[name][2] for sig in per_run_signatures}
        bands = {sig[name][0] for sig in per_run_signatures}
        decisions = {sig[name][1] for sig in per_run_signatures}
        identical = len(hashes) == 1 and len(bands) == 1 and len(decisions) == 1
        n5_delta_zero = n5_delta_zero and identical
        row = {
            "scenario": name,
            "band": next(iter(bands)),
            "decision": next(iter(decisions)),
            "bundle_sha256": next(iter(hashes)),
            "distinct_hashes_across_runs": len(hashes),
            "identical_across_runs": identical,
        }
        n5_table.append(row)
        print(
            f"  [{'OK' if identical else 'DRIFT'}] {name:40s} "
            f"distinct_hashes={len(hashes)} sha={next(iter(hashes))[:16]}..."
        )

    evidence = {
        "issue": "BEAAA-16533",
        "builds_on": "BEAAA-16490",
        "engine_version": DETERMINATION_ENGINE_VERSION,
        "policy_version": policy.version,
        "normalizer_version": "provider-normalizer@1.0.0",
        "scanner_version": "v0.5.0",
        "corpus_version": "0.1.0",
        "agent_id": data["agent_id"],
        "agent_version": data["agent_version"],
        "third_provider": data.get("provider_c_profile"),
        "n_runs": N_RUNS,
        "n5_delta_zero": n5_delta_zero,
        "all_scenarios_passed": all_ok,
        "n5_hash_table": n5_table,
        "scenarios": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nwrote evidence bundle -> {OUT.relative_to(REPO)}")
    print(f"all_scenarios_passed = {all_ok}")
    print(f"n5_delta_zero = {n5_delta_zero}")
    return 0 if (all_ok and n5_delta_zero) else 1


if __name__ == "__main__":
    raise SystemExit(main())
