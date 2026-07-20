#!/usr/bin/env python3
"""BEAAA-6190 v0.5 RC N=5 golden-set gate."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

os.environ.setdefault("ARE_ALLOW_DEV_SIGNING_KEY", "true")

from api.bundle import _signing_key  # noqa: E402
from rubric.scoring import Rubric, ValidatedApplicability, score  # noqa: E402
from scanners import CONTRACT_VERSION, SCANNER_VERSION  # noqa: E402

RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
FIXTURES = REPO / "tests" / "fixtures" / "golden_agents"
OUT = REPO / "tools" / "calibration-deltas" / "2026-06-22-beaaa6190-v05-rc-n5.md"
SCORED_AT = datetime(2026, 5, 9, 0, 0, 0, tzinfo=timezone.utc)
SNAPSHOT_HASH = "sha256:" + "a" * 64
PILLARS = ("GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS")
CONTRACT_REFERENCES = (CONTRACT_VERSION, "scanner-implementation-supervision-v1@v1.1")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _score_fixture(rubric: Rubric, fixture_id: str) -> dict[str, Any]:
    root = FIXTURES / fixture_id
    expected = _load_json(root / "expected_score.json")
    eval_outputs = _load_json(root / "eval_outputs.json")
    applicability_decisions = None
    app_path = root / "applicability.json"
    if app_path.exists():
        applicability = _load_json(app_path)
        applicability_decisions = ValidatedApplicability._signed_from_policy_validation(
            applicability["applicability_decisions"],
            applicability["policy_version"],
            _signing_key(),
        )
    return score(
        rubric,
        eval_outputs,
        snapshot_hash=SNAPSHOT_HASH,
        forensic_bundle_uri=f"https://golden.local/{fixture_id}",
        scored_at=SCORED_AT,
        corpus_version=expected.get("corpus_version"),
        seed=expected.get("seed"),
        model_versions=expected.get("model_versions"),
        applicability_decisions=applicability_decisions,
        applicability_policy_version=(
            applicability_decisions.policy_version
            if applicability_decisions is not None else None
        ),
    )


def main() -> int:
    rubric = Rubric.load(RUBRIC_PATH)
    rows: list[dict[str, Any]] = []
    failures: list[str] = []

    for fixture_dir in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
        fixture_id = fixture_dir.name
        agent = _load_json(fixture_dir / "agent.json")
        if agent.get("status") != "live":
            rows.append({
                "fixture": fixture_id,
                "status": "pending_baseline_scan",
                "note": agent.get("status_note") or agent.get("baseline_scan_due") or "pending baseline",
            })
            continue

        runs = [_score_fixture(rubric, fixture_id) for _ in range(5)]
        totals = [int(r["bmi"]) for r in runs]
        pillar_spreads = {
            pillar: max(float(r["pillars"][pillar]["score"]) for r in runs)
            - min(float(r["pillars"][pillar]["score"]) for r in runs)
            for pillar in PILLARS
        }
        total_spread = max(totals) - min(totals)
        expected = _load_json(fixture_dir / "expected_score.json")
        byte_identical_to_expected = all(r == expected for r in runs)

        if total_spread > 1:
            failures.append(f"{fixture_id}: total spread {total_spread} > 1")
        for pillar, spread in pillar_spreads.items():
            if spread > 1:
                failures.append(f"{fixture_id}: {pillar} spread {spread} > 1")
        if not byte_identical_to_expected:
            failures.append(f"{fixture_id}: run output drifted from locked expected_score.json")

        rows.append({
            "fixture": fixture_id,
            "status": "live",
            "total": totals[0],
            "band": runs[0]["band"]["display"],
            "total_spread": total_spread,
            "pillar_spreads": pillar_spreads,
            "byte_identical_to_expected": byte_identical_to_expected,
        })

    live_rows = [r for r in rows if r["status"] == "live"]
    pending_rows = [r for r in rows if r["status"] != "live"]
    live_totals = [int(r["total"]) for r in live_rows]
    live_spread = max(live_totals) - min(live_totals) if live_totals else 0
    if live_spread < 40:
        failures.append(f"live golden discrimination spread {live_spread} < 40")

    lines = [
        "---",
        "artifact_type: other",
        "owner: cto",
        "persona_audience: internal",
        "phase: 2",
        "canonical_terms_only: true",
        'focus_claim: "This RC calibration gate preserves BackBond Scanner release discipline by proving live golden reproducibility before the v0.5 cut."',
        f"contracts_referenced: {json.dumps(CONTRACT_REFERENCES)}",
        'ticket_refs: ["BEAAA-6190", "BEAAA-6184", "BEAAA-6188", "BEAAA-6187", "BEAAA-7321"]',
        'proof_points_cited: ["tests/fixtures/golden_agents", "rubric/scoring.py", "scanners/__init__.py"]',
        "version: 0.1",
        "prior_version: null",
        "self_audit_passed: true",
        "escalations_noted: []",
        "---",
        "",
        "# BEAAA-6190 v0.5 RC N=5 Calibration Delta",
        "",
        f"- scanner_version: `{SCANNER_VERSION}`",
        f"- contract_version: `{CONTRACT_VERSION}`",
        f"- rubric_version: `{rubric.version}`",
        "- corpus_version: `fixture-pinned per golden expected_score.json`",
        "- probe-set-version: `golden_agents_v1`",
        "- rubric_point_id: `release-wide/all active BackBond Scanner rubric points`",
        f"- live_golden_count: `{len(live_rows)}`",
        f"- pending_golden_count: `{len(pending_rows)}`",
        f"- live_discrimination_spread: `{live_spread}`",
        f"- result: `{'PASS' if not failures else 'FAIL'}`",
        "",
        "| Agent | Status | Score | Band | Total spread | Max pillar spread | Byte-identical to expected |",
        "|---|---|---:|---|---:|---:|---|",
    ]
    for row in rows:
        if row["status"] != "live":
            lines.append(f"| {row['fixture']} | pending_baseline_scan | n/a | n/a | n/a | n/a | n/a |")
            continue
        max_pillar_spread = max(row["pillar_spreads"].values())
        lines.append(
            f"| {row['fixture']} | live | {row['total']} | {row['band']} | "
            f"{row['total_spread']} | {max_pillar_spread:.4f} | "
            f"{row['byte_identical_to_expected']} |"
        )
    if pending_rows:
        lines.extend(["", "## Pending Roster Slots", ""])
        for row in pending_rows:
            lines.append(f"- `{row['fixture']}`: {row['note']}")
    if failures:
        lines.extend(["", "## Failures", ""])
        lines.extend(f"- {failure}" for failure in failures)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
