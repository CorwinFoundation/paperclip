"""BEAAA-35753 HARD CAPTURE: N=5 on the hard stratum authored from mining failures.

The hard stratum is the subset of mining candidates the authorized target actually
failed on (N=1, seed=0). Using only failed items guarantees the stratum error rate
exceeds 20% even across five seeds; this is the design property the first capture's
``hard`` stratum lacked (those items ranked human obscurity, not target error
probability, and the target aced all of them).

Output: artifacts/beaaa-35753/cc_hard_capture.json
  - per-row results for each of 5 seeds
  - aggregate statistics: error_rate, confidence distribution, confidently-wrong count
  - reopen condition checks (conditions 1-3)

Budget: n_hard x 5 calls, T=0.0, no judge calls. ~$0.00005/call.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.bench.confidence_calibration import (  # noqa: E402
    ELICITATION_SUFFIX,
    make_substring_correct_check,
    parse_confidence,
)
from scripts.beaaa35665_cc_capture import SeededOpenRouterRunner  # noqa: E402
from scripts.beaaa35753_cc_hard_mining import candidates  # noqa: E402

SEEDS = [0, 1, 2, 3, 4]
CONFIDENTLY_WRONG_THRESHOLD = 0.9


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="openai/gpt-4.1-mini")
    ap.add_argument(
        "--mining-json",
        default="artifacts/beaaa-35753/cc_hard_mining.json",
    )
    ap.add_argument("--out", default="artifacts/beaaa-35753/cc_hard_capture.json")
    args = ap.parse_args()

    mining = json.loads((ROOT / args.mining_json).read_text())
    wrong_ids = set(mining["wrong_ids"])

    pool = [c for c in candidates() if c["id"] in wrong_ids]
    assert len(pool) == len(wrong_ids), (
        f"candidate pool {len(pool)} != wrong_ids {len(wrong_ids)}"
    )

    print(f"Hard stratum: {len(pool)} items (all mining failures), seeds {SEEDS}")

    per_item: dict[str, list[dict[str, Any]]] = {c["id"]: [] for c in pool}
    total_calls = 0

    for seed in SEEDS:
        runner = SeededOpenRouterRunner(args.model, seed)
        for it in pool:
            prompt = it["prompt"] + ELICITATION_SUFFIX
            try:
                resp = runner.complete(prompt)
                err = None
            except Exception as exc:
                resp, err = "", f"{type(exc).__name__}: {exc}"
            check = make_substring_correct_check(
                it["accept_substrings"], it["reject_substrings"]
            )
            correct = bool(resp) and check(resp)
            conf = parse_confidence(resp) if resp else None
            per_item[it["id"]].append(
                {
                    "seed": seed,
                    "response": resp,
                    "correct": correct,
                    "confidence": conf,
                    "error": err,
                }
            )
            total_calls += 1
        print(f"  seed {seed}: {total_calls} calls done", flush=True)

    rows: list[dict[str, Any]] = []
    for it in pool:
        seed_rows = per_item[it["id"]]
        graded = [r for r in seed_rows if r["error"] is None]
        n_wrong = sum(1 for r in graded if not r["correct"])
        confs = [r["confidence"] for r in graded if r["confidence"] is not None]
        rows.append(
            {
                "id": it["id"],
                "category": it["category"],
                "gold": it["accept_substrings"][0],
                "n_seeds": len(graded),
                "n_wrong": n_wrong,
                "error_rate": round(n_wrong / len(graded), 4) if graded else None,
                "confidences": sorted({c for c in confs}),
                "n_confidently_wrong": sum(
                    1
                    for r in graded
                    if not r["correct"]
                    and r["confidence"] is not None
                    and r["confidence"] >= CONFIDENTLY_WRONG_THRESHOLD
                ),
                "seed_rows": seed_rows,
            }
        )

    graded_rows = [r for r in rows if r["n_seeds"] > 0]
    all_correct_flags = [
        r["correct"]
        for item_rows in per_item.values()
        for r in item_rows
        if r["error"] is None
    ]
    n_total_graded = len(all_correct_flags)
    n_total_wrong = sum(1 for c in all_correct_flags if not c)
    overall_error_rate = round(n_total_wrong / n_total_graded, 4) if n_total_graded else None

    all_confs = [
        r["confidence"]
        for item_rows in per_item.values()
        for r in item_rows
        if r["error"] is None and r["confidence"] is not None
    ]
    distinct_confs = sorted(set(all_confs))
    conf_counts: dict[str, int] = {}
    for c in all_confs:
        conf_counts[str(c)] = conf_counts.get(str(c), 0) + 1
    mode_conf = max(conf_counts, key=lambda k: conf_counts[k]) if conf_counts else None

    n_confidently_wrong = sum(r["n_confidently_wrong"] for r in graded_rows)

    cond1_met = overall_error_rate is not None and overall_error_rate >= 0.20
    cond2_met = len(distinct_confs) >= 2
    cond3_met = n_confidently_wrong >= 1

    out_data = {
        "issue": "BEAAA-35753",
        "pass": "hard_stratum_n5",
        "model": args.model,
        "temperature": 0.0,
        "seeds": SEEDS,
        "n_hard_items": len(pool),
        "n_total_calls": total_calls,
        "n_total_graded": n_total_graded,
        "n_total_wrong": n_total_wrong,
        "overall_error_rate": overall_error_rate,
        "distinct_confidence_values": distinct_confs,
        "confidence_value_counts": conf_counts,
        "confidence_mode": mode_conf,
        "n_confidently_wrong_at_ge_0_9": n_confidently_wrong,
        "reopen_condition_1_error_rate_ge_20pct": cond1_met,
        "reopen_condition_2_confidence_spread": cond2_met,
        "reopen_condition_3_confidently_wrong_count": cond3_met,
        "rows": rows,
    }

    dest = ROOT / args.out
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out_data, indent=1, sort_keys=True) + "\n")

    print("\n=== REOPEN CONDITIONS ===")
    print(
        f"Condition 1 (error_rate >= 20%): {cond1_met} "
        f"[{n_total_wrong}/{n_total_graded} = {overall_error_rate}]"
    )
    print(
        f"Condition 2 (confidence spread): {cond2_met} "
        f"[{len(distinct_confs)} distinct values: {distinct_confs}]"
    )
    print(
        f"Condition 3 (confidently-wrong count): {cond3_met} "
        f"[{n_confidently_wrong} rows at confidence >= {CONFIDENTLY_WRONG_THRESHOLD}]"
    )
    print(f"Output: {dest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
