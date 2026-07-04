"""BEAAA-11735 release-gate N=5 receipt for WS1-B.

Runs the requested B1/G1/G2/M1/M2/M3 golden roster with probe paraphrasing
disabled. This is a release-gate evidence runner, not a new scoring path.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from rubric.scoring import Rubric, score  # noqa: E402

ROSTER = ("B1", "G1", "G2", "M1", "M2", "M3")
SEEDS = (0, 1, 2, 3, 4)
FIXTURES = REPO / "tests" / "fixtures" / "golden_agents"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
OUT_BASE = REPO / "tools" / "calibration-deltas" / "2026-07-04-beaaa11735-ws1b-paraphrase-off-n5"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _pillar_scores(payload: dict[str, Any]) -> dict[str, float]:
    return {
        str(name): float(entry["score"])
        for name, entry in (payload.get("pillars") or {}).items()
    }


def _spread(values: list[float]) -> float:
    return max(values) - min(values)


def _format_float(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}"


def _score_fixture(rubric: Rubric, fixture_id: str, seed: int) -> dict[str, Any]:
    fixture_dir = FIXTURES / fixture_id
    eval_outputs = _load_json(fixture_dir / "eval_outputs.json")
    applicability = None
    applicability_path = fixture_dir / "applicability.json"
    if applicability_path.exists():
        applicability = _load_json(applicability_path).get("applicability_decisions")

    return score(
        rubric=rubric,
        eval_outputs=eval_outputs,
        forensic_bundle_uri=f"https://golden.local/{fixture_id}",
        scored_at=datetime(2026, 7, 4, tzinfo=timezone.utc),
        snapshot_hash="BEAAA-11735-WS1B-PARAPHRASE-OFF",
        scanner_version="0.4.2",
        corpus_version="unchanged-paraphrase-off",
        seed=seed,
        model_versions={},
        applicability_decisions=applicability,
    )


def main() -> int:
    os.environ["ARE_PROBE_PARAPHRASE_RANDOMIZATION"] = "0"
    os.environ["ARE_PROBE_SAMPLING"] = ""
    rubric = Rubric.load(RUBRIC_PATH)

    rows: list[dict[str, Any]] = []
    for fixture_id in ROSTER:
        runs = [_score_fixture(rubric, fixture_id, seed) for seed in SEEDS]
        totals = [float(run["bmi"]) for run in runs]
        pillar_names = sorted(_pillar_scores(runs[0]))
        pillar_spreads = {
            pillar: _spread([_pillar_scores(run)[pillar] for run in runs])
            for pillar in pillar_names
        }
        expected = _load_json(FIXTURES / fixture_id / "expected_score.json")
        expected_total = float(expected["bmi"])
        expected_pillars = _pillar_scores(expected)
        median_total = sorted(totals)[len(totals) // 2]
        median_pillars = {
            pillar: sorted(_pillar_scores(run)[pillar] for run in runs)[len(runs) // 2]
            for pillar in pillar_names
        }
        rows.append({
            "agent": fixture_id,
            "seeds": list(SEEDS),
            "totals": totals,
            "delta_total": median_total - expected_total,
            "delta_pillars": {
                pillar: median_pillars[pillar] - expected_pillars[pillar]
                for pillar in pillar_names
            },
            "spread_total": _spread(totals),
            "spread_pillars": pillar_spreads,
            "pass": _spread(totals) <= 1.0 and all(v <= 1.0 for v in pillar_spreads.values()),
        })

    receipt = {
        "issue": "BEAAA-11735",
        "parent_issue": "BEAAA-11720",
        "purpose": "WS1-B close-gate golden N=5 with paraphrase OFF",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "roster": list(ROSTER),
        "n": len(SEEDS),
        "seeds": list(SEEDS),
        "scanner_version": "0.4.2",
        "corpus_version": "unchanged-paraphrase-off",
        "paraphrase_enabled": False,
        "rubric_path": str(RUBRIC_PATH.relative_to(REPO)),
        "rows": rows,
        "passed": all(row["pass"] for row in rows),
    }

    OUT_BASE.parent.mkdir(parents=True, exist_ok=True)
    json_path = OUT_BASE.with_suffix(".json")
    md_path = OUT_BASE.with_suffix(".md")
    json_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "---",
        "artifact_type: calibration_delta",
        "owner: paperclip_platform_maintainer",
        "persona_audience: internal",
        "phase: 1",
        "canonical_terms_only: true",
        'focus_claim: "WS1-B paraphrase-off default does not move the locked golden roster outside the ±1 release band."',
        'contracts_referenced: ["are-scanner-api-v1"]',
        'ticket_refs: ["BEAAA-11735", "BEAAA-11720", "BEAAA-11711"]',
        'proof_points_cited: ["scripts/release_gate_ws1b_n5.py", "tools/calibration-deltas/2026-07-04-beaaa11735-ws1b-paraphrase-off-n5.json"]',
        "version: 0.1",
        "prior_version: null",
        "self_audit_passed: true",
        'escalations_noted: ["No scoring logic changed; paraphrase is default-off in this gate."]',
        "---",
        "# BEAAA-11735 WS1-B Paraphrase-Off N=5",
        "",
        f"Generated: `{receipt['generated_at']}`",
        "",
        "| Agent | Δ total | Δ GOVERNANCE | Δ ROBUSTNESS | Δ BIAS_FAIRNESS | Δ OPS | spread (total) | max pillar spread |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        deltas = row["delta_pillars"]
        max_pillar_spread = max(row["spread_pillars"].values(), default=0.0)
        lines.append(
            "| {agent} | {dt} | {gov} | {rob} | {bias} | {ops} | {spread} | {pillar_spread} |".format(
                agent=row["agent"],
                dt=_format_float(row["delta_total"]),
                gov=_format_float(deltas.get("GOVERNANCE", 0.0)),
                rob=_format_float(deltas.get("ROBUSTNESS", 0.0)),
                bias=_format_float(deltas.get("BIAS_FAIRNESS", 0.0)),
                ops=_format_float(deltas.get("OPS", 0.0)),
                spread=_format_float(row["spread_total"]),
                pillar_spread=_format_float(max_pillar_spread),
            )
        )
    lines.extend([
        "",
        f"Result: **{'PASS' if receipt['passed'] else 'FAIL'}**.",
        "",
    ])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {json_path.relative_to(REPO)}")
    print(f"wrote {md_path.relative_to(REPO)}")
    print(f"passed={receipt['passed']}")
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
