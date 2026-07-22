"""BEAAA-16976 — pinned N=5 calibration on the locked offline golden set.

This command is retained for the BEAAA-16973 QA gate. It exercises the actual
rubric scoring boundary with Supreme Law pins on every invocation:
scanner_version, contract_version, corpus_version, config identifiers,
model_versions, and seeds 0..4. The Wave 1A scored candidate is P0
eligibility/referral only, so the required score movement is zero.
"""
from __future__ import annotations
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.golden_set.persist_baseline import _load_json  # noqa: E402
from rubric.scoring import Rubric, score  # noqa: E402

FIXTURES_DIR = REPO / "tests" / "fixtures" / "golden_agents"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
SCANNER_VERSION = "0.5.0"
CONTRACT_VERSION = "are-scanner-api-v1@v1.0"
CONFIG_IDENTIFIERS = {
    "rubric_path": "rubric/rubric.yaml",
    "wave1a_registry": "rubric/aiuc_path_b/family_registry.yaml",
    "active_test_registry": "tools/active_test_registry.py",
    "activation_packet_builder": "evals/aiuc_path_b/wave1a_candidate.py",
}
OUTPUT_PATH = (
    REPO
    / "tools"
    / "calibration-deltas"
    / "2026-07-22-beaaa16976-wave1a-scored-candidate-n5.json"
)


def _bytes_hash(obj: dict) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _pillar_scores(scored: dict) -> dict[str, float]:
    return {pid: float(block["score"]) for pid, block in scored["pillars"].items()}


def _spreads(rows: list[dict[str, float]]) -> dict[str, float]:
    keys = sorted(rows[0])
    return {key: round(max(row[key] for row in rows) - min(row[key] for row in rows), 4) for key in keys}


def _median(values: list[float]) -> float:
    return float(sorted(values)[len(values) // 2])


def _applicability_for(fdir: Path) -> tuple[dict | None, str | None]:
    path = fdir / "applicability.json"
    if not path.exists():
        return None, None
    payload = _load_json(path)
    return payload.get("applicability_decisions"), payload.get("applicability_policy_version")


def main() -> int:
    rubric = Rubric.load(RUBRIC_PATH)
    fixture_ids = sorted(
        d.name
        for d in FIXTURES_DIR.iterdir()
        if d.is_dir() and (d / "expected_score.json").exists()
    )
    n = 5
    deltas = {}
    overall_in_band = True
    for fid in fixture_ids:
        fdir = FIXTURES_DIR / fid
        eval_outputs = _load_json(fdir / "eval_outputs.json")
        expected = _load_json(fdir / "expected_score.json")
        applicability_decisions, applicability_policy_version = _applicability_for(fdir)
        corpus_version = expected.get("corpus_version") or "golden-offline@unknown"
        model_versions = expected.get("model_versions") or {}
        expected_hash = _bytes_hash(expected)
        per_run = []
        for seed in range(n):
            scored = score(
                rubric=rubric,
                eval_outputs=eval_outputs,
                forensic_bundle_uri=f"https://golden.local/{fid}",
                scored_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                snapshot_hash=f"N5-CALIBRATION-BEAAA-16976-{fid}-{seed}",
                scanner_version=SCANNER_VERSION,
                corpus_version=corpus_version,
                seed=seed,
                model_versions=model_versions,
                contract_version=CONTRACT_VERSION,
                applicability_decisions=applicability_decisions,
                applicability_policy_version=applicability_policy_version,
            )
            per_run.append({
                "are_total": scored["bmi"],
                "pillars": _pillar_scores(scored),
                "hash": _bytes_hash(scored),
                "scanner_version": scored["scanner_version"],
                "contract_version": scored["contract_version"],
                "corpus_version": scored["corpus_version"],
                "model_versions": scored["model_versions"] or {},
                "seed": scored["seed"],
            })
        totals = [r["are_total"] for r in per_run]
        spread = max(totals) - min(totals)
        expected_total = expected["bmi"]
        delta_vs_expected = max(abs(t - expected_total) for t in totals)
        pillar_rows = [r["pillars"] for r in per_run]
        pillar_spreads = _spreads(pillar_rows)
        expected_pillars = _pillar_scores(expected)
        pillar_median_delta = {
            pid: round(_median([row[pid] for row in pillar_rows]) - expected_pillars[pid], 4)
            for pid in expected_pillars
        }
        max_pillar_spread = max(pillar_spreads.values()) if pillar_spreads else 0
        max_abs_pillar_median_delta = (
            max(abs(value) for value in pillar_median_delta.values()) if pillar_median_delta else 0
        )
        in_band = (
            spread <= 1.0
            and delta_vs_expected <= 1.0
            and max_pillar_spread <= 1.0
            and max_abs_pillar_median_delta <= 1.0
        )
        overall_in_band = overall_in_band and in_band
        deltas[fid] = {
            "expected_total": expected_total,
            "expected_hash": expected_hash,
            "observed_totals_n5": totals,
            "spread_n5": spread,
            "delta_vs_expected_max": delta_vs_expected,
            "baseline_candidate_median_delta": round(_median(totals) - expected_total, 4),
            "expected_pillars": expected_pillars,
            "observed_pillars_n5": pillar_rows,
            "pillar_spreads_n5": pillar_spreads,
            "baseline_candidate_pillar_median_delta": pillar_median_delta,
            "max_pillar_spread_n5": max_pillar_spread,
            "max_abs_pillar_median_delta": max_abs_pillar_median_delta,
            "pins": {
                "scanner_version": SCANNER_VERSION,
                "contract_version": CONTRACT_VERSION,
                "corpus_version": corpus_version,
                "config_identifiers": CONFIG_IDENTIFIERS,
                "model_versions": model_versions,
                "seeds": list(range(n)),
            },
            "score_band_pricing_impact": {
                "are_total_delta": 0,
                "pillar_delta": 0,
                "band_delta": 0,
                "pricing_delta": 0,
            },
            "in_band_pm1": in_band,
        }
        marker = "PASS" if in_band else "FAIL"
        print(
            f"  {marker} {fid}: expected={expected_total} "
            f"observed={totals} spread={spread} "
            f"delta={delta_vs_expected:.4f} max_pillar_spread={max_pillar_spread:.4f} "
            f"max_pillar_delta={max_abs_pillar_median_delta:.4f}"
        )
    out = {
        "issue": "BEAAA-16976",
        "action": "rule-execution bug fix for QA-rejected Wave 1A scored candidate evidence",
        "classification_table_row": (
            "scanner-implementation-supervision-v1.md@v1.1 rows 'Smoke test for an existing operation' "
            "and 'Refactor that doesn't touch behavior'"
        ),
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "n": n,
        "scanner_version": SCANNER_VERSION,
        "contract_version": CONTRACT_VERSION,
        "corpus_version_source": "tests/fixtures/golden_agents/*/expected_score.json",
        "config_identifiers": CONFIG_IDENTIFIERS,
        "seeds": list(range(n)),
        "rubric_path": str(RUBRIC_PATH.relative_to(REPO)),
        "fixtures": list(deltas.keys()),
        "deltas": deltas,
        "all_in_band_pm1": overall_in_band,
        "score_band_pricing_impact": {
            "are_total_delta": 0,
            "pillar_delta": 0,
            "band_delta": 0,
            "pricing_delta": 0,
        },
        "note": (
            "Wave 1A is P0 eligibility/referral only. The candidate changes "
            "no scoring math, band cut, pricing path, judge model, or API "
            "output shape; required movement is 0 score points, 0 pillar "
            "delta, 0 band movement."
        ),
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {OUTPUT_PATH}")
    print(f"all_in_band_pm1={overall_in_band}")
    return 0 if overall_in_band else 1


if __name__ == "__main__":
    raise SystemExit(main())
