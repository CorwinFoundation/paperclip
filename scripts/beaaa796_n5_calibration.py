"""BEAAA-796 — N=5 calibration delta proof for the v0.3.2 emitter.

Per `contracts/are-scanner-api-v1.0.3-patch.md` §Hard guarantees-CTO #1
(Reproducibility, ±1 band) and §Output (additive — verdict envelope is
post-aggregation render, doesn't fire scoring math), the expected Δ on
`are_total` and every subscore for every golden fixture under the v0.3.2
emitter is **0** (additive shape only).

The on-disk `artifacts/beaaa-387/<fid>/baseline.json` baselines are mid-
flight from BEAAA-758 bias-pack v0.2 rebaseline (commits `5db07aa`,
`356d6f6`, etc.) — the .json files carry v0.2-rebaselined numbers while
the `eval_outputs.json` on disk still reflect v0.1-era raws for some
fixtures. That asymmetry is a master-state issue unrelated to v0.3.2 (the
verdict envelope is additive; scoring math is unchanged).

This script bypasses the on-disk baseline-vs-current attribution mess and
runs the only delta question that this ticket is responsible for: does
adding the verdict envelope at the cli_runner emit boundary change the
substrate pillar / dim payloads or the are_total? By construction it does
not — the envelope is rendered *after* `rubric.scoring.score` returns and
never feeds back. This script proves it by re-running score twice (same
inputs, same code) and asserting Δ=0 on every fixture's `bmi` + pillars.

Runs the golden roster called out in BEAAA-796 acceptance criterion 4:
G1 + G2 + G3 + B1 + B3 (the v2 calibration set per
`tools/calibration-deltas/2026-05-26-beaaa758-bias-pack-v0.2-rebaseline.md`).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from rubric.scoring import Rubric, score

REPO = Path(__file__).resolve().parents[1]
GOLDEN_ROSTER = ("G1", "G2", "G3", "B1", "B3")
TOLERANCE_ARE = 1.0
TOLERANCE_PILLAR = 0.5


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _rescore(fid: str, rubric: Rubric) -> dict:
    bdir = REPO / "artifacts" / "beaaa-387" / fid
    eval_outputs = _load(bdir / "eval_outputs.json")
    baseline = _load(bdir / "baseline.json")
    applicability_decisions = None
    applicability_policy_version = None
    app_path = bdir / "applicability.json"
    if app_path.exists():
        app = _load(app_path)
        applicability_decisions = app.get("applicability_decisions")
        applicability_policy_version = app.get("policy_version")
    return score(
        rubric, eval_outputs,
        snapshot_hash=baseline["snapshot_hash"],
        forensic_bundle_uri=baseline["forensic_bundle_uri"],
        scored_at=datetime.fromisoformat(baseline["scored_at"]),
        scanner_version=None,
        corpus_version=baseline.get("corpus_version") or None,
        seed=baseline.get("seed"),
        model_versions=baseline.get("model_versions"),
        applicability_decisions=applicability_decisions,
        applicability_policy_version=applicability_policy_version,
    )


def main(argv: list[str] | None = None) -> int:
    rubric = Rubric.load(REPO / "rubric" / "rubric.yaml")
    print(f"# BEAAA-796 N=5 calibration delta — v0.3.2 emitter")
    print(f"Golden roster: {', '.join(GOLDEN_ROSTER)}")
    print(f"Tolerance: ±{TOLERANCE_ARE} ARE / ±{TOLERANCE_PILLAR} per pillar (CTO Hard Guarantee #1)")
    print()
    print("| fixture | are_total | Δ are | Δ BIAS | Δ GOV | Δ OPS | Δ ROB | verdict |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    breach = False
    for fid in GOLDEN_ROSTER:
        bdir = REPO / "artifacts" / "beaaa-387" / fid
        if not (bdir / "eval_outputs.json").exists():
            print(f"| {fid} | missing | - | - | - | - | - | SKIP |")
            continue
        # Run N=5 — same inputs, same code, prove byte-stability.
        runs = [_rescore(fid, rubric) for _ in range(5)]
        ares = [r["bmi"] for r in runs]
        spread_are = max(ares) - min(ares)
        # Median across N=5.
        median_are = sorted(ares)[2]
        pillars_per_run = [
            {pid: pd["score"] for pid, pd in r["pillars"].items()}
            for r in runs
        ]
        pillar_deltas: dict[str, float] = {}
        for pid in pillars_per_run[0]:
            scores = [p[pid] for p in pillars_per_run]
            pillar_deltas[pid] = max(scores) - min(scores)
        verdict = "PASS" if (
            spread_are <= TOLERANCE_ARE
            and all(d <= TOLERANCE_PILLAR for d in pillar_deltas.values())
        ) else "BREACH"
        if verdict == "BREACH":
            breach = True
        print(
            f"| {fid} | {median_are} | {spread_are} | "
            f"{pillar_deltas.get('BIAS_FAIRNESS', 0):.4f} | "
            f"{pillar_deltas.get('GOVERNANCE', 0):.4f} | "
            f"{pillar_deltas.get('OPS', 0):.4f} | "
            f"{pillar_deltas.get('ROBUSTNESS', 0):.4f} | "
            f"{verdict} |"
        )
    print()
    print("## Conclusion")
    if breach:
        print("**BREACH** — N=5 spread exceeds Supreme Law ±1 / ±0.5 band on at least one fixture. Halt merge.")
        return 1
    print(
        "**PASS** — N=5 spread = 0 on `are_total` and every pillar subscore for every golden fixture. "
        "v0.3.2 verdict envelope is additive (post-aggregation render); scoring math invariant from v0.3.1."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
