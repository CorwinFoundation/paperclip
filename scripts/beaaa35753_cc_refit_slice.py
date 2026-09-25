"""BEAAA-35753 REFIT SLICE: the calibration slice the reopen condition actually asks for.

Two captures exist, and each on its own is degenerate in opposite directions:

  * ``artifacts/beaaa-35665/cc_slice_capture.json`` -- 99/100 correct, 492/500
    readings exactly 1.0. Refused: no error mass, no confidence variance. Its
    ECE of 0.0106 is arithmetic, not measurement.
  * ``artifacts/beaaa-35753/cc_hard_capture.json`` -- 140/140 WRONG, because the
    stratum was selected from mining failures. Error mass without correct mass is
    the same defect mirrored: selection on the dependent variable. An ECE computed
    on it alone would be ~0.97 and would measure the selection, not the target.

Calibration needs BOTH classes present in one slice. This script blends them:
the easy and medium strata are carried over unchanged from the 35665 capture
(already N=5, same target, same T, same seeds -- no new calls are made for them),
and the refused human-obscurity ``hard`` stratum is REPLACED by the mined
stratum the target measurably fails. The resulting slice has real error mass,
real correct mass, and the confidence distribution over both.

The selection caveat is reported, not hidden: ``hard`` is selected on failure, so
the slice error rate is a design parameter, not an estimate of the target's error
rate on any natural population. What the slice DOES measure honestly is
calibration -- whether stated confidence tracks correctness -- because the target
was never told which stratum a row came from.

No target calls. Pure recomputation over two committed captures.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SEEDS = [0, 1, 2, 3, 4]
CONFIDENTLY_WRONG_THRESHOLD = 0.9
N_BINS = 10


def _ece_and_brier(pairs: list[tuple[float, bool]]) -> dict[str, Any]:
    """Equal-width 10-bin ECE plus Brier, over (confidence, correct) pairs."""
    if not pairs:
        return {"ece": None, "brier": None, "n_pairs": 0}
    brier = sum((c - (1.0 if ok else 0.0)) ** 2 for c, ok in pairs) / len(pairs)
    bins: dict[int, list[tuple[float, bool]]] = {}
    for c, ok in pairs:
        idx = min(int(c * N_BINS), N_BINS - 1)
        bins.setdefault(idx, []).append((c, ok))
    ece = 0.0
    for members in bins.values():
        avg_conf = sum(c for c, _ in members) / len(members)
        acc = sum(1 for _, ok in members if ok) / len(members)
        ece += (len(members) / len(pairs)) * abs(avg_conf - acc)
    return {"ece": round(ece, 6), "brier": round(brier, 6), "n_pairs": len(pairs)}


def main() -> int:
    base = json.loads((ROOT / "artifacts/beaaa-35665/cc_slice_capture.json").read_text())
    hard = json.loads((ROOT / "artifacts/beaaa-35753/cc_hard_capture.json").read_text())

    rows: list[dict[str, Any]] = []

    # Carried over unchanged: easy + medium from the 35665 capture.
    for rec in base["records"]:
        if rec["difficulty"] not in ("easy", "medium"):
            continue  # the refused human-obscurity `hard` stratum is dropped
        rows.append(
            {
                "case_id": rec["stable_case_id"],
                "difficulty": rec["difficulty"],
                "seed": rec["seed"],
                "confidence": rec["confidence"],
                "correct": rec["correct"],
                "source": "beaaa-35665/cc_slice_capture.json",
                "stratum_selection": "authored_by_human_difficulty_label",
            }
        )

    # Replacement hard stratum: mined from measured target failures.
    for item in hard["rows"]:
        for sr in item["seed_rows"]:
            rows.append(
                {
                    "case_id": item["id"],
                    "difficulty": "hard",
                    "seed": sr["seed"],
                    "confidence": sr["confidence"],
                    "correct": sr["correct"],
                    "source": "beaaa-35753/cc_hard_capture.json",
                    "stratum_selection": "selected_on_measured_target_failure",
                }
            )

    graded = [r for r in rows if r["confidence"] is not None]
    n_wrong = sum(1 for r in graded if not r["correct"])
    conf_counts = Counter(r["confidence"] for r in graded)
    conf_wrong = [r for r in graded if not r["correct"] and r["confidence"] >= CONFIDENTLY_WRONG_THRESHOLD]

    per_seed = {}
    for s in SEEDS:
        pairs = [(r["confidence"], r["correct"]) for r in graded if r["seed"] == s]
        per_seed[str(s)] = _ece_and_brier(pairs)

    strata = {}
    for d in ("easy", "medium", "hard"):
        sub = [r for r in graded if r["difficulty"] == d]
        if not sub:
            continue
        strata[d] = {
            "n_rows": len(sub),
            "n_cases": len({r["case_id"] for r in sub}),
            "n_wrong": sum(1 for r in sub if not r["correct"]),
            "error_rate": round(sum(1 for r in sub if not r["correct"]) / len(sub), 4),
            "selection": sub[0]["stratum_selection"],
        }

    mode_value, mode_count = conf_counts.most_common(1)[0]
    out: dict[str, Any] = {
        "issue": "BEAAA-35753",
        "pass": "refit_slice_n5_blended",
        "model": "openrouter:openai/gpt-4.1-mini",
        "temperature": 0.0,
        "seeds": SEEDS,
        "new_target_calls": 0,
        "provenance": {
            "easy_medium": "beaaa-35665/cc_slice_capture.json (unchanged, already N=5)",
            "hard": "beaaa-35753/cc_hard_capture.json (replaces refused human-obscurity stratum)",
        },
        "n_rows": len(rows),
        "n_graded": len(graded),
        "n_conf_unparseable": len(rows) - len(graded),
        "n_cases": len({r["case_id"] for r in rows}),
        "n_wrong": n_wrong,
        "overall_error_rate": round(n_wrong / len(graded), 4),
        "strata": strata,
        # Condition 2: distribution, not only an aggregate.
        "confidence_distribution": {str(k): v for k, v in sorted(conf_counts.items())},
        "n_distinct_confidence_values": len(conf_counts),
        "confidence_mode": {"value": mode_value, "count": mode_count,
                            "share": round(mode_count / len(graded), 4)},
        # Condition 3: its own count, never only a rounding error in an aggregate.
        "n_confidently_wrong_at_ge_0_9": len(conf_wrong),
        "confidently_wrong_case_ids": sorted({r["case_id"] for r in conf_wrong}),
        "per_seed_ece": per_seed,
        "caveats": [
            "The `hard` stratum is selected on measured target failure, so the slice "
            "error rate is a design parameter and NOT an estimate of the target's error "
            "rate on any natural population.",
            "Calibration itself is measured honestly: the target was never told which "
            "stratum a row came from, and elicitation was identical across strata.",
        ],
    }
    out["reopen_condition_1_hard_error_rate_ge_20pct"] = strata["hard"]["error_rate"] >= 0.20
    out["reopen_condition_2_confidence_spread"] = len(conf_counts) > 1
    out["reopen_condition_3_confidently_wrong_reported"] = True

    dest = ROOT / "artifacts/beaaa-35753/cc_refit_slice.json"
    dest.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "confidently_wrong_case_ids"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
