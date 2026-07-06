#!/usr/bin/env python3
"""BEAAA-152 Phase 1 pipeline rehearsal — no API spend.

Loads the two committed Phase 1 bin yamls, generates synthetic per-probe
pass-rates calibrated against priors from existing scans, runs them through
aggregate_to_measurements + compute_t4_sentinel, and writes a sample
handback packet to artifacts/beaaa-152/. Proves the WS-B integration works
end-to-end before any paid runs.

Usage:
    python3 scripts/beaaa152_phase1_rehearsal.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scanners import promptfoo_runner as pf  # noqa: E402

MAPPINGS_DIR = REPO / "rubric" / "probe_mappings"
ARTIFACT_DIR = REPO / "artifacts" / "beaaa-152"

# Synthetic per-probe pass-rates anchored on existing leaderboard priors
# (BEAAA-130 measured Llama-3.3-70B at P1.1=87%, P3.1=12%, P3.2=20%, P3.3=80%).
# A category-proxy run on Haiku 4.5 / gpt-4o-mini will land in a similar
# band on hallucination, weaker on jailbreak resistance — these synthetic
# numbers are illustrative for the rehearsal, not predictions.
SYNTHETIC_PASS_RATES = {
    # HaluEval — base model gets factual QA mostly right (high P1.1 prior),
    # struggles more on long-form summarization fidelity.
    "halueval.qa": 0.85,
    "halueval.dialogue": 0.78,
    "halueval.summarization.factuality": 0.72,
    "halueval.summarization.citation_integrity": 0.55,
    "halueval.general": 0.68,
    # JailbreakBench — base model is generally jailbreakable on the
    # harder categories; medical/legal/financial gates fail more often
    # than the simpler economic_harm row.
    "jbb.privacy.exfil": 0.65,
    "jbb.privacy.injection": 0.20,
    "jbb.fraud_deception": 0.18,
    "jbb.expert_advice": 0.12,
    "jbb.malware_hacking": 0.30,
    "jbb.economic_harm": 0.45,
    "jbb.disinformation": 0.25,
    "jbb.harassment_discrimination": 0.50,
}


def synth_parsed(probe_id: str, *, n_items: int = 100) -> dict[str, float]:
    rate = SYNTHETIC_PASS_RATES[probe_id]
    n_passed = int(round(rate * n_items))
    return {
        "n_items": n_items,
        "n_passed": n_passed,
        "pass_rate": n_passed / n_items,
    }


def rehearse_pack(yaml_name: str, *, base_model: str) -> dict:
    mapping = pf.ProbeMappingFile.load(MAPPINGS_DIR / yaml_name)
    parsed = {p.probe_id: synth_parsed(p.probe_id) for p in mapping.probes}
    rows = pf.aggregate_to_measurements(
        parsed, mapping,
        model_id=f"category-proxy:{base_model}",
        base_model=base_model,
    )
    sentinel = pf.compute_t4_sentinel(rows)
    return {
        "pack": mapping.benchmark,
        "version": mapping.version,
        "base_model": base_model,
        "binding": mapping.binding,
        "c2_head_active": mapping.c2_head_active,
        "rows": rows,
        "t4_sentinel": sentinel,
        "n_probes": len(mapping.probes),
        "n_synthetic_items_per_probe": 100,
    }


def render_packet(results: list[dict]) -> dict:
    """BEAAA-150 handback-style packet: per-pack rows + aggregate sentinel."""
    all_rows = []
    for r in results:
        all_rows.extend(r["rows"])
    aggregate_sentinel = pf.compute_t4_sentinel(all_rows)
    return {
        "issue": "BEAAA-152",
        "phase": 1,
        "rehearsal": True,
        "spend_usd": 0.00,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "spec_refs": {
            "ws_b_commit": "6a30794",
            "phase1_bins_commit": "6645058",
            "uw_inclusion_gate": "BEAAA-153 #comment-89df5af9",
            "uw_review_pass": "BEAAA-152 #comment-f5d6d0e1",
            "phase1_doc": "BEAAA-152 #document-wsd-bin-draft (rev 2)",
            "pricing_basis": "BEAAA-148 §7 (T4 baseline 0.30, ±30% sentinel)",
        },
        "packs": [
            {
                "pack": r["pack"],
                "version": r["version"],
                "base_model": r["base_model"],
                "c2_head_active": r["c2_head_active"],
                "n_probes": r["n_probes"],
                "n_synthetic_items_per_probe": r["n_synthetic_items_per_probe"],
                "t4_sentinel": r["t4_sentinel"],
                "dim_summary": [
                    {
                        "dim_id": row["dim_id"],
                        "pass_rate": round(row["pass_rate"], 4),
                        "raw": row["raw"],
                        "raw_for_aggregate": row["raw_for_aggregate"],
                        "applied_to_aggregate": row["applied_to_aggregate"],
                        "categories": row["categories"],
                        "trigger_ids": row["trigger_ids"],
                        "provenance_label": row["provenance_label"],
                    }
                    for row in r["rows"]
                ],
            }
            for r in results
        ],
        "aggregate_t4_sentinel": aggregate_sentinel,
    }


def main() -> int:
    base_model = "haiku-4.5"
    results = [
        rehearse_pack("halueval.yaml", base_model=base_model),
        rehearse_pack("jailbreakbench.yaml", base_model=base_model),
    ]
    packet = render_packet(results)

    # Write full sample handback packet + a compact summary
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    full_path = ARTIFACT_DIR / "phase1_rehearsal_packet.json"
    full_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n")
    print(f"wrote {full_path.relative_to(REPO)}")

    # Print the WS-B-touching evidence in human form
    print("\n=== Phase 1 rehearsal — WS-B integration evidence ===")
    print(f"spend_usd: ${packet['spend_usd']:.2f}  (synthetic, no API calls)")
    for pack in packet["packs"]:
        print(f"\n[{pack['pack']} v{pack['version']}, c2_head_active={pack['c2_head_active']}]")
        for d in pack["dim_summary"]:
            agg = "in_aggregate" if d["applied_to_aggregate"] else "ZERO_ROLLUP"
            cats = ",".join(d["categories"]) or "-"
            print(f"  {d['dim_id']}: pass_rate={d['pass_rate']:.3f} "
                  f"raw={d['raw']:.1f} raw_agg={d['raw_for_aggregate']:.1f} "
                  f"[{agg}] cats={cats} prov={d['provenance_label']}")
        s = pack["t4_sentinel"]
        if s["T4_aggregate_freq"] is not None:
            print(f"  T4_sentinel: freq={s['T4_aggregate_freq']:.3f} "
                  f"delta={s['delta_vs_baseline']:+.3f} "
                  f"breached={s['sentinel_breached']}")
        else:
            print("  T4_sentinel: no T4 rows")
    s = packet["aggregate_t4_sentinel"]
    print(f"\nAggregate T4 sentinel across both packs: "
          f"freq={s['T4_aggregate_freq']:.3f} "
          f"delta={s['delta_vs_baseline']:+.3f} "
          f"breached={s['sentinel_breached']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
