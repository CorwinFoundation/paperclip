"""§3 Reproducibility band (L1 deterministic N=5) — BEAAA-398.

Layered per `agents/cto-os.md` Supreme Law: every release runs the calibration
set N=5 times and blocks if total or any subscore varies by more than ±1.

This module owns the **L1 (deterministic, no-API)** layer: re-run
`rubric.scoring.score()` against each fixture's locked `eval_outputs.json`
exactly N times and assert the math is byte-stable. Any non-zero drift here is
a code regression, full stop — there is no stochasticity to amortize since the
scoring engine is pure.

The L2 layer (live probe-runner stochasticity) is owned separately. CTO
recommendation per BEAAA-398: weekly cadence on cheap fixtures, monthly on
the full slate; revisit if BEAAA-152 budget allows tighter cadence. L2 lives
in `harness/golden_set/repro_gate.py` and is run out-of-band of CI.

Pending fixtures emit `status=pending_baseline_scan` and do not block
emission of the others.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rubric.scoring import Rubric, score
from scanners import CONTRACT_VERSION, SCANNER_VERSION

REPO = Path(__file__).resolve().parents[2]
BASELINES = REPO / "artifacts" / "beaaa-387"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
MANIFEST_PATH = BASELINES / "manifest.json"

DEFAULT_N = 5
DEFAULT_BMI_TOLERANCE = 1.0
DEFAULT_PILLAR_TOLERANCE = 0.5


@dataclass
class FixtureBand:
    fixture_id: str
    slot: str
    baselined: bool
    n_replicates: int = 0
    are_min: int | None = None
    are_max: int | None = None
    are_drift: int | None = None
    pillar_drifts: dict[str, float] = field(default_factory=dict)
    are_breach: bool = False
    pillar_breaches: list[str] = field(default_factory=list)
    pending_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "fixture_id": self.fixture_id,
            "slot": self.slot,
            "baselined": self.baselined,
        }
        if self.baselined:
            out.update({
                "n_replicates": self.n_replicates,
                "are_min": self.are_min,
                "are_max": self.are_max,
                "are_drift": self.are_drift,
                "pillar_drifts": {k: round(v, 4) for k, v in self.pillar_drifts.items()},
                "are_breach": self.are_breach,
                "pillar_breaches": list(self.pillar_breaches),
            })
        else:
            out["pending_reason"] = self.pending_reason
        return out


def _load_json(p: Path) -> dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def run_l1_n5(
    *,
    manifest_path: Path = MANIFEST_PATH,
    n: int = DEFAULT_N,
    bmi_tolerance: float = DEFAULT_BMI_TOLERANCE,
    pillar_tolerance: float = DEFAULT_PILLAR_TOLERANCE,
    today: str | None = None,
) -> dict[str, Any]:
    """Run the L1 deterministic N-replicate gate against every baselined fixture.

    L1 should always emit drift = 0 across N runs (pure math). Non-zero drift
    here means the scoring engine has lost determinism — release-blocker.
    """
    if n < 2:
        raise ValueError("L1 N=5 gate requires n >= 2")

    manifest = _load_json(manifest_path)
    rubric = Rubric.load(RUBRIC_PATH)

    fixtures: list[FixtureBand] = []
    for fid, finfo in sorted(manifest["fixtures"].items()):
        slot = finfo.get("slot", "")
        if not finfo.get("baseline_persisted"):
            fixtures.append(FixtureBand(
                fixture_id=fid,
                slot=slot,
                baselined=False,
                pending_reason=finfo.get("blocked_reason")
                or "fixture pending baseline scan, owned by BEAAA-387b/c",
            ))
            continue

        bdir = BASELINES / fid
        baseline = _load_json(bdir / "baseline.json")
        eval_outputs = _load_json(bdir / "eval_outputs.json")

        replicates: list[dict[str, Any]] = []
        for _ in range(n):
            replicates.append(score(
                rubric, eval_outputs,
                snapshot_hash=baseline["snapshot_hash"],
                forensic_bundle_uri=baseline["forensic_bundle_uri"],
                scored_at=datetime.fromisoformat(baseline["scored_at"]),
                scanner_version=None,
                corpus_version=baseline.get("corpus_version") or None,
                seed=baseline.get("seed"),
                model_versions=baseline.get("model_versions"),
            ))

        ares = [int(r["bmi"]) for r in replicates]
        are_min, are_max = min(ares), max(ares)
        are_drift = are_max - are_min
        are_breach = are_drift > bmi_tolerance

        pillar_ids = list(replicates[0].get("pillars", {}).keys())
        pillar_drifts: dict[str, float] = {}
        pillar_breaches: list[str] = []
        for pid in pillar_ids:
            scores = [float(r["pillars"][pid]["score"]) for r in replicates]
            drift = max(scores) - min(scores)
            pillar_drifts[pid] = drift
            if drift > pillar_tolerance:
                pillar_breaches.append(pid)

        fixtures.append(FixtureBand(
            fixture_id=fid,
            slot=slot,
            baselined=True,
            n_replicates=n,
            are_min=are_min,
            are_max=are_max,
            are_drift=are_drift,
            pillar_drifts=pillar_drifts,
            are_breach=are_breach,
            pillar_breaches=pillar_breaches,
        ))

    summary = _summarize(fixtures, n, bmi_tolerance, pillar_tolerance)
    today = today or datetime.now(timezone.utc).date().isoformat()
    return {
        "issue": "BEAAA-398",
        "section": "§3 reproducibility_band_L1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "for_status_date": today,
        "n": n,
        "current_scanner_version": SCANNER_VERSION,
        "current_rubric_version": rubric.version,
        "current_contract_version": CONTRACT_VERSION,
        "manifest_path": str(manifest_path.relative_to(REPO)),
        "tolerances": {
            "are_total": bmi_tolerance,
            "per_pillar": pillar_tolerance,
        },
        "layer_notes": {
            "L1": (
                "deterministic re-run of score() against locked eval_outputs.json; "
                "drift=0 by construction. Non-zero drift = release-blocker code regression."
            ),
            "L2_owner": (
                "harness/golden_set/repro_gate.py — live probe-runner replicates, "
                "weekly on cheap fixtures + monthly on full slate (BEAAA-398 §3 cadence)."
            ),
        },
        "fixtures": [f.as_dict() for f in fixtures],
        "summary": summary,
    }


def _summarize(
    fixtures: list[FixtureBand],
    n: int,
    bmi_tolerance: float,
    pillar_tolerance: float,
) -> dict[str, Any]:
    baselined = [f for f in fixtures if f.baselined]
    pending = [f for f in fixtures if not f.baselined]
    are_breaches = [f.fixture_id for f in baselined if f.are_breach]
    pillar_breaches = [
        (f.fixture_id, p)
        for f in baselined for p in f.pillar_breaches
    ]
    passed = not are_breaches and not pillar_breaches
    max_are_drift = max((f.are_drift or 0 for f in baselined), default=0)
    parts = [
        f"N={n}",
        f"baselined={len(baselined)}/{len(fixtures)}",
        f"pending={len(pending)}",
        f"max ARE drift={max_are_drift}",
        f"breaches={len(are_breaches) + len(pillar_breaches)}",
        "PASS" if passed else "BREACH",
    ]
    return {
        "passed": passed,
        "baselined_count": len(baselined),
        "pending_count": len(pending),
        "max_are_drift": max_are_drift,
        "are_breach_fixtures": are_breaches,
        "pillar_breach_pairs": [list(t) for t in pillar_breaches],
        "summary_line": " | ".join(parts),
    }


__all__ = ["run_l1_n5", "FixtureBand"]
