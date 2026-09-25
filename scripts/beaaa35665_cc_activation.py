"""BEAAA-35665 ACTIVATE: turn the measured confidence_calibration slice into a dev-mode ledger.

Reads the CAPTURE/VALIDATE output written by ``beaaa35665_cc_capture.py`` and
emits ``artifacts/beaaa-35665/cc-dev-activation-ledger.json`` in the same shape
``tools/active_test_registry.py`` already consumes for BEAAA-35380.

Dev mode only. ``production_activation`` is false and ``priced_score_weight`` is
0.0, so the flag-off production count cannot move. Rollback is deleting this
ledger: every row falls back to ``reported_not_scored`` because the registry
loader promotes nothing when the ledger is missing.

Fail closed, per row:
  - a row that errored on any seed is ``held``, never ``scored``
  - a row that is not unanimous across all 5 seeds is ``held`` as unmeasured --
    a 3-of-5 is not a 0.6, it is an unstable row (CEO ruling, BEAAA-35020)
  - a row whose rubric point cannot be read from the family mapping is ``held``
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAMILY = "confidence_calibration"
ART = ROOT / "artifacts" / "beaaa-35665"
MAPPING_YAML = ROOT / "rubric" / "probe_mappings" / "confidence_calibration.yaml"
PARENT_TICKET = "BEAAA-21728"
EXPECTED_SEEDS = 5


def rubric_point_id() -> str:
    """The dim this family maps to, read from the family mapping, not hardcoded."""
    data = yaml.safe_load(MAPPING_YAML.read_text(encoding="utf-8"))
    dims = {str(p.get("dim") or "") for p in (data.get("probes") or [])}
    dims.discard("")
    if len(dims) != 1:
        raise SystemExit(f"expected exactly one dim in {MAPPING_YAML.name}, found {sorted(dims)}")
    return dims.pop()


def build_ledger(per_row: list[dict[str, Any]], validation: dict[str, Any], capture: dict[str, Any]) -> dict[str, Any]:
    point = rubric_point_id()
    rows = []
    for row in sorted(per_row, key=lambda r: r["stable_case_id"]):
        n_seeds = int(row["n_seeds"])
        n_passed = int(row["n_passed"])
        stable = bool(row["seed_stable"]) and n_seeds == EXPECTED_SEEDS
        hold = None
        if row["errors"]:
            hold = "target_error_on_at_least_one_seed"
        elif n_seeds != EXPECTED_SEEDS:
            hold = f"only_{n_seeds}_of_{EXPECTED_SEEDS}_seeds_measured"
        elif not stable:
            hold = f"below_stability_floor_{n_passed}_of_{n_seeds}"
        rows.append(
            {
                "stable_case_id": row["stable_case_id"],
                "dedupe_sha256": row["dedupe_sha256"],
                "family_id": FAMILY,
                "rubric_point_id": point,
                "disposition": "held" if hold else "scored",
                "hold_reason": hold,
                "difficulty": row["difficulty"],
                "category": row["category"],
                "n_seeds": n_seeds,
                "n_passed": n_passed,
                "seed_stable": stable,
                # Unanimous pass -> 1.0, unanimous fail -> 0.0. Nothing in between
                # is written, because nothing in between is scored.
                "measured_raw_median": (1.0 if n_passed == n_seeds else 0.0) if stable else None,
            }
        )
    scored = [r for r in rows if r["disposition"] == "scored"]
    return {
        "issue": "BEAAA-35665",
        "parent_ticket": PARENT_TICKET,
        "family_id": FAMILY,
        "activation_tier": "founder_dev_mode",
        "production_activation": False,
        "priced_score_weight": 0.0,
        "mapped": len(rows),
        "evidenced": len(scored),
        "held": len(rows) - len(scored),
        "evidence_source": {
            "adapter": "openrouter",
            "model": capture["model"],
            "seeds": capture["seeds"],
            "temperature": 0.0,
            "target_calls": capture["target_calls"],
            "judge_calls": 0,
            "oracle": "deterministic_verifier",
            "grader": "harness/bench/confidence_calibration.py",
            "path": "artifacts/beaaa-35665/cc_slice_capture.json",
        },
        "construction_gate": "all 100 rows clear at 40a8482c (artifacts/beaaa-35660/construction_ledger.json)",
        "difficulty_strata": validation["difficulty_strata"],
        "per_seed_ece": validation["per_seed_ece"],
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--art", type=Path, default=ART)
    args = parser.parse_args()
    per_row = json.loads((args.art / "cc_slice_per_row.json").read_text(encoding="utf-8"))
    validation = json.loads((args.art / "cc_slice_validation.json").read_text(encoding="utf-8"))
    capture = json.loads((args.art / "cc_slice_capture.json").read_text(encoding="utf-8"))
    ledger = build_ledger(per_row, validation, capture)
    out = args.art / "cc-dev-activation-ledger.json"
    out.write_text(json.dumps(ledger, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{out.relative_to(ROOT)}: {ledger['evidenced']} scored, {ledger['held']} held, rubric={ledger['rows'][0]['rubric_point_id']}")
    for row in ledger["rows"]:
        if row["hold_reason"]:
            print(f"  HELD {row['stable_case_id']}: {row['hold_reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
