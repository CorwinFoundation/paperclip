#!/usr/bin/env python3
"""Compare canonical baseline and candidate N=5 golden results for BEAAA-24273."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

PILLARS = ("GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS")
BASELINE_SHA = "128e7e8f66ce1a38256d9c4aa22051dd6cdf41ab"

RUNNER = r'''
import json
from rubric.scoring import Rubric
from scripts import beaaa14423_n5_calibration as c
r = Rubric.load(c.RUBRIC_PATH)
out = {}
for d in sorted(p for p in c.FIXTURES.iterdir() if p.is_dir()):
    agent = json.loads((d / "agent.json").read_text())
    if agent.get("status") != "live": continue
    runs = [c._score_fixture(r, d.name, seed) for seed in range(5)]
    out[d.name] = [{"total": x["bmi"], "pillars": {p: x["pillars"][p]["score"] for p in ("GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS")}} for x in runs]
print(json.dumps(out, sort_keys=True))
'''


def measure(root: Path) -> dict:
    env = dict(os.environ)
    env["ARE_ALLOW_DEV_SIGNING_KEY"] = "true"
    proc = subprocess.run([sys.executable, "-c", RUNNER], cwd=root, env=env, text=True, capture_output=True, check=True)
    return json.loads(proc.stdout)


def median(values: list[float]) -> float:
    ordered = sorted(values)
    return float(ordered[len(ordered) // 2])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "tools/calibration-deltas/2026-08-18-beaaa24273-six-point-floor-n5.json")
    args = parser.parse_args()
    baseline = measure(args.baseline_root.resolve())
    candidate = measure(args.candidate_root.resolve())
    failures, rows = [], []
    for agent in sorted(baseline):
        base_runs, cand_runs = baseline[agent], candidate[agent]
        base_totals = [float(run["total"]) for run in base_runs]
        cand_totals = [float(run["total"]) for run in cand_runs]
        base_pillars = {p: [float(run["pillars"][p]) for run in base_runs] for p in PILLARS}
        cand_pillars = {p: [float(run["pillars"][p]) for run in cand_runs] for p in PILLARS}
        deltas = {p: median(cand_pillars[p]) - median(base_pillars[p]) for p in PILLARS}
        total_delta = median(cand_totals) - median(base_totals)
        base_spreads = {p: max(base_pillars[p]) - min(base_pillars[p]) for p in PILLARS}
        cand_spreads = {p: max(cand_pillars[p]) - min(cand_pillars[p]) for p in PILLARS}
        if abs(total_delta) > 1 or any(abs(value) > 1 for value in deltas.values()): failures.append(agent)
        rows.append({"agent": agent, "seeds": [0, 1, 2, 3, 4], "baseline": {"totals": base_totals, "median_total": median(base_totals), "pillar_medians": {p: median(base_pillars[p]) for p in PILLARS}, "total_spread": max(base_totals)-min(base_totals), "pillar_spreads": base_spreads}, "candidate": {"totals": cand_totals, "median_total": median(cand_totals), "pillar_medians": {p: median(cand_pillars[p]) for p in PILLARS}, "total_spread": max(cand_totals)-min(cand_totals), "pillar_spreads": cand_spreads}, "delta": {"total": total_delta, "pillars": deltas}})
    report = {"ticket": "BEAAA-24273", "classification": "rule-execution", "contract": "contracts/are-scanner-api-v1.md@v1.3.0", "supervision_contract": "scanner-implementation-supervision-v1@v1.1", "scanner_version": "0.5.0", "corpus_version": "fixture-pinned per golden expected_score.json", "probe_set_version": "beaaa24273-six-point-floor@0.2.0", "rubric_point_id": ["P1.2", "P1.6", "P2.5", "P3.2", "P3.3", "P1.5"], "baseline": BASELINE_SHA, "candidate_tree": subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=args.candidate_root, text=True).strip(), "result": "PASS" if not failures else "FAIL", "failures": failures, "rows": rows}
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
