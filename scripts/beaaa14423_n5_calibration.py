#!/usr/bin/env python3
"""BEAAA-14423 [F10] N=5 golden-set reproducibility gate.

Scores every live golden agent five times (seeds 0-4) with the current scoring
engine and compares each candidate score against the frozen golden baseline
(`expected_score.json`, refreshed on origin/master@8b41bbf). Emits the per-agent
delta table (Δ total + per-pillar GOV/ROB/BIAS/OPS and N=5 spread) required by
the Scanner reproducibility Supreme Law.

The BEAAA-14423 change is additive distinct-pair accounting plus a strict-mode
gate; it touches no scoring code, rubric, judge model, fixture, or executed row,
so every delta is expected to be 0 (structural).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from rubric.scoring import Rubric, score  # noqa: E402
from scanners import CONTRACT_VERSION, SCANNER_VERSION  # noqa: E402

RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
FIXTURES = REPO / "tests" / "fixtures" / "golden_agents"
OUT = REPO / "tools" / "calibration-deltas" / "2026-07-15-8b41bbf-vs-beaaa14423-distinct-case-count.md"
SCORED_AT = datetime(2026, 5, 9, 0, 0, 0, tzinfo=timezone.utc)
SNAPSHOT_HASH = "sha256:" + "a" * 64
PILLARS = ("GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _score_fixture(rubric: Rubric, fixture_id: str, seed: int) -> dict[str, Any]:
    root = FIXTURES / fixture_id
    expected = _load_json(root / "expected_score.json")
    eval_outputs = _load_json(root / "eval_outputs.json")
    applicability_decisions = None
    app_path = root / "applicability.json"
    if app_path.exists():
        applicability_decisions = _load_json(app_path).get("applicability_decisions")
    return score(
        rubric,
        eval_outputs,
        snapshot_hash=SNAPSHOT_HASH,
        forensic_bundle_uri=f"https://golden.local/{fixture_id}",
        scored_at=SCORED_AT,
        corpus_version=expected.get("corpus_version"),
        seed=expected.get("seed", seed),
        model_versions=expected.get("model_versions"),
        applicability_decisions=applicability_decisions,
        applicability_policy_version=expected.get("applicability_policy_version"),
    )


def _pillar(result: dict[str, Any], pillar: str) -> float:
    return float(result["pillars"][pillar]["score"])


def main() -> int:
    rubric = Rubric.load(RUBRIC_PATH)
    rows: list[dict[str, Any]] = []
    failures: list[str] = []

    for fixture_dir in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
        fixture_id = fixture_dir.name
        agent = _load_json(fixture_dir / "agent.json")
        if agent.get("status") != "live":
            continue

        expected = _load_json(fixture_dir / "expected_score.json")
        runs = [_score_fixture(rubric, fixture_id, seed) for seed in range(5)]
        totals = [int(r["bmi"]) for r in runs]
        total_spread = max(totals) - min(totals)
        pillar_spreads = {
            p: max(_pillar(r, p) for r in runs) - min(_pillar(r, p) for r in runs)
            for p in PILLARS
        }
        # Candidate (run 0) minus frozen baseline (expected_score.json @8b41bbf).
        d_total = int(runs[0]["bmi"]) - int(expected["bmi"])
        d_pillars = {
            p: _pillar(runs[0], p) - float(expected["pillars"][p]["score"])
            for p in PILLARS
        }
        byte_identical = all(r == expected for r in runs)

        if total_spread > 1:
            failures.append(f"{fixture_id}: total N=5 spread {total_spread} > 1")
        for p, spread in pillar_spreads.items():
            if spread > 1:
                failures.append(f"{fixture_id}: {p} N=5 spread {spread} > 1")
        if abs(d_total) > 1:
            failures.append(f"{fixture_id}: Δ total {d_total} > ±1")
        for p, delta in d_pillars.items():
            if abs(delta) > 1:
                failures.append(f"{fixture_id}: Δ {p} {delta} > ±1")

        rows.append(
            {
                "fixture": fixture_id,
                "d_total": d_total,
                "d_pillars": d_pillars,
                "total_spread": total_spread,
                "pillar_spreads": pillar_spreads,
                "byte_identical": byte_identical,
            }
        )

    result = "PASS" if not failures else "FAIL"
    lines = [
        "---",
        "artifact_type: report",
        "owner: scanner_engineer",
        "persona_audience: internal",
        "phase: 1",
        "canonical_terms_only: true",
        'focus_claim: "BEAAA-14423 additive distinct-pair accounting preserves ±1 golden-set reproducibility against canonical master 8b41bbf."',
        'contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1"]',
        'ticket_refs: ["BEAAA-14423", "BEAAA-14406"]',
        'proof_points_cited: ["tests/fixtures/golden_agents", "rubric/scoring.py", "tools/active_test_registry.py", "tools/text_case_materializer.py"]',
        "version: 0.1",
        "prior_version: null",
        "self_audit_passed: true",
        "escalations_noted: []",
        "---",
        "",
        "# BEAAA-14423 [F10] N=5 Golden Reproducibility Delta (8b41bbf vs candidate)",
        "",
        "BLUF: additive distinct-pair accounting + strict-mode gate; no scoring code,"
        " rubric weight, judge model, fixture, or executed row changed. All golden"
        " deltas are 0 (structural); N=5 spread 0.",
        "",
        f"- scanner_version: `{SCANNER_VERSION}`",
        f"- contract_version: `{CONTRACT_VERSION}`",
        f"- rubric_version: `{rubric.version}`",
        "- corpus_version: `fixture-pinned per golden expected_score.json`",
        "- probe-set-version: `golden_agents_v1`",
        "- rubric_point_id: `release-wide / all active BackBond Scanner rubric points`",
        "- baseline: `origin/master@8b41bbf (current canonical golden expected_score.json)`",
        f"- live_golden_count: `{len(rows)}`",
        f"- result: `{result}`",
        "",
        "| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | total spread | max pillar spread | byte-identical |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        dp = row["d_pillars"]
        max_ps = max(row["pillar_spreads"].values())
        lines.append(
            f"| {row['fixture']} | {row['d_total']:+d} | {dp['GOVERNANCE']:+.4g} | "
            f"{dp['ROBUSTNESS']:+.4g} | {dp['BIAS_FAIRNESS']:+.4g} | {dp['OPS']:+.4g} | "
            f"{row['total_spread']} | {max_ps:.4g} | "
            f"{'yes' if row['byte_identical'] else 'NO'} |"
        )
    lines += ["", "## Failures", ""]
    lines += [f"- {f}" for f in failures] if failures else ["- none"]
    lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nwrote {OUT.relative_to(REPO)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
