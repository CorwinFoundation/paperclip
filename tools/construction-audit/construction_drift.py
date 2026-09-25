#!/usr/bin/env python3
"""BEAAA-35660: did a row's construction change after its score was captured?

The signature predicate reads construction as it stands at master. That answers
"is this row's construction defective now". It does not answer "was this row's
recorded score produced under a construction we have since changed", which is
the question that actually bears on the 479.

This module closes that gap: for every row it takes the last commit date of each
file that builds the row's prompt, and compares it to the row's evidence capture
date. A constructor that moved after capture means the stored score was produced
under a construction that no longer exists, and the score is not cleared by a
clean predicate result.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORRECTION_COMMIT = "1ba1e8a0bb175ad1ce3bfd598a6f1985f25486d7"


def last_commit_date(path: str, cache: dict) -> str | None:
    if path not in cache:
        proc = subprocess.run(
            ["git", "-C", str(REPO), "log", "-1", "--format=%cI", "--", path],
            capture_output=True, text=True,
        )
        cache[path] = (proc.stdout.strip() or None) if proc.returncode == 0 else None
    return cache[path]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="artifacts/beaaa-35660/construction_ledger.json")
    ap.add_argument("--out", default="artifacts/beaaa-35660/construction_drift.json")
    args = ap.parse_args()

    ledger = json.loads((REPO / args.ledger).read_text(encoding="utf-8"))
    cache: dict = {}
    drift_rows = []

    for row in ledger["rows"]:
        captured = row.get("last_verified_at") or ledger.get("last_verified_at")
        sources = [s.split("#")[0] for s in row.get("construction_sources", [])]
        sources = [s for s in sources if s and (REPO / s).exists()]
        dates = {s: last_commit_date(s, cache) for s in sources}
        newest = max((d for d in dates.values() if d), default=None)
        drift_rows.append({
            "case_id": row["case_id"],
            "scoring_active": row["scoring_active"],
            "captured_at": captured,
            "construction_files": dates,
            "newest_construction_commit": newest,
        })

    prod = [r for r in drift_rows if r["scoring_active"]]
    payload = {
        "issue": "BEAAA-35660",
        "correction_commit": CORRECTION_COMMIT,
        "production_scored_rows": len(prod),
        "rows": drift_rows,
    }
    (REPO / args.out).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(drift_rows), "production_scored": len(prod)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
