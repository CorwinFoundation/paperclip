"""ADR-0067 §4 N=5 seeds 0..4 checkpoint table (BEAAA-23757 Underwriter input).

Scores all eight baselined golden fixtures at the rubric-1.3.0 / scanner-0.5.0
enforcement checkpoint under seeds 0,1,2,3,4 -- overriding each fixture's
persisted seed -- and reports total + per-pillar spread (max-min) per fixture.

Also records, per fixture, which ScoreResponse fields differ across the five
seeds. That is the load-bearing column: it distinguishes "stable under
resampling" from "seed is not a sampling input on this path".

Run: PYTHONPATH=. python3 artifacts/BEAAA-23752/n5_seeds_0_4_checkpoint.py
"""
import json
from datetime import datetime
from pathlib import Path

from harness.golden_set._trusted_applicability import load_trusted_applicability
from rubric.scoring import Rubric, score
from scanners import SCANNER_VERSION

REPO = Path(__file__).resolve().parents[2]
BASELINES = REPO / "artifacts" / "beaaa-387"
SEEDS = [0, 1, 2, 3, 4]

rubric = Rubric.load(REPO / "rubric" / "rubric.yaml")
manifest = json.loads((BASELINES / "manifest.json").read_text())

rows, all_fields_differing = [], set()
for fid, finfo in sorted(manifest["fixtures"].items()):
    if not finfo.get("baseline_persisted"):
        continue
    bdir = BASELINES / fid
    baseline = json.loads((bdir / "baseline.json").read_text())
    eval_outputs = json.loads((bdir / "eval_outputs.json").read_text())
    decisions = load_trusted_applicability(BASELINES, fid)

    reps = []
    for s in SEEDS:
        reps.append(score(
            rubric, eval_outputs,
            snapshot_hash=baseline["snapshot_hash"],
            forensic_bundle_uri=baseline["forensic_bundle_uri"],
            scored_at=datetime.fromisoformat(baseline["scored_at"]),
            scanner_version=None,
            corpus_version=baseline.get("corpus_version"),
            seed=s,                                  # override persisted seed
            model_versions=baseline.get("model_versions"),
            applicability_decisions=decisions,
            applicability_policy_version=None,
        ))

    totals = [int(r["bmi"]) for r in reps]
    pillars = sorted({p for r in reps for p in r.get("pillars", {})})
    pillar_spread = {}
    for p in pillars:
        vals = [float(r["pillars"][p]["score"]) for r in reps]
        pillar_spread[p] = round(max(vals) - min(vals), 4)

    # Which top-level response fields actually vary across the five seeds?
    differing = sorted({
        k for k in reps[0]
        if len({json.dumps(r.get(k), sort_keys=True, default=str) for r in reps}) > 1
    })
    all_fields_differing |= set(differing)

    rows.append({
        "fixture_id": fid,
        "persisted_seed": baseline.get("seed"),
        "totals_by_seed": dict(zip(map(str, SEEDS), totals)),
        "total_spread": max(totals) - min(totals),
        "pillar_spread": pillar_spread,
        "fields_differing_across_seeds": differing,
    })

out = {
    "issue": "BEAAA-23757",
    "adr": "ADR-0067 §4",
    "candidate": "516078e2ac7a1ffd012f8498533bb27b048277ba",
    "scanner_version": SCANNER_VERSION,
    "rubric_version": rubric.version,
    "seeds": SEEDS,
    "fixtures": rows,
    "max_total_spread": max(r["total_spread"] for r in rows),
    "max_pillar_spread": max(v for r in rows for v in r["pillar_spread"].values()),
    "fields_differing_across_seeds_any_fixture": sorted(all_fields_differing),
}
print(json.dumps(out, indent=2, sort_keys=True))
