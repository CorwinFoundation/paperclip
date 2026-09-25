"""BEAAA-35938: turn the hallucination_legal capture into the dev-activation ledger.

Reads artifacts/beaaa-35938/hl_slice_capture.json and writes
artifacts/beaaa-35938/hl-dev-activation-ledger.json in the shape
tools/active_test_registry.py:_beaaa35938_dev_scored_rows() consumes.

Disposition is decided here and nowhere else, fail-closed:

  scored        every one of the 5 seeds produced a verdict AND the verdict was
                identical on all five. For a pinned_llm_judge family that is the
                whole safety argument -- an ensemble that disagrees with itself
                across seeds is not a measurement, and the BEAAA-21728 guardrail
                is that nothing is scored without a real verdict.
  held          captured but not seed-stable, or a seed errored, or the judge
                output failed to parse on any seed. Held rows carry their reason
                and are absent from the registry loader's digest set.

There is no ``evidence_only`` bucket here. BEAAA-35915 needed one because its
hard-arithmetic stratum was authored as calibration stimulus rather than as probe
candidates; every row in this slice is a probe candidate from the family YAML.

The rubric point is NOT chosen by this script. It is read from the four
scoring_active ``bench_wrapper`` rows the registry already carries for this
family (hl.f1.statute / hl.f2.case_citation / hl.f3.self_context /
hl.f4.procedural), all of which sit at P1.1. If those four ever disagree, this
script raises rather than picking one.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CAPTURE = "artifacts/beaaa-35938/hl_slice_capture.json"
LEDGER = "artifacts/beaaa-35938/hl-dev-activation-ledger.json"


def production_rubric_point(registry: list[dict[str, Any]]) -> str:
    """P1.1, read off the family's own production rows rather than assumed."""
    points = {
        r["risk_or_rubric_id"]
        for r in registry
        if r.get("test_id") == "hallucination_legal"
        and r.get("runner") == "bench_wrapper"
        and r.get("scoring_active") is True
    }
    if len(points) != 1:
        raise SystemExit(
            f"hallucination_legal bench_wrapper rows disagree on rubric point: {sorted(points)}"
        )
    return points.pop()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", default=CAPTURE)
    ap.add_argument("--registry", required=True, help="registry JSON built at this tree")
    ap.add_argument("--out", default=LEDGER)
    args = ap.parse_args()

    cap = json.loads((ROOT / args.capture).read_text())
    registry = json.loads(Path(args.registry).read_text())
    rubric_point = production_rubric_point(registry)

    if cap.get("stopped_reason"):
        raise SystemExit(f"capture stopped early, refusing to build a ledger: {cap['stopped_reason']}")

    rows: list[dict[str, Any]] = []
    for r in cap["rows"]:
        labels = [
            (s.get("verdict") or {}).get("label")
            for s in r["per_seed"]
            if s.get("verdict") is not None
        ]
        parse_ok = all(
            (s.get("verdict") or {}).get("parse_ok", True) for s in r["per_seed"] if s.get("verdict")
        )
        errors = [s.get("error") for s in r["per_seed"] if s.get("error")]
        stable = r["complete"] and len(set(labels)) == 1 and len(labels) == r["seeds_expected"]

        if errors:
            disposition, reason = "held", f"seed error: {errors[0]}"
        elif not parse_ok:
            disposition, reason = "held", "judge output failed to parse on at least one seed"
        elif not stable:
            disposition, reason = "held", f"verdict not stable across seeds: {sorted(set(labels))}"
        else:
            disposition, reason = "scored", None

        rows.append(
            {
                "stable_case_id": r["stable_case_id"],
                "dedupe_sha256": r["dedupe_sha256"],
                "family_id": "hallucination_legal",
                "probe_family": r["family"],
                "disposition": disposition,
                "hold_reason": reason,
                "rubric_point_id": rubric_point if disposition == "scored" else None,
                "verdict_label": labels[0] if stable else None,
                "measured_raw_median": r["median_score"],
                "n_passed": sum(1 for s in r["scores"] if s >= 1.0),
                "n_seeds": r["n_seeds"],
                "seed_stable": stable,
                "distinct_responses": r["distinct_responses"],
                "n_fabrication": r["n_fabrication"],
            }
        )

    scored = [r for r in rows if r["disposition"] == "scored"]
    held = [r for r in rows if r["disposition"] == "held"]

    ledger = {
        "issue": "BEAAA-35938",
        "parent_ticket": "BEAAA-21728",
        "family_id": "hallucination_legal",
        "activation_tier": "founder_dev_mode",
        "production_activation": False,
        "priced_score_weight": 0.0,
        "rubric_point_id": rubric_point,
        "rubric_point_source": (
            "the four scoring_active bench_wrapper rows this family already carries "
            "(hl.f1.statute, hl.f2.case_citation, hl.f3.self_context, hl.f4.procedural)"
        ),
        "n_rows": len(rows),
        "n_scored": len(scored),
        "n_held": len(held),
        "evidence_source": {
            "capture": args.capture,
            "target": cap["target"],
            "judge": cap["judge"],
            "seeds": cap["seeds"],
            "measured_spend": cap["budget"],
        },
        "corpus_note": (
            "120 rows, the catalogue's hallucination_legal rows whose provenance path is "
            "rubric/probe_mappings/hallucination_legal.yaml v0.3.0 (30 per probe family). The "
            "5 further catalogue rows sourced from corpus/wave0/beaaa-15688-authored-scenarios.jsonl "
            "were not captured and are not in this ledger."
        ),
        "excluded_wave0_rows": cap["excluded_wave0_rows"],
        "family_rollup": cap["family_rollup"],
        "rows": rows,
    }
    dest = ROOT / args.out
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    print(f"wrote {dest}: {len(scored)} scored / {len(held)} held of {len(rows)} at {rubric_point}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
