#!/usr/bin/env python3
"""BEAAA-24454 N=5 golden calibration delta, seeds 0-4, medians and spreads."""
import json, sys, statistics
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from scripts import beaaa14423_n5_calibration as base
from rubric.scoring import Rubric
PILLARS = ("GOVERNANCE","ROBUSTNESS","BIAS_FAIRNESS","OPS")

rubric = Rubric.load(base.RUBRIC_PATH)
out = {}
pinned = {}
for fd in sorted(p for p in base.FIXTURES.iterdir() if p.is_dir()):
    agent = json.loads((fd/"agent.json").read_text())
    if agent.get("status") != "live":
        continue
    exp = json.loads((fd/"expected_score.json").read_text())
    pinned[fd.name] = exp.get("seed", None)
    runs = [base._score_fixture(rubric, fd.name, s) for s in range(5)]
    tot = [int(r["bmi"]) for r in runs]
    rec = {"expected_bmi": int(exp["bmi"]),
           "totals_seed0_4": tot,
           "median_total": statistics.median(tot),
           "spread_total": max(tot)-min(tot),
           "delta_vs_expected": statistics.median(tot)-int(exp["bmi"]),
           "pillars": {}}
    for p in PILLARS:
        vals=[base._pillar(r,p) for r in runs]
        rec["pillars"][p]={"values":vals,"median":statistics.median(vals),
                           "spread":max(vals)-min(vals),
                           "delta_vs_expected":statistics.median(vals)-float(exp["pillars"][p]["score"])}
    out[fd.name]=rec
print(json.dumps({"pinned_seeds":pinned,"fixtures":out}, indent=1, sort_keys=True))
