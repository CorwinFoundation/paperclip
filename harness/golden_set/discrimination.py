"""§5 Discrimination metric — calibration-set ARE spread (BEAAA-398).

Per `agents/cto-os.md` Supreme Law and `contracts/are-scanner-api-v1.md`
§hard-guarantee #4: the ARE distribution across the calibration set must span
the range, not collapse to ±10 of the mean. A collapse is a release-blocker
because pricing power dies when every agent looks like every other agent.

This module computes:

  - per-fixture current ARE (re-scored under current rubric/scanner)
  - spread = max(ARE) - min(ARE) across baselined fixtures
  - 10-pt distribution buckets across [0, 100]
  - mean, range coverage, collapse-to-mean check
  - by-slot view (known_good vs known_bad vs graded_middle) so the status
    consumer can see whether the slots themselves separate

Phase-1 target per BEAAA-398: spread ≥ 50 across 9 fully-baselined fixtures.
With only 5 baselined today (B1, M1, M2, G1, G2), the partial-coverage spread
is reported as-is and tagged with the BEAAA-419 partial-coverage caveat.
"""
from __future__ import annotations

import json
import statistics
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

DEFAULT_SPREAD_TARGET = 50
DEFAULT_COLLAPSE_HALF_WIDTH = 10
DEFAULT_BUCKET_WIDTH = 10


@dataclass
class FixtureScore:
    fixture_id: str
    slot: str
    baselined: bool
    current_are: int | None = None
    baseline_are: int | None = None
    pending_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "fixture_id": self.fixture_id,
            "slot": self.slot,
            "baselined": self.baselined,
        }
        if self.baselined:
            out["baseline_are"] = self.baseline_are
            out["current_are"] = self.current_are
        else:
            out["pending_reason"] = self.pending_reason
        return out


def _load_json(p: Path) -> dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def _bucketize(ares: list[int], width: int = DEFAULT_BUCKET_WIDTH) -> dict[str, int]:
    buckets: dict[str, int] = {}
    for lo in range(0, 100, width):
        hi = lo + width - 1 if lo + width < 100 else 100
        label = f"{lo:02d}-{hi:02d}"
        buckets[label] = 0
    for a in ares:
        bucket_idx = min(a // width, (100 // width) - 1)
        lo = bucket_idx * width
        hi = lo + width - 1 if lo + width < 100 else 100
        label = f"{lo:02d}-{hi:02d}"
        buckets[label] = buckets.get(label, 0) + 1
    return buckets


def compute_discrimination(
    *,
    manifest_path: Path = MANIFEST_PATH,
    spread_target: int = DEFAULT_SPREAD_TARGET,
    collapse_half_width: int = DEFAULT_COLLAPSE_HALF_WIDTH,
    today: str | None = None,
) -> dict[str, Any]:
    """Compute discrimination metric across baselined calibration fixtures."""
    manifest = _load_json(manifest_path)
    rubric = Rubric.load(RUBRIC_PATH)

    fixtures: list[FixtureScore] = []
    for fid, finfo in sorted(manifest["fixtures"].items()):
        slot = finfo.get("slot", "")
        if not finfo.get("baseline_persisted"):
            fixtures.append(FixtureScore(
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
        # BEAAA-647: thread applicability sidecar so Option-C baselines
        # (G1/G2/G3) re-score byte-identical to the locked aggregate.
        app_path = bdir / "applicability.json"
        applicability_decisions = None
        if app_path.exists():
            applicability_decisions = _load_json(app_path).get("applicability_decisions")
        current = score(
            rubric, eval_outputs,
            snapshot_hash=baseline["snapshot_hash"],
            forensic_bundle_uri=baseline["forensic_bundle_uri"],
            scored_at=datetime.fromisoformat(baseline["scored_at"]),
            scanner_version=None,
            corpus_version=baseline.get("corpus_version") or None,
            seed=baseline.get("seed"),
            model_versions=baseline.get("model_versions"),
            applicability_decisions=applicability_decisions,
            applicability_policy_version=baseline.get("applicability_policy_version"),
        )
        fixtures.append(FixtureScore(
            fixture_id=fid,
            slot=slot,
            baselined=True,
            current_are=int(current["bmi"]),
            baseline_are=int(baseline["bmi"]),
        ))

    baselined = [f for f in fixtures if f.baselined]
    pending = [f for f in fixtures if not f.baselined]
    current_ares = [f.current_are for f in baselined if f.current_are is not None]
    baseline_ares = [f.baseline_are for f in baselined if f.baseline_are is not None]

    if current_ares:
        spread_current = max(current_ares) - min(current_ares)
        mean_current = statistics.fmean(current_ares)
        within_band = [a for a in current_ares if abs(a - mean_current) <= collapse_half_width]
        collapse = len(within_band) == len(current_ares) and spread_current <= 2 * collapse_half_width
    else:
        spread_current = 0
        mean_current = 0.0
        collapse = False

    spread_baseline = (max(baseline_ares) - min(baseline_ares)) if baseline_ares else 0

    by_slot: dict[str, dict[str, Any]] = {}
    for slot in sorted({f.slot for f in baselined if f.slot}):
        slot_ares = [
            f.current_are for f in baselined
            if f.slot == slot and f.current_are is not None
        ]
        if slot_ares:
            by_slot[slot] = {
                "n": len(slot_ares),
                "min": min(slot_ares),
                "max": max(slot_ares),
                "mean": round(statistics.fmean(slot_ares), 2),
            }

    spread_pass = spread_current >= spread_target
    today = today or datetime.now(timezone.utc).date().isoformat()
    return {
        "issue": "BEAAA-398",
        "section": "§5 discrimination",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "for_status_date": today,
        "current_scanner_version": SCANNER_VERSION,
        "current_rubric_version": rubric.version,
        "current_contract_version": CONTRACT_VERSION,
        "manifest_path": str(manifest_path.relative_to(REPO)),
        "thresholds": {
            "spread_target": spread_target,
            "collapse_half_width": collapse_half_width,
            "bucket_width": DEFAULT_BUCKET_WIDTH,
        },
        "caveats": {
            "partial_coverage": (
                f"baselined={len(baselined)}/{len(fixtures)} "
                f"(pending={[f.fixture_id for f in pending]}). Spread target of "
                f"{spread_target} pts is set against fully-baselined 9-fixture set; "
                "partial-coverage spread reported as-is per BEAAA-419 anchor decision."
            ),
            "beaaa353_rubric_drift": (
                "Spread reported using current rubric (1.0.0) re-score of locked "
                "eval_outputs. Baselines under rubric 0.1.2 (B1/M1/M2) measure "
                "different ARE under current rubric — see calibration_delta artifact."
            ),
        },
        "fixtures": [f.as_dict() for f in fixtures],
        "spread": {
            "current": spread_current,
            "baseline": spread_baseline,
            "target": spread_target,
            "passes_target": spread_pass,
        },
        "distribution": {
            "n_baselined": len(baselined),
            "n_pending": len(pending),
            "mean_current": round(mean_current, 2) if current_ares else None,
            "stdev_current": (
                round(statistics.pstdev(current_ares), 2)
                if len(current_ares) >= 2 else None
            ),
            "buckets_current": _bucketize(current_ares),
            "buckets_baseline": _bucketize(baseline_ares),
            "collapse_to_mean": collapse,
            "by_slot": by_slot,
        },
        "summary": {
            "passed": spread_pass and not collapse,
            "summary_line": (
                f"baselined={len(baselined)}/{len(fixtures)} | "
                f"current spread={spread_current} (target {spread_target}) | "
                f"collapse={collapse} | "
                f"slots={list(by_slot.keys())}"
            ),
        },
    }


__all__ = ["compute_discrimination", "FixtureScore"]
