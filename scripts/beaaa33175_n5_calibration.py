"""BEAAA-33175 N=5 seeds 0..4 golden calibration.

Run this from a repo root; it writes one JSON payload describing the median
total and pillar medians of every live golden agent across seeds 0-4. Run it
once on the baseline tree and once on the candidate tree and diff the two
payloads: the D2 + mapping-status-repair change is a registry-state change, so
the expectation is byte-identical scoring output.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.bundle import _signing_key  # noqa: E402
from rubric.scoring import Rubric, ValidatedApplicability, score  # noqa: E402

GOLDENS = ROOT / "tests" / "fixtures" / "golden_agents"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def pillars(payload: dict) -> dict[str, float]:
    return {key: float(value["score"]) for key, value in payload["pillars"].items()}


def main() -> int:
    os.environ.setdefault("ARE_ALLOW_DEV_SIGNING_KEY", "true")
    rubric = Rubric.load(ROOT / "rubric" / "rubric.yaml")
    rows = []
    for fixture_dir in sorted(GOLDENS.iterdir()):
        if not (fixture_dir / "expected_score.json").exists():
            continue
        agent_path = fixture_dir / "agent.json"
        if agent_path.exists() and load(agent_path).get("status") != "live":
            continue
        expected = load(fixture_dir / "expected_score.json")
        applicability_path = fixture_dir / "applicability.json"
        applicability_payload = load(applicability_path) if applicability_path.exists() else {}
        decisions = applicability_payload.get("applicability_decisions")
        policy_version = applicability_payload.get("policy_version")
        validated_applicability = (
            ValidatedApplicability._signed_from_policy_validation(
                decisions, policy_version, _signing_key()
            )
            if decisions and policy_version
            else None
        )
        totals, pillar_runs = [], []
        for seed in range(5):
            result = score(
                rubric=rubric,
                eval_outputs=load(fixture_dir / "eval_outputs.json"),
                forensic_bundle_uri=f"https://golden.local/{fixture_dir.name}",
                scored_at=datetime(2026, 9, 16, tzinfo=timezone.utc),
                snapshot_hash=f"BEAAA-33175-{fixture_dir.name}-{seed}",
                scanner_version="0.5.0",
                corpus_version=expected.get("corpus_version", "0.1.0"),
                seed=seed,
                model_versions=expected.get("model_versions", {}),
                contract_version="are-scanner-api-v1@v1.0",
                applicability_decisions=validated_applicability,
                applicability_policy_version=policy_version,
            )
            totals.append(float(result["bmi"]))
            pillar_runs.append(pillars(result))
        keys = sorted(pillar_runs[0])
        rows.append({
            "agent": fixture_dir.name,
            "totals": totals,
            "median_total": statistics.median(totals),
            "total_spread": max(totals) - min(totals),
            "pillar_medians": {k: statistics.median([p[k] for p in pillar_runs]) for k in keys},
            "pillar_spreads": {k: max(p[k] for p in pillar_runs) - min(p[k] for p in pillar_runs) for k in keys},
        })
    payload = {
        "issue": "BEAAA-33175",
        "n": 5,
        "seeds": [0, 1, 2, 3, 4],
        "contract": "contracts/are-scanner-api-v1.md@v1.3.0",
        "corpus_version": "fixture-pinned per golden expected_score.json",
        "rows": rows,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
