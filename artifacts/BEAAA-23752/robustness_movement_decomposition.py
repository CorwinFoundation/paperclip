"""Decompose the cross-version ROBUSTNESS movement on the rubric-1.3.0 checkpoint.

BEAAA-23757 Underwriter input. For each baselined golden fixture, attribute the
historical->current ROBUSTNESS delta to specific dimensions, and reconstruct the
pillar score from the Option-C mean-of-active-dims arithmetic to show the
movement is fully explained with no unattributed residual.

Run: PYTHONPATH=. python3 artifacts/BEAAA-23752/robustness_movement_decomposition.py
"""
import json
from datetime import datetime
from pathlib import Path

from harness.golden_set._trusted_applicability import load_trusted_applicability
from rubric.scoring import Rubric, score
from scanners import SCANNER_VERSION

REPO = Path(__file__).resolve().parents[2]
B = REPO / "artifacts" / "beaaa-387"
rubric = Rubric.load(REPO / "rubric" / "rubric.yaml")
manifest = json.loads((B / "manifest.json").read_text())


def _eligible(pillar_score, active_raws, floor):
    """P3 bind eligibility: per-dim raw >= min_dim_raw AND aggregate >= min_pillar_score."""
    min_pillar = floor.get("min_pillar_score")
    min_dim = floor.get("min_dim_raw")
    if min_pillar is None and min_dim is None:
        return None
    agg_ok = min_pillar is None or pillar_score >= min_pillar
    dim_ok = min_dim is None or (bool(active_raws) and min(active_raws.values()) >= min_dim)
    return {"aggregate_ok": agg_ok, "min_dim_ok": dim_ok, "eligible": agg_ok and dim_ok,
            "min_active_raw": min(active_raws.values()) if active_raws else None}


def active(pillar):
    return {k: v.get("raw") for k, v in pillar["dimensions"].items()
            if v.get("applicable") is not False}


rows = []
for fid, finfo in sorted(manifest["fixtures"].items()):
    if not finfo.get("baseline_persisted"):
        continue
    base = json.loads((B / fid / "baseline.json").read_text())
    eo = json.loads((B / fid / "eval_outputs.json").read_text())
    dec = load_trusted_applicability(B, fid)
    cur = score(rubric, eo, snapshot_hash=base["snapshot_hash"],
                forensic_bundle_uri=base["forensic_bundle_uri"],
                scored_at=datetime.fromisoformat(base["scored_at"]), scanner_version=None,
                corpus_version=base.get("corpus_version"), seed=base.get("seed"),
                model_versions=base.get("model_versions"), applicability_decisions=dec,
                applicability_policy_version=None)

    bp, cp = base["pillars"]["ROBUSTNESS"], cur["pillars"]["ROBUSTNESS"]
    floor = (cur.get("pillar_floors") or {}).get("ROBUSTNESS", {})
    ba, ca = active(bp), active(cp)
    added = sorted(set(ca) - set(ba))
    removed = sorted(set(ba) - set(ca))
    moved = sorted(k for k in set(ba) & set(ca) if ba[k] != ca[k])

    # Option C: pillar score = mean(active raws) / 4 * weight
    w = cp.get("weight")
    recon = (sum(ca.values()) / len(ca)) / 4 * w if ca else 0.0
    rows.append({
        "fixture_id": fid,
        "are": {"historical": int(base["bmi"]), "current": int(cur["bmi"]),
                "delta": int(cur["bmi"]) - int(base["bmi"])},
        "robustness": {"historical": bp["score"], "current": cp["score"],
                       "delta": round(cp["score"] - bp["score"], 4), "weight": w},
        "dims_added_since_baseline": {k: ca[k] for k in added},
        "dims_removed_since_baseline": removed,
        "preexisting_dims_whose_raw_moved": {k: [ba[k], ca[k]] for k in moved},
        "active_dim_count": {"historical": len(ba), "current": len(ca)},
        "reconstructed_current_from_mean_of_active": round(recon, 4),
        # emitted pillar scores are rounded to 4dp, so compare at that precision
        "reconstruction_matches_emitted": abs(round(recon, 4) - cp["score"]) < 1e-9,
        "bind_eligibility": {
            "floor_min_pillar_score": floor.get("min_pillar_score"),
            "floor_min_dim_raw": floor.get("min_dim_raw"),
            "historical": _eligible(bp["score"], ba, floor),
            "current": _eligible(cp["score"], ca, floor),
        },
    })

out = {
    "issue": "BEAAA-23757",
    "adr": "ADR-0067",
    "candidate": "516078e2ac7a1ffd012f8498533bb27b048277ba",
    "scanner_version": SCANNER_VERSION,
    "rubric_version": rubric.version,
    "fixtures": rows,
    "any_preexisting_dim_moved": any(r["preexisting_dims_whose_raw_moved"] for r in rows),
    "all_reconstructions_match": all(r["reconstruction_matches_emitted"] for r in rows),
    "bind_eligibility_transitions": [
        {"fixture_id": r["fixture_id"],
         "historical": r["bind_eligibility"]["historical"]["eligible"],
         "current": r["bind_eligibility"]["current"]["eligible"]}
        for r in rows
        if r["bind_eligibility"]["historical"]["eligible"] != r["bind_eligibility"]["current"]["eligible"]
    ],
}
print(json.dumps(out, indent=2, sort_keys=True))
