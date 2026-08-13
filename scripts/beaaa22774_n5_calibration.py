"""Run BEAAA-22774 N=5 seeds 0..4 on the frozen golden roster."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.bundle import _signing_key  # noqa: E402
from rubric.scoring import Rubric, ValidatedApplicability, score  # noqa: E402


GOLDENS = ROOT / "tests" / "fixtures" / "golden_agents"
OUT = ROOT / "artifacts" / "beaaa-22774" / "n5-calibration.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def pillars(payload: dict) -> dict[str, float]:
    return {key: float(value["score"]) for key, value in payload["pillars"].items()}


def main() -> int:
    os.environ.setdefault("ARE_ALLOW_DEV_SIGNING_KEY", "true")
    rubric = Rubric.load(ROOT / "rubric" / "rubric.yaml")
    rows = []
    passed = True
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
        runs = []
        for seed in range(5):
            result = score(
                rubric=rubric,
                eval_outputs=load(fixture_dir / "eval_outputs.json"),
                forensic_bundle_uri=f"https://golden.local/{fixture_dir.name}",
                scored_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
                snapshot_hash=f"BEAAA-22774-{fixture_dir.name}-{seed}",
                scanner_version="0.5.0",
                corpus_version=expected.get("corpus_version", "0.1.0"),
                seed=seed,
                model_versions=expected.get("model_versions", {}),
                contract_version="are-scanner-api-v1@v1.0",
                applicability_decisions=validated_applicability,
                applicability_policy_version=policy_version,
            )
            runs.append({"seed": seed, "total": float(result["bmi"]), "pillars": pillars(result)})
        expected_total = float(expected["bmi"])
        expected_pillars = pillars(expected)
        total_delta = max(abs(run["total"] - expected_total) for run in runs)
        pillar_delta = max(
            abs(run["pillars"][pillar] - expected_pillars[pillar])
            for run in runs
            for pillar in expected_pillars
        )
        row_passed = total_delta == 0 and pillar_delta == 0
        passed = passed and row_passed
        rows.append({
            "agent": fixture_dir.name,
            "runs": runs,
            "max_total_delta": total_delta,
            "max_pillar_delta": pillar_delta,
            "passed": row_passed,
        })
    receipt = {
        "issue": "BEAAA-22774",
        "scanner_version": "0.5.0",
        "corpus_version": "0.1.0 (ticket pin; fixture provenance retained per run)",
        "probe-set-version": "real_adapter_asi_v0@0.1.0",
        "rubric_point_id": ["P3.2", "P3.3"],
        "contract_version": "are-scanner-api-v1@v1.0",
        "seeds": [0, 1, 2, 3, 4],
        "rows": rows,
        "max_total_delta": max((row["max_total_delta"] for row in rows), default=0),
        "max_pillar_delta": max((row["max_pillar_delta"] for row in rows), default=0),
        "passed": passed,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}; passed={passed}; agents={len(rows)}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
