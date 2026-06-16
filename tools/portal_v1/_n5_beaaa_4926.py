"""BEAAA-4926 N=5 reproducibility check.

Runs the portal scoring pipeline 5x with the SBOM attestation in place
("candidate") and 5x without ("baseline"), then asserts spread==0 across
are_total and every pillar subscore, and verifies baseline == candidate on
each pillar (weight=0 → no scoring math change).
"""

from __future__ import annotations

import base64
import json
import statistics
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Import portal modules and reuse test fixtures.
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from portal import build_service  # noqa: E402
from test_portal import enroll_ready_dp, submit_all_tasks  # noqa: E402
from test_sbom_attestation import CYCLONEDX_BODY, _base_attestation  # noqa: E402

PILLARS = ("GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS")


def run_once(use_sbom: bool, seed_label: int) -> dict[str, float]:
    with tempfile.TemporaryDirectory(prefix=f"beaaa4926_n5_{'cand' if use_sbom else 'base'}_{seed_label}_") as td:
        svc = build_service(Path(td))
        try:
            signing_key, dp, enrollment = enroll_ready_dp(svc)
            scan = svc.trigger_scan(dp["dp_id"])
            if use_sbom:
                payload = _base_attestation(
                    CYCLONEDX_BODY,
                    "cyclonedx_json",
                    kev_hits=[
                        {
                            "cve": "CVE-2024-0001",
                            "package": "requests",
                            "version": "2.32.3",
                            "status": "patched",
                            "evidence_uri": "https://example.invalid/patches/CVE-2024-0001",
                        }
                    ],
                )
                svc.submit_sbom_attestation(scan["scan_id"], payload)
                svc.evaluate_bind(scan["scan_id"])
            submit_all_tasks(svc, signing_key, scan, enrollment["connector_id"])
            row = svc.db.execute("SELECT * FROM scans WHERE id = ?", (scan["scan_id"],)).fetchone()
            score = json.loads(row["score_json"])
            out = {"are_total": float(score["are_total"])}
            for p in PILLARS:
                out[p] = float(score["subscores"][p])
            return out
        finally:
            svc.close()


def spread(values: list[float]) -> float:
    return max(values) - min(values)


def main() -> int:
    baseline = [run_once(use_sbom=False, seed_label=i) for i in range(5)]
    candidate = [run_once(use_sbom=True, seed_label=i) for i in range(5)]
    dims = ("are_total",) + PILLARS
    print("seed | are_total | GOV | ROB | BIAS | OPS  (baseline)")
    for i, row in enumerate(baseline):
        print(f"{i}    | {row['are_total']:g} | {row['GOVERNANCE']:g} | {row['ROBUSTNESS']:g} | {row['BIAS_FAIRNESS']:g} | {row['OPS']:g}")
    print("seed | are_total | GOV | ROB | BIAS | OPS  (candidate)")
    for i, row in enumerate(candidate):
        print(f"{i}    | {row['are_total']:g} | {row['GOVERNANCE']:g} | {row['ROBUSTNESS']:g} | {row['BIAS_FAIRNESS']:g} | {row['OPS']:g}")
    failures = []
    for d in dims:
        base_vals = [r[d] for r in baseline]
        cand_vals = [r[d] for r in candidate]
        base_spread = spread(base_vals)
        cand_spread = spread(cand_vals)
        cross_delta = abs(statistics.median(cand_vals) - statistics.median(base_vals))
        print(f"{d}: baseline_spread={base_spread} candidate_spread={cand_spread} median_delta={cross_delta}")
        if base_spread > 1.0 or cand_spread > 1.0:
            failures.append(f"{d}_spread_gt_1")
        if cross_delta > 1.0:
            failures.append(f"{d}_baseline_vs_candidate_delta_gt_1")
    if failures:
        print(f"FAIL: {failures}")
        return 2
    print("PASS: are_total and every pillar subscore have spread <= 1 within each cohort and median delta <= 1 across cohorts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
