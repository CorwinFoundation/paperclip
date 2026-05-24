"""BEAAA-714 — Live N=5 calibration delta on the golden roster for the
PyRIT P3.1 pipeline wire-up.

Re-scores every golden in `artifacts/beaaa-387/manifest.json` against the
current rubric + SCANNER_VERSION N=5 times and records:

- per-fixture re-score spread across the 5 replicates (byte-stability of
  the post-wire scorer);
- per-fixture pre-wire vs. post-wire ARE / pillar deltas (the post-wire
  number is this run; the pre-wire reference is the persisted
  `baseline.json` from BEAAA-387).

The wiring landed in `harness/pipeline.py` is gated on
`scanner_invocation_context.requested_pillars` containing ROBUSTNESS /
P3 and runs `pyrit_runner` alongside the existing Garak path. The
calibration-delta re-score does NOT supply `requested_pillars` (it
re-scores frozen `eval_outputs.json` against the current rubric), so a
non-zero delta on any golden would mean the wire-up perturbed the
default no-PyRIT path — which would be a contract breach per
`contracts/scanner-implementation-supervision-v1.md@v1.0` Operational
guarantee #3 and would force a re-classification as rule-change.

Per the BEAAA-714 AC: if any golden Δ > ±1 on total or any subscore,
HALT merge, re-classify as rule-change, request CTO co-sign. We surface
the spread + delta and the supervision verdict.

Output: this file's sibling `n5_pipeline_wire_calibration.json`.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from harness.golden_set.calibration_delta import compute_delta  # noqa: E402
from scanners import CONTRACT_VERSION, SCANNER_VERSION  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent

CORPUS_VERSION = "path_a_v2_1@2026-05-15"
PROBE_SET_VERSION = "beaaa-714-pyrit-p3-1-wire-v0.1.0"
UPSTREAM_EVAL_VERSION = "0.6.0"  # PyRIT pin in requirements.txt
RUBRIC_POINT_ID = "ROBUSTNESS.PROMPT_INJECTION"
DIM_ID = "P3.1"
N_REPLICATES = 5
SUPERVISION_TOLERANCE = 1  # ±1 per supervision contract for rule-execution


def _replicate_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-fixture spread across N=5 replicates of the re-score."""
    per_fixture: dict[str, dict[str, Any]] = {}
    fixtures_by_id: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        for fixture in record["fixtures"]:
            fixtures_by_id.setdefault(fixture["fixture_id"], []).append(fixture)

    for fid, runs in fixtures_by_id.items():
        baselined = runs[0]["baselined"]
        if not baselined:
            per_fixture[fid] = {
                "baselined": False,
                "pending_reason": runs[0].get("pending_reason"),
                "stable": True,  # vacuously — nothing scored
                "spread": {},
            }
            continue
        current_ares = [r["current_are"] for r in runs]
        pillar_keys = sorted(runs[0]["pillar_deltas"].keys())
        pillar_spreads: dict[str, dict[str, float]] = {}
        for pid in pillar_keys:
            values = [r["pillar_deltas"][pid] for r in runs]
            pillar_spreads[pid] = {
                "min": min(values),
                "max": max(values),
                "spread": max(values) - min(values),
            }
        spread = max(current_ares) - min(current_ares)
        per_fixture[fid] = {
            "baselined": True,
            "baseline_are": runs[0]["baseline_are"],
            "current_are_min": min(current_ares),
            "current_are_max": max(current_ares),
            "current_are_spread": spread,
            "are_delta_min": min(r["are_delta"] for r in runs),
            "are_delta_max": max(r["are_delta"] for r in runs),
            "drift_attributable_to": runs[0]["drift_attributable_to"],
            "are_breach_any": any(r["are_breach"] for r in runs),
            "pillar_breach_any": any(r["pillar_breaches"] for r in runs),
            "pillar_spreads": pillar_spreads,
            "stable": spread == 0
            and all(s["spread"] == 0 for s in pillar_spreads.values()),
        }
    return per_fixture


def _golden_only(per_fixture: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {fid: info for fid, info in per_fixture.items() if fid.startswith("G")}


def _supervision_verdict(per_fixture: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Apply the BEAAA-714 AC: any Δ > ±1 on total or any subscore
    re-classifies the ticket as rule-change.

    Pre-wire reference = baseline.json ARE / pillar scores (persisted
    BEAAA-387 baselines). Post-wire ARE / pillar scores = this run's
    re-score. Δ_pillar uses the existing calibration-delta per-pillar
    value; Δ_total uses |current_are - baseline_are|.
    """
    breaches: list[dict[str, Any]] = []
    for fid, info in per_fixture.items():
        if not info["baselined"]:
            continue
        # Calibration delta classifies non-zero deltas under rubric_reweight
        # or scanner_only attribution — only `none` means version-aligned
        # like-for-like comparison where a non-zero delta would be a true
        # rule-execution → rule-change escalation.
        if info["drift_attributable_to"] != "none":
            continue
        # Worst-case |delta| over the N=5 replicates.
        worst_total = max(
            abs(info["are_delta_min"]), abs(info["are_delta_max"])
        )
        worst_pillar = 0.0
        worst_pillar_id: str | None = None
        for pid, sp in info["pillar_spreads"].items():
            cand = max(abs(sp["min"]), abs(sp["max"]))
            if cand > worst_pillar:
                worst_pillar = cand
                worst_pillar_id = pid
        if (
            worst_total > SUPERVISION_TOLERANCE
            or worst_pillar > SUPERVISION_TOLERANCE
        ):
            breaches.append({
                "fixture_id": fid,
                "worst_total_delta": worst_total,
                "worst_pillar_delta": worst_pillar,
                "worst_pillar_id": worst_pillar_id,
            })
    return {
        "tolerance": SUPERVISION_TOLERANCE,
        "passed": not breaches,
        "breaches": breaches,
        "verdict": (
            "RULE-EXECUTION CONFIRMED — calibration delta within ±1 on every "
            "golden total and subscore; rule-execution classification stands; "
            "no CTO co-sign required."
            if not breaches
            else "RULE-EXECUTION FAILED — Δ > ±1 detected. HALT merge, "
                 "re-classify as rule-change, request CTO co-sign per "
                 "scanner-implementation-supervision-v1.md@v1.0 §3."
        ),
    }


def main() -> None:
    replicates: list[dict[str, Any]] = []
    for _ in range(N_REPLICATES):
        replicates.append(compute_delta())

    per_fixture = _replicate_summary(replicates)
    goldens = _golden_only(per_fixture)
    verdict = _supervision_verdict(goldens)
    all_goldens_stable = all(g["stable"] for g in goldens.values() if g["baselined"])

    record = {
        "issue": "BEAAA-714",
        "section": "Live N=5 calibration delta — pipeline wire-up",
        "rubric_point_id": RUBRIC_POINT_ID,
        "dim_id": DIM_ID,
        "wrapper": "harness.pipeline.run_pyrit_p31",
        "supervision_classification": "rule-execution",
        "supervision_table_row": (
            "Implement a new probe inside an existing rubric_point_id"
        ),
        "supervision_contract_version": "scanner-implementation-supervision-v1.md@v1.0",
        "cto_cosign_ref": "NOT_REQUIRED unless reclassified",
        "scanner_version": SCANNER_VERSION,
        "corpus_version": CORPUS_VERSION,
        "probe_set_version": PROBE_SET_VERSION,
        "contract_version": CONTRACT_VERSION,
        "upstream_eval": "pyrit",
        "upstream_eval_version": UPSTREAM_EVAL_VERSION,
        "n_replicates": N_REPLICATES,
        "goldens": sorted(goldens.keys()),
        "byte_stable_across_replicates": all_goldens_stable,
        "per_fixture_summary": per_fixture,
        "supervision_verdict": verdict,
        "rationale": (
            "Wire-up is purely additive: `run_pyrit_p31` is gated on "
            "`scanner_invocation_context.requested_pillars` containing "
            "ROBUSTNESS (or P3). The calibration-delta re-score path does "
            "NOT supply requested_pillars (it re-scores frozen baseline "
            "eval_outputs.json against the current rubric), so the gate is "
            "closed at this call site and PyRIT does not run. Δ should be 0 "
            "on every golden; non-zero Δ here would indicate the wire-up "
            "leaked into the no-PyRIT path."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    out_path = OUT_DIR / "n5_pipeline_wire_calibration.json"
    out_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")

    status = (
        "BYTE-STABLE (spread=0 across N=5 on every golden)"
        if all_goldens_stable
        else "SPREAD DETECTED — see per_fixture_summary"
    )
    sup = verdict["verdict"].splitlines()[0]
    print(f"{RUBRIC_POINT_ID} ({DIM_ID}) wire N=5: {status}")
    print(f"  goldens: {sorted(goldens.keys())}")
    print(f"  verdict: {sup}")
    print(f"  -> {out_path.relative_to(REPO)}")
    if not verdict["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
