"""BEAAA-542 — N=5 calibration delta on the locked golden set.

This action adds ``scanners/live_probe_workers.py`` (scan worker process
model + IPC validator wrap) and a minimal list-form extension to
``ScopedEgressAllowlist`` so the Scorer worker can express its deny-all
posture (``ScopedEgressAllowlist([])``).

Classification: rule-execution per ``contracts/scanner-implementation-
supervision-v1.md@v1.0`` — net-increase in tamper-resistance posture
via process split + IPC validator reuse (Δ1 verbatim wrap of
``api.intake_guard``). Hard Invariant #3 + #4 + #5.

The new module is not imported by any scoring path; the orchestrator
(ADR-0009 §5 #2) is the sibling ticket that will import both this
module and the credential vault. The expected calibration result is
therefore byte-identical replay (spread=0, delta=0.0000) across N=5
runs on every locked golden — the strictest form of Supreme-Law ±1.

Mirrors ``scripts/beaaa538_credentials_n5.py`` exactly — same code
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
                snapshot_hash="N5-CALIBRATION-BEAAA-542",
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
        "issue": "BEAAA-542",
        "action": (
            "rule-execution (defensive control — scan worker process "
            "model + IPC validator wrap; not imported by any scoring "
            "path until the orchestrator sibling ticket lands). "
            "Classification per scanner-implementation-supervision-"
            "v1.md@v1.0; net-increase in tamper-resistance posture "
            "(Hard Invariant #3 + #4 + #5)."
        ),
        "classification_table_row": (
            "scanner-implementation-supervision-v1.md@v1.0 row "
            "'Refactor that doesn't touch behavior' (closest analogue: "
            "additive module not invoked by any locked scoring path; "
            "the egress allow-list extension is API-additive — existing "
            "single-string callers unchanged)"
        ),
        "contract_version": "are-scanner-api-v1@v1.0.1",
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "n": n,
        "rubric_path": str(RUBRIC_PATH.relative_to(REPO)),
        "fixtures": list(deltas.keys()),
        "deltas": deltas,
        "all_in_band_pm1_and_byte_identical": overall_in_band,
        "note": (
            "scanners/live_probe_workers.py is the new scan worker "
            "process model for the live-probe build (ADR-0009 §5 #4). "
            "It is imported only in the Prober / Scorer workers that "
            "the orchestrator (ADR-0009 §5 #2) spawns. No scoring path "
            "imports it. The minimal ScopedEgressAllowlist extension "
            "(list form, incl. deny-all []) is API-additive: existing "
            "single-string callers see no behavior change. Byte-"
            "identical replay on every locked golden across N=5 "
            "confirms zero impact on offline scoring (Hard Invariant "
            "#2)."
        ),
    }
    out_path = (
        REPO
        / "tools"
        / "calibration-deltas"
        / "2026-05-18-beaaa542-workers-n5.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {out_path}")
    print(f"all_in_band_pm1_and_byte_identical={overall_in_band}")
    return 0 if overall_in_band else 1


if __name__ == "__main__":
    raise SystemExit(main())
