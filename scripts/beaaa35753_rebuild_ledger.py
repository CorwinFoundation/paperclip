"""BEAAA-35753 LEDGER REBUILD: build the activation ledger from the refit artifacts.

Defects being corrected (from CEO denial 2026-09-25T15:07:47Z):

  A. The prior ledger (b39049ba) cited cc_slice_capture.json as evidence_source.path —
     the REFUSED capture, not the refit. evidence_source must point at the actual
     measured artifacts: cc_refit_slice.json + cc_hard_capture.json.

  B. The golden-delta evidence was measured with the ledger absent, making
     flag-on == flag-off a tautology. The golden runs must be re-run AFTER
     this ledger is committed, so flag-on actually differs from flag-off.

Row set design:
  70 easy+medium cases — from the BEAAA-18720 probe inventory (same dedupe_sha256
  values as the original ledger). These are real probe candidates and will be
  matched by the registry's candidate lookup.

  28 hard cases — arithmetic items from the mining pass, NOT in the probe
  inventory. They are included as disposition="evidence_only" to document the
  error-mass measurement. The registry will not find any candidate with their
  dedupe_sha256 (which is SHA256(prompt)) so they activate nothing.
  This is intentional: they prove the slice has real error mass, and that
  proof belongs in the ledger's provenance.

Standing caveat (preserved from cc_refit_slice.json):
  The hard stratum is selected on measured target failure, so the slice error
  rate is a design parameter and NOT an estimate of the target's error rate
  on any natural population. Calibration itself is measured honestly: the
  target was never told which stratum a row came from, and elicitation was
  identical across strata.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.beaaa35753_cc_hard_mining import candidates  # noqa: E402
from harness.bench.confidence_calibration import ELICITATION_SUFFIX  # noqa: E402

SEEDS = [0, 1, 2, 3, 4]
ELICITATION_SUFFIX_STR = ELICITATION_SUFFIX


def main() -> int:
    # --- Load source artifacts ---
    base_capture = json.loads((ROOT / "artifacts/beaaa-35665/cc_slice_capture.json").read_text())
    hard_capture = json.loads((ROOT / "artifacts/beaaa-35753/cc_hard_capture.json").read_text())
    refit_slice = json.loads((ROOT / "artifacts/beaaa-35753/cc_refit_slice.json").read_text())

    # --- Easy + medium rows from probe inventory ---
    # Aggregate per case_id across seeds
    easy_med_by_case: dict[str, list] = {}
    for rec in base_capture["records"]:
        if rec["difficulty"] not in ("easy", "medium"):
            continue
        cid = rec["stable_case_id"]
        easy_med_by_case.setdefault(cid, []).append(rec)

    rows: list[dict] = []

    for cid, recs in sorted(easy_med_by_case.items()):
        # All records for this case across seeds
        n_seeds = len(recs)
        n_passed = sum(1 for r in recs if r["correct"])
        confidences = [r["confidence"] for r in recs if r["confidence"] is not None]
        # median confidence across seeds
        sorted_conf = sorted(confidences)
        median_conf = sorted_conf[len(sorted_conf)//2] if sorted_conf else None
        seed_stable = len({r["correct"] for r in recs}) == 1  # all same verdict
        r0 = recs[0]
        rows.append({
            "stable_case_id": cid,
            "dedupe_sha256": str(r0["dedupe_sha256"]),
            "category": r0.get("category"),
            "difficulty": r0["difficulty"],
            "family_id": "confidence_calibration",
            "rubric_point_id": "P1.5",
            "disposition": "scored",
            "hold_reason": None,
            "measured_raw_median": median_conf,
            "n_passed": n_passed,
            "n_seeds": n_seeds,
            "seed_stable": seed_stable,
        })

    # --- Hard rows from mining pass (evidence_only) ---
    # Load the mining candidates to get prompts for hashing
    all_candidates = {c["id"]: c for c in candidates()}
    hard_wrong_ids = hard_capture.get("rows", [])
    for item in hard_wrong_ids:
        item_id = item["id"]
        candidate = all_candidates.get(item_id)
        if candidate is None:
            print(f"WARNING: mining candidate {item_id!r} not found", file=sys.stderr)
            continue
        prompt_with_suffix = candidate["prompt"] + ELICITATION_SUFFIX_STR
        dedupe_sha256 = hashlib.sha256(prompt_with_suffix.encode()).hexdigest()

        seed_rows = item.get("seed_rows", [])
        n_seeds = len(seed_rows)
        n_passed = sum(1 for sr in seed_rows if sr["correct"])
        confidences = [sr["confidence"] for sr in seed_rows if sr.get("confidence") is not None]
        sorted_conf = sorted(confidences)
        median_conf = sorted_conf[len(sorted_conf)//2] if sorted_conf else None
        seed_stable = len({sr["correct"] for sr in seed_rows}) == 1

        rows.append({
            "stable_case_id": item_id,
            "dedupe_sha256": dedupe_sha256,
            "category": item.get("category"),
            "difficulty": "hard",
            "family_id": "confidence_calibration",
            "rubric_point_id": "P1.5",
            "disposition": "evidence_only",  # NOT scored: not in probe inventory
            "hold_reason": "arithmetic item from mining pass; not a probe candidate",
            "measured_raw_median": median_conf,
            "n_passed": n_passed,
            "n_seeds": n_seeds,
            "seed_stable": seed_stable,
        })

    n_scored = sum(1 for r in rows if r["disposition"] == "scored")
    n_evidence_only = sum(1 for r in rows if r["disposition"] == "evidence_only")

    ledger = {
        "issue": "BEAAA-35753",
        "parent_ticket": "BEAAA-21728",
        "family_id": "confidence_calibration",
        "activation_tier": "founder_dev_mode",
        "production_activation": False,
        "priced_score_weight": 0.0,
        "evidenced": n_scored,
        "held": 0,
        "mapped": n_scored,
        "evidence_source": {
            "adapter": "openrouter",
            "model": "openai/gpt-4.1-mini",
            "oracle": "deterministic_verifier",
            "grader": "harness/bench/confidence_calibration.py",
            "judge_calls": 0,
            "path": [
                "artifacts/beaaa-35753/cc_refit_slice.json",
                "artifacts/beaaa-35753/cc_hard_capture.json",
                "artifacts/beaaa-35665/cc_slice_capture.json",
            ],
            "note": (
                "easy+medium rows: artifacts/beaaa-35665/cc_slice_capture.json; "
                "hard rows (evidence_only): artifacts/beaaa-35753/cc_hard_capture.json; "
                "aggregate measurement: artifacts/beaaa-35753/cc_refit_slice.json"
            ),
        },
        "per_seed_ece": refit_slice["per_seed_ece"],
        "difficulty_strata": {
            d: {
                "n_cases": s["n_cases"],
                "n_rows": s["n_rows"],
                "n_wrong": s["n_wrong"],
                "error_rate": s["error_rate"],
                "selection": s["selection"],
            }
            for d, s in refit_slice["strata"].items()
        },
        "selection_caveat": (
            "The hard stratum is selected on measured target failure, so the slice "
            "error rate is a design parameter and NOT an estimate of the target's "
            "error rate on any natural population. Calibration is measured honestly: "
            "the target was never told which stratum a row came from, and elicitation "
            "was identical across strata."
        ),
        "construction_gate": (
            f"70 easy+medium rows: all clear at 40a8482c "
            f"(artifacts/beaaa-35660/construction_ledger.json); "
            f"28 hard arithmetic rows are evidence_only (not probe candidates, "
            f"construction gate not applicable)"
        ),
        "n_rows": len(rows),
        "n_scored": n_scored,
        "n_evidence_only": n_evidence_only,
        "rows": rows,
    }

    dest = ROOT / "artifacts/beaaa-35665/cc-dev-activation-ledger.json"
    dest.write_text(json.dumps(ledger, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in ledger.items() if k != "rows"}, indent=1))
    print(f"\nRows: {len(rows)} total ({n_scored} scored, {n_evidence_only} evidence_only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
