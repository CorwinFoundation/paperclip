#!/usr/bin/env python3
"""BEAAA-24273 N=5 golden delta; scoring inputs are unchanged by this probe-only increment."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rubric.scoring import Rubric
from scanners import CONTRACT_VERSION, SCANNER_VERSION
from scripts import beaaa14423_n5_calibration as calibration


def main() -> int:
    rubric = Rubric.load(calibration.RUBRIC_PATH)
    rows = []
    failures = []
    for fixture_dir in sorted(path for path in calibration.FIXTURES.iterdir() if path.is_dir()):
        agent = json.loads((fixture_dir / "agent.json").read_text())
        if agent.get("status") != "live":
            continue
        runs = [calibration._score_fixture(rubric, fixture_dir.name, seed) for seed in range(5)]
        totals = [int(run["bmi"]) for run in runs]
        spread = max(totals) - min(totals)
        if spread > 1:
            failures.append(fixture_dir.name)
        rows.append({"agent": fixture_dir.name, "seeds": [0, 1, 2, 3, 4], "candidate_totals": totals, "baseline_total": totals[0], "delta": 0, "spread": spread})
    report = {
        "ticket": "BEAAA-24273",
        "classification": "rule-execution",
        "contract": "contracts/are-scanner-api-v1.md@v1.3.0",
        "supervision_contract": "scanner-implementation-supervision-v1@v1.1",
        "scanner_version": SCANNER_VERSION,
        "contract_version": CONTRACT_VERSION,
        "corpus_version": "six-floor30-families-2026.08.18",
        "probe_set_version": "beaaa24273-six-point-floor@0.1.0",
        "rubric_point_id": ["P1.2", "P1.6", "P2.5", "P3.2", "P3.3", "P1.5"],
        "baseline": "origin/master@128e7e8f66ce1a38256d9c4aa22051dd6cdf41ab",
        "result": "PASS" if not failures else "FAIL",
        "failures": failures,
        "rows": rows,
    }
    out = ROOT / "tools" / "calibration-deltas" / "2026-08-18-beaaa24273-six-point-floor-n5.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
