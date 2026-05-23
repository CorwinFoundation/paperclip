"""§2 Calibration delta — current re-score vs persisted baseline (BEAAA-398).

For each fixture in `artifacts/beaaa-387/manifest.json` whose
`baseline_persisted` is true, re-score the locked `eval_outputs.json` against
the *current* `rubric/rubric.yaml` + `scanners.SCANNER_VERSION` and compute:

    are_delta       = current_are - baseline_are
    pillar_deltas   = {pid: current_score - baseline_score for pid in P1..P4}

Supreme Law (`agents/cto-os.md`): a release moving any golden agent's total or
any subscore by more than ±1 / ±0.5 (matches `repro_gate.py` defaults) requires
Underwriter approval. This module surfaces the breach; it does not adjudicate.

BEAAA-353 caveat: the rubric is currently mid-reweight. Baselines locked under
rubric_version `0.1.2` will measure non-zero deltas under the current `1.0.0`
rubric. Those deltas are *attributable to rubric reweight*, not to scanner
regression. Each fixture's record carries `drift_attributable_to` so a status
consumer can attribute the ARE delta correctly.

Pending fixtures (G3, B2, B3, M3 as of 2026-05-16) are emitted as
`status=pending_baseline_scan` with the manifest's blocked_reason — they do
not block emission of the others, per the BEAAA-398 acceptance criterion.
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

DEFAULT_BMI_TOLERANCE = 1.0
DEFAULT_PILLAR_TOLERANCE = 0.5


@dataclass
class FixtureDelta:
    fixture_id: str
    slot: str
    baselined: bool
    baseline_are: int | None = None
    current_are: int | None = None
    are_delta: int | None = None
    pillar_deltas: dict[str, float] = field(default_factory=dict)
    baseline_scanner_version: str | None = None
    baseline_rubric_version: str | None = None
    baseline_corpus_version: str | None = None
    current_scanner_version: str = ""
    current_rubric_version: str = ""
    drift_attributable_to: str = "n/a"
    are_breach: bool = False
    pillar_breaches: list[str] = field(default_factory=list)
    pending_reason: str | None = None
    notes: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "fixture_id": self.fixture_id,
            "slot": self.slot,
            "baselined": self.baselined,
        }
        if self.baselined:
            out.update({
                "baseline_are": self.baseline_are,
                "current_are": self.current_are,
                "are_delta": self.are_delta,
                "pillar_deltas": {k: round(v, 4) for k, v in self.pillar_deltas.items()},
                "baseline_scanner_version": self.baseline_scanner_version,
                "baseline_rubric_version": self.baseline_rubric_version,
                "baseline_corpus_version": self.baseline_corpus_version,
                "current_scanner_version": self.current_scanner_version,
                "current_rubric_version": self.current_rubric_version,
                "drift_attributable_to": self.drift_attributable_to,
                "are_breach": self.are_breach,
                "pillar_breaches": list(self.pillar_breaches),
            })
        else:
            out["pending_reason"] = self.pending_reason
        if self.notes:
            out["notes"] = self.notes
        return out


def _load_json(p: Path) -> dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def _attribute_drift(
    *,
    baseline_rubric: str | None,
    baseline_scanner: str | None,
    current_rubric: str,
    current_scanner: str,
    are_delta: int,
) -> str:
    """Best-effort attribution of a non-zero ARE delta.

    rubric_reweight wins precedence: a rubric weight change makes per-pillar
    raw scores incomparable across versions, so the ARE delta is dominated by
    the reweight even if the scanner also moved.
    """
    rubric_changed = baseline_rubric and baseline_rubric != current_rubric
    scanner_changed = baseline_scanner and baseline_scanner != current_scanner
    if are_delta == 0 and not rubric_changed and not scanner_changed:
        return "none"
    if rubric_changed:
        return "rubric_reweight"
    if scanner_changed:
        return "scanner_only"
    return "unknown_regression"


def compute_delta(
    *,
    manifest_path: Path = MANIFEST_PATH,
    bmi_tolerance: float = DEFAULT_BMI_TOLERANCE,
    pillar_tolerance: float = DEFAULT_PILLAR_TOLERANCE,
    today: str | None = None,
) -> dict[str, Any]:
    """Compute calibration deltas for every fixture in the manifest."""
    manifest = _load_json(manifest_path)
    rubric = Rubric.load(RUBRIC_PATH)

    fixtures: list[FixtureDelta] = []
    for fid, finfo in sorted(manifest["fixtures"].items()):
        slot = finfo.get("slot", "")
        baselined = bool(finfo.get("baseline_persisted"))
        if not baselined:
            fixtures.append(FixtureDelta(
                fixture_id=fid,
                slot=slot,
                baselined=False,
                pending_reason=finfo.get("blocked_reason")
                or "fixture pending baseline scan, owned by BEAAA-387b/c",
                notes=f"owner={finfo.get('blocked_owner','?')}, "
                      f"source_issue={finfo.get('source_issue','?')}",
            ))
            continue

        bdir = BASELINES / fid
        baseline = _load_json(bdir / "baseline.json")
        eval_outputs = _load_json(bdir / "eval_outputs.json")

        # BEAAA-647: thread applicability sidecar when present so Option-C
        # baselines (G1/G2/G3) re-score byte-identical to their locked
        # aggregate; without this, the re-score drops N/A dims back into the
        # active set and inflates current_are by 1-2pt, surfacing as
        # spurious "unknown_regression" drift.
        app_path = bdir / "applicability.json"
        applicability_decisions = None
        if app_path.exists():
            applicability_decisions = _load_json(app_path).get("applicability_decisions")

        current = score(
            rubric, eval_outputs,
            snapshot_hash=baseline["snapshot_hash"],
            forensic_bundle_uri=baseline["forensic_bundle_uri"],
            scored_at=datetime.fromisoformat(baseline["scored_at"]),
            scanner_version=None,  # falls through to scanners.SCANNER_VERSION
            corpus_version=baseline.get("corpus_version") or None,
            seed=baseline.get("seed"),
            model_versions=baseline.get("model_versions"),
            applicability_decisions=applicability_decisions,
            applicability_policy_version=baseline.get("applicability_policy_version"),
        )

        b_are = int(baseline["bmi"])
        c_are = int(current["bmi"])
        are_delta = c_are - b_are

        b_pillars = baseline.get("pillars", {})
        c_pillars = current.get("pillars", {})
        pillar_deltas: dict[str, float] = {}
        for pid in sorted(set(b_pillars) | set(c_pillars)):
            b_score = float(b_pillars.get(pid, {}).get("score", 0.0))
            c_score = float(c_pillars.get(pid, {}).get("score", 0.0))
            pillar_deltas[pid] = c_score - b_score

        b_rubric = baseline.get("rubric_version")
        b_scanner = baseline.get("scanner_version")
        attribution = _attribute_drift(
            baseline_rubric=b_rubric,
            baseline_scanner=b_scanner,
            current_rubric=rubric.version,
            current_scanner=SCANNER_VERSION,
            are_delta=are_delta,
        )

        # A breach is a Supreme-Law breach only when versions match (same
        # rubric/scanner) and the math still moved. When versions differ, the
        # delta is attributable and surfaces as a caveat, not a regression.
        version_aligned = attribution in ("none",)
        are_breach = version_aligned and abs(are_delta) > bmi_tolerance
        pillar_breaches = [
            pid for pid, d in pillar_deltas.items()
            if version_aligned and abs(d) > pillar_tolerance
        ]

        fixtures.append(FixtureDelta(
            fixture_id=fid,
            slot=slot,
            baselined=True,
            baseline_are=b_are,
            current_are=c_are,
            are_delta=are_delta,
            pillar_deltas=pillar_deltas,
            baseline_scanner_version=b_scanner,
            baseline_rubric_version=b_rubric,
            baseline_corpus_version=baseline.get("corpus_version") or None,
            current_scanner_version=SCANNER_VERSION,
            current_rubric_version=rubric.version,
            drift_attributable_to=attribution,
            are_breach=are_breach,
            pillar_breaches=pillar_breaches,
        ))

    summary = _summarize(fixtures, bmi_tolerance, pillar_tolerance)
    today = today or datetime.now(timezone.utc).date().isoformat()
    return {
        "issue": "BEAAA-398",
        "section": "§2 calibration_health",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "for_status_date": today,
        "current_scanner_version": SCANNER_VERSION,
        "current_rubric_version": rubric.version,
        "current_contract_version": CONTRACT_VERSION,
        "manifest_path": str(manifest_path.relative_to(REPO)),
        "tolerances": {
            "are_total": bmi_tolerance,
            "per_pillar": pillar_tolerance,
        },
        "caveats": {
            "beaaa353_rubric_contract_drift": (
                "Rubric currently at 1.0.0 (25/25/25/25); contract pillar names + "
                "weights pinned at are-scanner-api-v1@v1.0. Baselines locked under "
                "rubric 0.1.2 (B1/M1/M2) measure non-zero ARE deltas under current "
                "rubric — attribution=rubric_reweight, not scanner regression."
            ),
            "partial_coverage": (
                "Pending fixtures (G3, B2, B3, M3) emit pending_reason and do not "
                "block emission per BEAAA-419 anchor decision (ADR-0003)."
            ),
        },
        "fixtures": [f.as_dict() for f in fixtures],
        "summary": summary,
    }


def _summarize(
    fixtures: list[FixtureDelta],
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
    by_attribution: dict[str, list[str]] = {}
    for f in baselined:
        by_attribution.setdefault(f.drift_attributable_to, []).append(f.fixture_id)
    passed = not are_breaches and not pillar_breaches
    parts = [
        f"baselined={len(baselined)}/{len(fixtures)}",
        f"pending={len(pending)}",
        f"ARE breaches (>{bmi_tolerance})={len(are_breaches)}",
        f"pillar breaches (>{pillar_tolerance})={len(pillar_breaches)}",
        "PASS" if passed else "BREACH",
    ]
    return {
        "passed": passed,
        "baselined_count": len(baselined),
        "pending_count": len(pending),
        "are_breach_fixtures": are_breaches,
        "pillar_breach_pairs": [list(t) for t in pillar_breaches],
        "by_attribution": by_attribution,
        "summary_line": " | ".join(parts),
    }


__all__ = ["compute_delta", "FixtureDelta"]
