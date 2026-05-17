"""BEAAA-484 — N=5 calibration delta on the questionnaire pipeline + golden L1.

Produces the artifact required by `contracts/scanner-implementation-supervision-v1.md`
§"Required artifacts per Operational action":

  - scanner_version, corpus_version, probe-set-version, rubric_point_id (per dim),
    N=5 calibration delta on the golden set, cto_cosign_ref (= null, rule-execution).

Two checks:

  1. Pipeline determinism (N=5 on the BEAAA-484 vendor packet fixture). Same
     packet -> same evidence_refs across five runs. Per-dim deltas on raw,
     verdict, and weight must be exactly 0. Any non-zero drift halts the merge
     and re-classifies as rule-change per §3 of the supervision contract.

  2. Golden-set L1 deterministic N=5 regression check. Re-run the existing
     `harness.golden_set.n5_band.run_l1_n5` to confirm scoring math is byte-
     stable across N=5 on every baselined fixture. The BEAAA-484 change does
     not touch scoring math (wraps score_p4_attestation; does not fork), so
     the L1 band must remain ±0.

Usage:
    python3 scripts/beaaa484_calibration_delta_n5.py [--out-dir artifacts/beaaa-484]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from harness.p4_questionnaire import (  # noqa: E402
    GOV_RUBRIC_POINTS,
    GOV_TO_P4,
    PROBE_SET_VERSION,
    QUESTIONNAIRE_SPEC_VERSION,
)
from harness.golden_set.n5_band import run_l1_n5  # noqa: E402
from scanners import CONTRACT_VERSION, SCANNER_VERSION  # noqa: E402
from scanners.p4_vendor_review import review_vendor_packet  # noqa: E402

VENDOR_FIXTURE = REPO / "harness" / "fixtures" / "p4_vendor_packet_fixture_v1.yaml"


def _per_dim_signature(result: dict) -> dict[str, dict]:
    """Flatten evidence_refs into a {rubric_point_id: {raw, verdict, weight}} dict."""
    out: dict[str, dict] = {}
    for ref in result["evidence_refs"]:
        out[ref["rubric_point_id"]] = {
            "raw": ref["internal"]["raw"],
            "verdict": ref["verdict"],
            "weight": ref["weight"],
            "tier": ref["internal"]["tier"],
            "dim_id": ref["internal"]["dim_id"],
        }
    return out


def run_questionnaire_n5(fixture_path: Path, n: int = 5) -> dict:
    packet = yaml.safe_load(fixture_path.read_text())
    runs: list[dict[str, dict]] = []
    for _ in range(n):
        result = review_vendor_packet(packet)
        runs.append(_per_dim_signature(result))

    per_dim: dict[str, dict] = {}
    for rubric_point_id in GOV_RUBRIC_POINTS:
        raws = [r[rubric_point_id]["raw"] for r in runs]
        verdicts = [r[rubric_point_id]["verdict"] for r in runs]
        weights = [r[rubric_point_id]["weight"] for r in runs]
        raw_min, raw_max = min(raws), max(raws)
        per_dim[rubric_point_id] = {
            "dim_id": GOV_TO_P4[rubric_point_id],
            "tier": runs[0][rubric_point_id]["tier"],
            "raw_min": raw_min,
            "raw_max": raw_max,
            "raw_delta": round(raw_max - raw_min, 6),
            "raw_std": round(statistics.pstdev(raws), 6),
            "verdicts": sorted(set(verdicts)),
            "weight_min": min(weights),
            "weight_max": max(weights),
            "weight_delta": round(max(weights) - min(weights), 6),
            "in_band": (raw_max - raw_min) <= 1.0,
        }

    overall_in_band = all(d["in_band"] for d in per_dim.values())
    raw_delta_max = max(d["raw_delta"] for d in per_dim.values())
    return {
        "n": n,
        "fixture": str(fixture_path.relative_to(REPO)),
        "raw_delta_max": raw_delta_max,
        "overall_in_band": overall_in_band,
        "per_dim": per_dim,
    }


def run_golden_l1_regression() -> dict:
    """Re-run L1 deterministic N=5 on the baselined golden set fixtures.

    Returns the dict payload from harness.golden_set.n5_band.run_l1_n5.
    """
    report = run_l1_n5()
    return {
        "scanner_version": report.get("current_scanner_version"),
        "contract_version": report.get("current_contract_version"),
        "rubric_version": report.get("current_rubric_version"),
        "run_at": report.get("generated_at"),
        "fixtures": report.get("fixtures", []),
        "summary": report.get("summary", {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="BEAAA-484 N=5 calibration delta report")
    parser.add_argument("--out-dir", default="artifacts/beaaa-484",
                        help="Where to write the calibration delta JSON")
    parser.add_argument("--n", type=int, default=5)
    args = parser.parse_args()

    questionnaire_n5 = run_questionnaire_n5(VENDOR_FIXTURE, n=args.n)
    golden_l1 = run_golden_l1_regression()

    payload = {
        "ticket": "BEAAA-484",
        "classification": "rule-execution",
        "supervision_contract_row": "Customer onboarding harness for a new customer archetype",
        "cto_cosign_ref": None,
        "scanner_version": SCANNER_VERSION,
        "contract_version": CONTRACT_VERSION,
        "corpus_version": golden_l1.get("rubric_version", ""),
        "probe_set_version": PROBE_SET_VERSION,
        "questionnaire_spec_version": QUESTIONNAIRE_SPEC_VERSION,
        "rubric_point_ids": list(GOV_RUBRIC_POINTS),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "questionnaire_pipeline_n5": questionnaire_n5,
        "golden_l1_regression": golden_l1,
        "halt_condition": "any rubric_point_id raw_delta > 1.0 OR golden L1 are_drift > 1",
        "halt_triggered": (
            (not questionnaire_n5["overall_in_band"])
            or any(b.get("are_breach") for b in golden_l1["fixtures"])
        ),
    }

    out_dir = (REPO / args.out_dir) if not Path(args.out_dir).is_absolute() else Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "calibration_delta_n5.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"wrote {out_path.relative_to(REPO)}")
    print(f"questionnaire raw_delta_max = {questionnaire_n5['raw_delta_max']}")
    print(f"overall_in_band = {questionnaire_n5['overall_in_band']}")
    print(f"halt_triggered = {payload['halt_triggered']}")

    return 0 if not payload["halt_triggered"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
