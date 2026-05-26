"""BEAAA-334 AC #5 — N=5 reproducibility band re-verification with proxy ON.

Confirms that introducing the Decodo (Smartproxy) residential proxy infra did
not perturb scoring math. The L1 reproducibility layer re-scores each locked
`eval_outputs.json` fixture N=5 times against `rubric.scoring.score()`; the
scoring engine never touches the network, so a `BEAAA_RESI_PROXY_URL`-set
environment must still produce byte-identical replay across the run.

Acceptance per BEAAA-334 AC #5:
  - For every golden fixture: spread_n5 == 0
  - delta_vs_expected_max <= 1.0
  - byte_identical_replay_n5 == True

Classification: rule-execution (Smoke test for an existing operation /
infra-POC reproducibility re-verification). Mirrors
scripts/beaaa725_calibration_delta_n5.py byte-for-byte except for the issue
tag and the output path. No rubric / weight / judge / scoring math /
contract-output-shape touch.
"""
from __future__ import annotations
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.golden_set.persist_baseline import _load_json  # noqa: E402
from rubric.scoring import Rubric, score  # noqa: E402

FIXTURES_DIR = REPO / "tests" / "fixtures" / "golden_agents"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
OUT_DIR = REPO / "artifacts" / "beaaa-334"


def _bytes_hash(obj: dict) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main() -> int:
    rubric = Rubric.load(RUBRIC_PATH)
    fixture_ids = sorted(
        d.name
        for d in FIXTURES_DIR.iterdir()
        if d.is_dir() and (d / "expected_score.json").exists()
    )
    n = 5
    deltas: dict[str, dict] = {}
    overall_in_band = True
    proxy_enabled = bool(os.environ.get("BEAAA_RESI_PROXY_URL"))
    proxy_rotate = os.environ.get("BEAAA_RESI_PROXY_ROTATE", "")

    for fid in fixture_ids:
        fdir = FIXTURES_DIR / fid
        eval_outputs = _load_json(fdir / "eval_outputs.json")
        expected = _load_json(fdir / "expected_score.json")
        expected_hash = _bytes_hash(expected)
        applicability_decisions = None
        app_path = fdir / "applicability.json"
        if app_path.exists():
            applicability_decisions = _load_json(app_path).get(
                "applicability_decisions"
            )
        per_run = []
        for _ in range(n):
            scored = score(
                rubric=rubric,
                eval_outputs=eval_outputs,
                forensic_bundle_uri=f"https://golden.local/{fid}",
                scored_at=datetime(2026, 5, 9, 0, 0, 0, tzinfo=timezone.utc),
                snapshot_hash="sha256:" + "a" * 64,
                corpus_version=expected.get("corpus_version") or None,
                seed=expected.get("seed"),
                model_versions=expected.get("model_versions"),
                applicability_decisions=applicability_decisions,
                applicability_policy_version=expected.get(
                    "applicability_policy_version"
                ),
            )
            per_run.append(
                {
                    "are_total": scored["bmi"],
                    "rubric_version": scored.get("rubric_version"),
                    "hash": _bytes_hash(scored),
                }
            )
        totals = [r["are_total"] for r in per_run]
        spread = max(totals) - min(totals)
        all_byte_identical = len({r["hash"] for r in per_run}) == 1
        expected_total = expected["bmi"]
        delta_vs_expected = max(abs(t - expected_total) for t in totals)
        in_band = (
            spread == 0 and delta_vs_expected <= 1.0 and all_byte_identical
        )
        overall_in_band = overall_in_band and in_band
        deltas[fid] = {
            "expected_total": expected_total,
            "expected_hash": expected_hash,
            "observed_totals_n5": totals,
            "spread_n5": spread,
            "byte_identical_replay_n5": all_byte_identical,
            "delta_vs_expected_max": delta_vs_expected,
            "rubric_version_observed": per_run[0]["rubric_version"],
            "in_band_pm1_and_byte_identical": in_band,
        }
        marker = "OK" if in_band else "FAIL"
        print(
            f"  [{marker}] {fid}: expected={expected_total} "
            f"observed={totals} spread={spread} "
            f"delta={delta_vs_expected:.4f} byte_id={all_byte_identical}"
        )

    out = {
        "issue": "BEAAA-334",
        "ac": "AC #5 — Reproducibility band (±1, N=5) under proxy ON",
        "action": (
            "rule-execution (infra-POC reproducibility re-verification; "
            "proxy is network-layer infra and cannot perturb pure scoring "
            "math — this run proves the no-touch contract empirically)"
        ),
        "proxy_enabled": proxy_enabled,
        "proxy_rotate": proxy_rotate,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "n": n,
        "fixtures": deltas,
        "overall_in_band_pm1_and_byte_identical": overall_in_band,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "n5_proxy_on.json"
    out_path.write_text(json.dumps(out, indent=2, sort_keys=True))
    print(f"\n  proxy_enabled={proxy_enabled} rotate={proxy_rotate}")
    print(f"  overall_in_band={overall_in_band}  ->  {out_path}")
    return 0 if overall_in_band else 1


if __name__ == "__main__":
    raise SystemExit(main())
