"""BEAAA-200 Track 4 5/22 milestone — first P4 attestation pilot scored end-to-end.

Loads the synthetic AcmeBot v3 attestation form, runs it through the P4 adapter
and the full ARE pipeline, and emits a complete ScoreResponse JSON + summary.

Usage:
    python3 scripts/scan_p4_pilot.py [--output artifacts/beaaa-200/pilot_v1.json]

Supreme Law compliance:
    - scanner_version, corpus_version (n/a), model_versions (n/a), seed (42 default)
      all stamped on every output.
    - Evidence trail: every P4 dim carries evidence_refs from the attestation form;
      non-P4 dims carry provenance="prior" evidence_refs with a provenance stub.
    - Tamper resistance: rejection path verified in test suite; adapter returns
      empty measurements on signature insufficient, no silent inflation.
    - Reproducibility: same fixture + same seed + same scanner_version = same
      are_total within ±0 (deterministic, no LLM judge on this path).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.p4_attestation_adapter import attestation_to_measurements  # noqa: E402
from harness.pipeline import merge_measurements_with_priors  # noqa: E402
from rubric.scoring import Rubric, score  # noqa: E402
from scanners import SCANNER_VERSION  # noqa: E402

FIXTURE_PATH = REPO / "harness" / "fixtures" / "p4_attestation_pilot_v1.json"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"

# Dims that will use priors (no behavioral scan available for new customer).
# Values are mid-tier neutral stubs representing a new enterprise SaaS customer
# with no existing IS eval history — set to 2.0 (documented but not operationalized).
# P4 dims are overridden entirely by the attestation form.
NEUTRAL_PRIORS: dict[str, float] = {
    "P1.1": 2.0, "P1.2": 2.0, "P1.3": 2.0, "P1.4": 2.0, "P1.5": 2.0, "P1.6": 2.0,
    "P2.1": 2.0, "P2.2": 2.0, "P2.3": 2.0, "P2.4": 2.0, "P2.5": 2.0, "P2.6": 2.0,
    "P3.1": 2.0, "P3.2": 2.0, "P3.3": 2.0,
    # P4 stubs — will be overridden by attestation adapter below
    "P4.1": 2.0, "P4.2": 2.0, "P4.3": 2.0, "P4.4": 2.0, "P4.5": 2.0, "P4.6": 2.0,
}


def _form_sha256(form_path: Path) -> str:
    return hashlib.sha256(form_path.read_bytes()).hexdigest()


def _evidence_uri(dim_id: str, provenance: str) -> str:
    if provenance.startswith("measured:"):
        bench = provenance[len("measured:"):]
        return f"attestation://p4_pilot_v1/{dim_id}?benchmark={bench}"
    return f"prior://neutral_stub/{dim_id}"


def run_pilot(
    fixture_path: Path = FIXTURE_PATH,
    *,
    seed: int = 42,
) -> dict:
    form = json.loads(fixture_path.read_text())
    form_hash = _form_sha256(fixture_path)
    snapshot_hash = f"sha256:{form_hash}"
    forensic_bundle_uri = f"attestation://pilot/beaaa-200/v1?form_sha256={form_hash}"

    # Score the P4 attestation form → emit 6 measurements
    adapter_result = attestation_to_measurements(form)
    if adapter_result.scored.get("rejected"):
        raise RuntimeError(
            f"Pilot form rejected by scorer: "
            f"{adapter_result.scored.get('rejection_reason', {}).get('error')}"
        )

    # Merge P4 adapter measurements into neutral priors (overrides P4.1–P4.6)
    raw_scores, provenance = merge_measurements_with_priors(
        adapter_result.measurements, NEUTRAL_PRIORS
    )

    # Build eval_outputs in the shape score() expects:
    # { dim_id: {"raw": float, "evidence_uri": str} }
    eval_outputs = {
        dim_id: {
            "raw": raw,
            "evidence_uri": _evidence_uri(dim_id, provenance[dim_id]),
        }
        for dim_id, raw in raw_scores.items()
    }

    rubric = Rubric.load(RUBRIC_PATH)
    scored_at = datetime.now(timezone.utc)

    result = score(
        rubric,
        eval_outputs,
        snapshot_hash=snapshot_hash,
        forensic_bundle_uri=forensic_bundle_uri,
        scored_at=scored_at,
        scanner_version=SCANNER_VERSION,
        corpus_version=None,   # n/a — attestation-only scan
        seed=seed,
        model_versions={"primary": "n/a", "judges": []},
    )

    # Annotate with pilot metadata for the UW brief
    result["_pilot_metadata"] = {
        "pilot_id": "beaaa-200-pilot-v1",
        "form_fixture": str(fixture_path.relative_to(REPO)),
        "form_sha256": form_hash,
        "customer_id": form.get("customer_id"),
        "agent_id": form.get("agent_id"),
        "attested_at": form.get("attested_at"),
        "p4_adapter_result": {
            "measurements": adapter_result.measurements,
            "p4_dims": adapter_result.scored.get("dims"),
            "pillar_score": adapter_result.scored.get("pillar_score"),
            "pillar_floor": adapter_result.scored.get("pillar_floor"),
            "bind_eligible_on_pillar": adapter_result.scored.get("bind_eligible_on_pillar"),
        },
        "non_p4_provenance": "neutral_prior_stub_2.0_no_behavioral_scan",
        "seed": seed,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="BEAAA-200 Track 4 pilot scoring run")
    parser.add_argument(
        "--output", default=None,
        help="Write JSON result to this path (default: print to stdout)"
    )
    args = parser.parse_args()

    result = run_pilot()

    # Print human-readable summary
    meta = result["_pilot_metadata"]
    p4_meta = meta["p4_adapter_result"]
    bmi = result.get("bmi") or result.get("are_total")
    band = result.get("band", {})
    print(f"\n=== BEAAA-200 Track 4 Pilot Score (scanner_version={result.get('scanner_version')}) ===")
    print(f"  Customer:        {meta['customer_id']}")
    print(f"  Agent:           {meta['agent_id']}")
    print(f"  Attested at:     {meta['attested_at']}")
    print(f"  Form SHA-256:    {meta['form_sha256'][:16]}...")
    print(f"\n  ARE Total:       {bmi} / 100")
    print(f"  Band:            {band.get('display')} ({band.get('posture')})")
    print(f"\n  P4 Pillar Score: {p4_meta['pillar_score']} / {p4_meta['pillar_floor'] * (100 / 25):.0f}pt max  (floor {p4_meta['pillar_floor']})")
    print(f"  Bind eligible:   {p4_meta['bind_eligible_on_pillar']}")
    print(f"\n  P4 dim breakdown:")
    for m in p4_meta["measurements"]:
        dims_detail = p4_meta.get("p4_dims") or {}
        tier = (dims_detail.get(m["dim_id"]) or {}).get("tier", "?")
        print(f"    {m['dim_id']}: raw={m['raw']}  tier={tier}  benchmark={m['benchmark']}")
    print(f"\n  Non-P4 dims:     {meta['non_p4_provenance']}")
    print(f"  Seed:            {meta['seed']}")
    print(f"  Scanner version: {result.get('scanner_version')}")
    print()

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2, default=str))
        print(f"Result written to {out_path}")
    else:
        print(json.dumps(result, indent=2, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())
