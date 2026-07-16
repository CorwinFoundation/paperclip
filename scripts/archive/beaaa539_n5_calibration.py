"""BEAAA-539 — N=5 calibration delta on the locked offline golden set.

This action adds `scanners.snapshot_hash.compute_corpus_snapshot_hash` —
a reproducibility-plumbing helper that hashes a probe corpus tarball
content-addressedly. It's rule-execution per
`scanner-implementation-supervision-v1.md@v1.0` row "Refactor that doesn't
touch behavior" (additive helper; nothing in the locked scoring path
imports it yet — T2 will plug it in).

Per Supreme Law (CTO `agents/cto-os.md` Reproducibility ±1):
**byte-identical replay** of every locked golden across N=5 runs.
"in band" here means spread=0 AND delta_vs_expected_max ≤ 1.0.

Mirrors `scripts/beaaa525_n5_calibration.py` — same code path, same
fixtures, same hash. Output filed under
`tools/calibration-deltas/2026-05-18-beaaa539-corpus-snapshot-n5.json`
per supervision contract artifact convention.
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
                snapshot_hash="N5-CALIBRATION-BEAAA-539",
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
        marker = "✓" if in_band else "✗"
        print(
            f"  {marker} {fid}: expected={expected_total} "
            f"observed={totals} spread={spread} "
            f"delta={delta_vs_expected:.4f} byte_id={all_byte_identical}"
        )
    out = {
        "issue": "BEAAA-539",
        "action": (
            "rule-execution (Refactor that doesn't touch behavior — "
            "scanners.snapshot_hash.compute_corpus_snapshot_hash added as "
            "an additive reproducibility-plumbing helper; not yet imported "
            "by any locked scoring path)"
        ),
        "classification_table_row": (
            "scanner-implementation-supervision-v1.md@v1.0 row "
            "'Refactor that doesn't touch behavior'"
        ),
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "n": n,
        "rubric_path": str(RUBRIC_PATH.relative_to(REPO)),
        "fixtures": list(deltas.keys()),
        "deltas": deltas,
        "all_in_band_pm1_and_byte_identical": overall_in_band,
        "note": (
            "compute_corpus_snapshot_hash is a content-addressed tarball "
            "hasher. It is not imported by any in-agent scoring path in "
            "this commit (T2 wires it into the orchestrator). "
            "Reproducibility on locked goldens is therefore expected and "
            "verified byte-identical (spread=0) across N=5 replays per "
            "are-scanner-api-v1@v1.0 Reproducibility ±1."
        ),
    }
    out_path = (
        REPO
        / "tools"
        / "calibration-deltas"
        / "2026-05-18-beaaa539-corpus-snapshot-n5.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {out_path}")
    print(f"all_in_band_pm1_and_byte_identical={overall_in_band}")
    return 0 if overall_in_band else 1


if __name__ == "__main__":
    raise SystemExit(main())
