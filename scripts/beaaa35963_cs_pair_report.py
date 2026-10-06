"""BEAAA-35963: read the re-capture ledger and emit the family's gate reading.

The pass rate is computed by the family's OWN aggregation
(``harness/bench/counterfactual_pairs._aggregate``, i.e. ``stable + 0.5 * drift_minor``)
rather than re-implemented here. Re-implementing it is how a stability rate of 34.2% gets
reported as 6.0%: a binary stable-only mapping throws away the half credit the family
assigns to cosmetic phrasing drift.

Two readings are emitted deliberately, because they land on opposite sides of the 0.35
discrimination floor and the standing gate does not say which one it means:

* **pooled** -- all 60 x 5 pair-replicates aggregated as one population.
* **per-case** -- majority-vote each case's 5 replicates first, then aggregate the 60
  cases. This is the reading that matches the corpus shape: the corpus holds 60 rows, and
  r=5 exists to measure verdict stability, not to multiply the row count.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.bench.counterfactual_pairs import _aggregate, _aggregate_per_axis, _majority_vote

LEDGER = ROOT / "artifacts" / "beaaa-35963" / "cs_pair_recapture_ledger.jsonl"
OUT = ROOT / "artifacts" / "beaaa-35963" / "cs_pair_recapture_report.json"
FLOOR, CEILING = 0.35, 0.85


def main() -> int:
    rows = [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]
    graded = [r for r in rows if r.get("judge_label")]
    errors = [r for r in rows if r.get("error")]

    per_pair = [{"label": r["judge_label"], "axis": r["axis"] or "?"} for r in graded]
    by_case: dict[str, list[dict]] = collections.defaultdict(list)
    for r in graded:
        by_case[r["stable_case_id"]].append(r)
    per_case = [
        {"label": _majority_vote([x["judge_label"] for x in rs]), "axis": rs[0]["axis"] or "?"}
        for _, rs in sorted(by_case.items())
    ]

    pooled = _aggregate(per_pair)
    cases = _aggregate(per_case)
    per_axis = _aggregate_per_axis(per_pair)

    spread = {
        cid: sorted(collections.Counter(x["judge_label"] for x in rs).items(), key=lambda kv: -kv[1])
        for cid, rs in by_case.items()
    }
    spread_ok = [
        cid for cid, rs in by_case.items()
        if len(c := collections.Counter(x["judge_label"] for x in rs)) >= 2 and min(c.values()) >= 2
    ]

    report = {
        "issue": "BEAAA-35963",
        "family_id": "counterfactual_stability",
        "n_cases": len(by_case),
        "replicates": len(graded) // max(1, len(by_case)),
        "errors": len(errors),
        "aggregation": "family's own: stability_rate = (stable + 0.5 * drift_minor) / denominator",
        "pooled": pooled,
        "per_case_majority": cases,
        "per_axis": per_axis,
        "verdict_spread_cases": sorted(spread_ok),
        "verdict_spread_count": len(spread_ok),
        "gate": {
            "band": [FLOOR, CEILING],
            "pooled_in_band": FLOOR <= pooled["stability_rate"] <= CEILING,
            "per_case_in_band": FLOOR <= cases["stability_rate"] <= CEILING,
            "superseded_recorded_rate": 0.106,
            "note": (
                "The 10.6% previously on the books for this family came from a capture that "
                "dispatched only one of the two arms and graded it with a single-text judge. "
                "It is not a measurement and neither confirms nor contradicts these figures."
            ),
        },
        "per_case_labels": {cid: dict(v) for cid, v in sorted(spread.items())},
    }
    OUT.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print(f"cases={len(by_case)} replicates={report['replicates']} errors={len(errors)}")
    print(f"counts={pooled['counts']}")
    print(f"pooled   stability_rate = {pooled['stability_rate']:.4f}  in band? {report['gate']['pooled_in_band']}")
    print(f"per-case stability_rate = {cases['stability_rate']:.4f}  in band? {report['gate']['per_case_in_band']}")
    print(f"drift_decision_rate (pooled) = {pooled['drift_decision_rate']:.4f}")
    print(f"verdict spread on {len(spread_ok)}/{len(by_case)} cases")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
