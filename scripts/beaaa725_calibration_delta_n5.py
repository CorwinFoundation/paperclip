"""BEAAA-725 - N=5 calibration delta on the locked golden set after the
M3 (Mistral Le Chat) re-baseline under HoUW BEAAA-722 C1-C3.

This action adds the M3 fixture to scripts/path_a_baselines.py
FIXTURE_CONFIG (substrate flip mistral_direct_api -> mistral_via_openrouter
authorized by HoUW BEAAA-722 ACCEPT 2026-05-24T18:01:59Z, interaction
b40024d9; CTO Path 3 ratification BEAAA-677) and persists the live
Path A baseline (are_total=54, band=D+, C1 abs_delta=4 <= 5pp).

Classification: rule-execution per scanner-implementation-supervision
-v1.md@v1.0 row "Smoke test for an existing operation". HoUW co-sign
is on the substrate flip (BEAAA-722); no rubric / weight / judge /
scoring math / contract output shape change. The script reuses the
existing P1.1 / P3.1 / P3.2 / P3.3 probe wrappers plus the
mistral_le_chat 17-dim prior; no new probe family is introduced.

Per Supreme Law (CTO agents/cto-os.md Reproducibility +/-1):
**byte-identical replay** of every locked golden across N=5 runs.
"in band" here means spread=0 AND delta_vs_expected_max <= 1.0 AND
byte-identical replay across all 5 runs.

Mirrors scripts/beaaa724_calibration_delta_n5.py exactly. Output
filed under tools/calibration-deltas/2026-05-24-beaaa725-m3-
re-execution-n5.json per supervision contract artifact convention.
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
    deltas = {}
    overall_in_band = True
    for fid in fixture_ids:
        fdir = FIXTURES_DIR / fid
        eval_outputs = _load_json(fdir / "eval_outputs.json")
        expected = _load_json(fdir / "expected_score.json")
        expected_hash = _bytes_hash(expected)
        applicability_decisions = None
        app_path = fdir / "applicability.json"
        if app_path.exists():
            applicability_decisions = _load_json(app_path).get("applicability_decisions")
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
                applicability_policy_version=expected.get("applicability_policy_version"),
            )
            per_run.append({
                "are_total": scored["bmi"],
                "rubric_version": scored.get("rubric_version"),
                "hash": _bytes_hash(scored),
            })
        totals = [r["are_total"] for r in per_run]
        spread = max(totals) - min(totals)
        all_byte_identical = len({r["hash"] for r in per_run}) == 1
        expected_total = expected["bmi"]
        delta_vs_expected = max(abs(t - expected_total) for t in totals)
        in_band = (
            spread == 0
            and delta_vs_expected <= 1.0
            and all_byte_identical
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
        "issue": "BEAAA-725",
        "action": (
            "rule-execution (Smoke test for an existing operation - "
            "M3 Path A baseline re-execution under HoUW BEAAA-722 "
            "substrate-flip C-block; reuses existing P1.1/P3.1/P3.2/"
            "P3.3 probe wrappers plus mistral_le_chat 17-dim prior; no "
            "rubric / weight / judge / scoring math / contract output "
            "shape change)"
        ),
        "classification_table_row": (
            "scanner-implementation-supervision-v1.md@v1.0 row "
            "'Smoke test for an existing operation'"
        ),
        "substrate_flip_cosign_ref": "BEAAA-722",
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "n": n,
        "rubric_path": str(RUBRIC_PATH.relative_to(REPO)),
        "fixtures": list(deltas.keys()),
        "deltas": deltas,
        "all_in_band_pm1_and_byte_identical": overall_in_band,
        "note": (
            "N=5 ARE drift = 0 by construction on the deterministic-"
            "replay path against the locked eval_outputs. The M3 entry "
            "in `deltas` is the new fixture under test; the remaining "
            "8 fixtures act as regression backstop confirming the M3 "
            "addition did not perturb any previously locked baseline."
        ),
    }
    out_path = (
        REPO
        / "tools"
        / "calibration-deltas"
        / "2026-05-24-beaaa725-m3-re-execution-n5.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {out_path}")
    print(f"all_in_band_pm1_and_byte_identical={overall_in_band}")
    return 0 if overall_in_band else 1


if __name__ == "__main__":
    raise SystemExit(main())
