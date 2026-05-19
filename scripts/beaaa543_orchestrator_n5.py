"""BEAAA-543 — N=5 calibration delta on the locked golden set.

This action adds ``scanners/live_probe_orchestrator.py`` (Prober-side scan
orchestrator skeleton: corpus pin verification, credential resolve/forget,
HOSTED_RUNNER row stamping, IPC streaming).

Classification: rule-execution per ``contracts/scanner-implementation-
supervision-v1.md@v1.0`` — executes the ratified ADR-0009 §4 architecture
with no scoring math change. K-replicate + median-of-K aggregation is split
out to T5 (BEAAA-544, rule-change). Per-scan rate/cost caps are split out
to T9 (BEAAA-545).

The new module is not imported by any offline scoring path; it is invoked
only inside the Prober subprocess that T4 spawns. The expected calibration
result is therefore byte-identical replay (spread=0, delta=0.0000) across
N=5 runs on every locked golden — the strictest form of Supreme-Law ±1.

Mirrors ``scripts/beaaa542_workers_n5.py`` exactly — same code path, same
fixtures, same hash math.
"""
from __future__ import annotations
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.golden_set.persist_baseline import _load_json  # noqa: E402
from rubric.scoring import Rubric, score  # noqa: E402

FIXTURES_DIR = REPO / "tests" / "fixtures" / "golden_agents"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"


def _bytes_hash(obj: dict) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main() -> int:
    rubric = Rubric.load(RUBRIC_PATH)
    fixture_ids = sorted(
        d.name
        for d in FIXTURES_DIR.iterdir()
        if d.is_dir() and (d / "expected_score.json").exists()
    )
    n = 5
    deltas = {}
    overall_in_band = True
    for fid in fixture_ids:
        fdir = FIXTURES_DIR / fid
        eval_outputs = _load_json(fdir / "eval_outputs.json")
        expected = _load_json(fdir / "expected_score.json")
        expected_hash = _bytes_hash(expected)
        per_run = []
        for _ in range(n):
            scored = score(
                rubric=rubric,
                eval_outputs=eval_outputs,
                forensic_bundle_uri=f"https://golden.local/{fid}",
                scored_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                snapshot_hash="N5-CALIBRATION-BEAAA-543",
            )
            per_run.append({
                "are_total": scored["bmi"],
                "pillars": scored["pillars"],
                "hash": _bytes_hash(scored),
            })
        totals = [r["are_total"] for r in per_run]
        spread = max(totals) - min(totals)
        all_byte_identical = len({r["hash"] for r in per_run}) == 1
        expected_total = expected["bmi"]
        delta_vs_expected = max(abs(t - expected_total) for t in totals)
        in_band = (
            spread == 0
            and delta_vs_expected <= 1.0
            and all_byte_identical
        )
        overall_in_band = overall_in_band and in_band
        deltas[fid] = {
            "expected_total": expected_total,
            "expected_hash": expected_hash,
            "observed_totals_n5": totals,
            "spread_n5": spread,
            "byte_identical_replay_n5": all_byte_identical,
            "delta_vs_expected_max": delta_vs_expected,
            "in_band_pm1_and_byte_identical": in_band,
        }
        marker = "OK" if in_band else "FAIL"
        print(
            f"  [{marker}] {fid}: expected={expected_total} "
            f"observed={totals} spread={spread} "
            f"delta={delta_vs_expected:.4f} byte_id={all_byte_identical}"
        )
    out = {
        "issue": "BEAAA-543",
        "action": (
            "rule-execution (scan orchestrator skeleton: corpus pin, "
            "credential resolve/forget, HOSTED_RUNNER row stamping, IPC "
            "streaming; not imported by any offline scoring path). "
            "Classification per scanner-implementation-supervision-"
            "v1.md@v1.0; executes the ratified ADR-0009 §4 architecture "
            "with no scoring math change."
        ),
        "classification_table_row": (
            "scanner-implementation-supervision-v1.md@v1.0 row "
            "'Implement a new probe inside an existing rubric_point_id' "
            "(closest analogue: additive Prober-side driver module "
            "invoked only inside the Prober subprocess that T4 spawns; "
            "no offline scoring path imports it)"
        ),
        "contract_version": "are-scanner-api-v1@v1.0.1",
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "n": n,
        "rubric_path": str(RUBRIC_PATH.relative_to(REPO)),
        "fixtures": list(deltas.keys()),
        "deltas": deltas,
        "all_in_band_pm1_and_byte_identical": overall_in_band,
        "note": (
            "scanners/live_probe_orchestrator.py is the Prober-side scan "
            "orchestrator skeleton for the live-probe build (ADR-0009 §5 "
            "#2). It is imported only by the Prober subprocess that T4 "
            "(scanners/live_probe_workers.py) spawns and by T1's POST "
            "/scan handler (BEAAA-547, not yet landed). No offline "
            "scoring path imports it. Byte-identical replay on every "
            "locked golden across N=5 confirms zero impact on offline "
            "scoring (Hard Invariant #2 on BEAAA-533). "
            "K-replicate + median-of-K aggregation is split out to T5 "
            "(BEAAA-544, rule-change). Per-scan rate/cost caps are "
            "split out to T9 (BEAAA-545)."
        ),
    }
    out_path = (
        REPO
        / "tools"
        / "calibration-deltas"
        / "2026-05-18-beaaa543-orchestrator-n5.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {out_path}")
    print(f"all_in_band_pm1_and_byte_identical={overall_in_band}")
    return 0 if overall_in_band else 1


if __name__ == "__main__":
    raise SystemExit(main())
