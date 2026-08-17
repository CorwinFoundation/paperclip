"""Version-aligned golden checkpoint enforcement (ADR-0067 v1.1).

Historical files under ``artifacts/beaaa-387`` remain lineage evidence.  This
module selects a separately approved checkpoint and fails closed when the
candidate has no matching scoring-semantics generation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CHECKPOINT = REPO / "artifacts" / "beaaa-23802" / "aligned-checkpoint-v1.json"
REQUIRED_PINS = (
    "scanner_version", "rubric_version", "corpus_version", "seed",
    "model_versions", "applicability_policy_version",
)
PILLARS = ("GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def compare_aligned_checkpoint(
    candidate: dict[str, Any],
    *,
    checkpoint_path: Path = DEFAULT_CHECKPOINT,
    total_tolerance: float = 1.0,
    pillar_tolerance: float = 0.5,
) -> dict[str, Any]:
    """Compare a candidate fixture set with an approved aligned checkpoint."""
    if not checkpoint_path.exists():
        return _blocked("BLOCKED_NO_ALIGNED_BASELINE")
    checkpoint = _load(checkpoint_path)
    if not checkpoint.get("approved"):
        return _blocked("BLOCKED_NO_ALIGNED_BASELINE")
    if any(candidate.get(k) != checkpoint.get(k) for k in ("scanner_version", "rubric_version", "scoring_semantics")):
        return _blocked("BLOCKED_NO_ALIGNED_BASELINE")

    expected = checkpoint.get("fixtures", {})
    observed = candidate.get("fixtures", {})
    failures: list[dict[str, Any]] = []
    for fixture_id, anchor in expected.items():
        row = observed.get(fixture_id)
        if not isinstance(row, dict):
            failures.append({"fixture_id": fixture_id, "code": "MISSING_FIXTURE"})
            continue
        if row.get("status") == "masked":
            failures.append({"fixture_id": fixture_id, "code": "MASKED_FIXTURE"})
            continue
        missing = [pin for pin in REQUIRED_PINS if pin not in row]
        if missing:
            failures.append({"fixture_id": fixture_id, "code": "MISSING_REQUIRED_PINS", "pins": missing})
            continue
        if any(row[pin] != anchor[pin] for pin in REQUIRED_PINS):
            failures.append({"fixture_id": fixture_id, "code": "PIN_MISMATCH"})
            continue
        total_delta = float(row["total"]) - float(anchor["total"])
        pillar_deltas = {
            pillar: float(row["pillars"][pillar]) - float(anchor["pillars"][pillar])
            for pillar in PILLARS
        }
        if abs(total_delta) > total_tolerance or any(abs(value) > pillar_tolerance for value in pillar_deltas.values()):
            failures.append({
                "fixture_id": fixture_id,
                "code": "ALIGNED_DRIFT_BREACH",
                "total_delta": total_delta,
                "pillar_deltas": pillar_deltas,
            })

    status = "PASS" if not failures and len(observed) == len(expected) else "BREACH"
    return {
        "status": status,
        "passed": status == "PASS",
        "checkpoint_id": checkpoint["checkpoint_id"],
        "failures": failures,
        "caveats": checkpoint["caveats"],
    }


def _blocked(status: str) -> dict[str, Any]:
    return {"status": status, "passed": False, "failures": []}


__all__ = ["compare_aligned_checkpoint", "DEFAULT_CHECKPOINT", "PILLARS", "REQUIRED_PINS"]
