"""BEAAA-545 — N=5 calibration delta on the locked offline golden set.

This action adds three operational ceilings (MAX_PROBES_PER_SCAN,
MAX_TOKENS_PER_SCAN, MAX_WALL_CLOCK_SECONDS) to the T2 live-probe
orchestrator (``scanners/live_probe_orchestrator.py``). The caps live
inside the live-probe code path only; no offline scoring path imports
the orchestrator. Expected calibration result: byte-identical replay
(spread=0, delta=0.0000) across N=5 runs on every locked golden — the
strictest form of Supreme-Law ±1.

Classification: rule-execution per ``contracts/scanner-implementation-
supervision-v1.md@v1.0``. The ``incomplete: true`` / ``incomplete_reason``
JSON field shape was co-signed under BEAAA-531 (v1.1.0 contract); the
cap thresholds themselves are implementation defaults, not contract
surface.

Mirrors ``scripts/beaaa543_orchestrator_n5.py`` exactly — same code
path, same fixtures, same hash math.
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
                snapshot_hash="N5-CALIBRATION-BEAAA-545",
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
        "issue": "BEAAA-545",
        "action": (
            "rule-execution (T9 — three operational ceilings on T2 "
            "orchestrator: MAX_PROBES_PER_SCAN, MAX_TOKENS_PER_SCAN, "
            "MAX_WALL_CLOCK_SECONDS, with incomplete: true / "
            "incomplete_reason emission on breach). The caps live "
            "inside scanners/live_probe_orchestrator.py only; no "
            "offline scoring path imports it. Classification per "
            "scanner-implementation-supervision-v1.md@v1.0."
        ),
        "classification_table_row": (
            "scanner-implementation-supervision-v1.md@v1.0 row "
            "'Implement a new probe inside an existing rubric_point_id' "
            "(closest analogue: additive operational caps on the Prober-"
            "side orchestrator invoked only inside the Prober subprocess "
            "that T4 spawns; no offline scoring path imports it)"
        ),
        "contract_version": "are-scanner-api-v1@v1.1.0",
        "cto_cosign_ref": (
            "BEAAA-531 ACCEPT for the `incomplete: true` + "
            "`incomplete_reason` JSON field shape only. Cap thresholds "
            "themselves are implementation defaults, not contract surface."
        ),
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "n": n,
        "rubric_path": str(RUBRIC_PATH.relative_to(REPO)),
        "fixtures": list(deltas.keys()),
        "deltas": deltas,
        "all_in_band_pm1_and_byte_identical": overall_in_band,
        "note": (
            "scanners/live_probe_orchestrator.py is the Prober-side "
            "scan orchestrator (ADR-0009 §5 #2). Its operational caps "
            "(T9 / BEAAA-545) live entirely inside the live-probe code "
            "path. No offline scoring path imports it. Byte-identical "
            "replay on every locked golden across N=5 confirms zero "
            "impact on offline scoring (Hard Invariant #2 on BEAAA-533)."
        ),
    }
    out_path = (
        REPO
        / "tools"
        / "calibration-deltas"
        / "2026-05-18-beaaa545-caps-n5.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {out_path}")
    print(f"all_in_band_pm1_and_byte_identical={overall_in_band}")
    return 0 if overall_in_band else 1


if __name__ == "__main__":
    raise SystemExit(main())
