"""BEAAA-35938: record an OpenRouter account-pool reading as the authoritative spend measure.

The capture script's own spend meter is an estimate derived from a price table
and the token counts the API reports back. It is not the account. The authorized
cap on this slice is a real-money cap, so the cap gate reads the pool, and the
pool readings have to be on the artifact rather than in a comment.

Baseline is the CEO's pre-capture reading on BEAAA-35938 at 2026-09-25T20:19Z:
``total_credits - total_usage == 14.284500``, taken before any call went out.

Usage: python3 scripts/beaaa35938_pool_reading.py --label <why>
Appends one JSON line to artifacts/beaaa-35938/pool-readings.jsonl.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_REMAINING_USD = 14.284500
BASELINE_LABEL = "CEO pre-capture reading, BEAAA-35938 2026-09-25T20:19Z"
OUT = ROOT / "artifacts" / "beaaa-35938" / "pool-readings.jsonl"


def read_pool() -> dict[str, float]:
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/credits",
        headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]},
    )
    d = json.load(urllib.request.urlopen(req))["data"]
    return {"total_credits": d["total_credits"], "total_usage": d["total_usage"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--journal", default="artifacts/beaaa-35938/hl_slice_journal.jsonl")
    args = ap.parse_args()

    pool = read_pool()
    remaining = pool["total_credits"] - pool["total_usage"]
    journal = ROOT / args.journal
    rows = 0
    if journal.exists():
        rows = len(
            {json.loads(line)["stable_case_id"] for line in journal.read_text().splitlines() if line.strip()}
        )
    rec = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "label": args.label,
        "remaining_usd": round(remaining, 6),
        "drawn_since_baseline_usd": round(BASELINE_REMAINING_USD - remaining, 6),
        "baseline_remaining_usd": BASELINE_REMAINING_USD,
        "baseline_label": BASELINE_LABEL,
        "distinct_rows_journaled": rows,
        **pool,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    print(json.dumps(rec, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
