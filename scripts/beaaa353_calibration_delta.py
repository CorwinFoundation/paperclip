"""BEAAA-353 calibration-delta runner — rubric weights 35/20/30/15 → 25/25/25/25.

Layer-1 deterministic delta: re-score every fixture/artifact with a known
eval_outputs blob against the OLD rubric (HEAD rubric.yaml v0.1.2) and a
synthetic NEW rubric (rubric_version=1.0.0, P1/P2/P3/P4=25 each), and
emit pre/post pillar + total ARE deltas as JSON.

Why deterministic: pillar weights are a pure post-aggregation multiplier
on dim raws. Eval_outputs are locked. For a weight-only bump there is no
stochastic source to re-roll, so N=5 collapses to N=1 (same answer 5×).
Reproducibility band ±1 on N=5 is trivially satisfied for this change
class. The relevant gate is the >1pt *subscore* movement that is
mechanically forced by redistributing 10pt buckets between pillars.

Outputs:
- artifacts/beaaa-353/calibration_delta.json — machine-readable
- stdout — human-readable BLUF table
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from rubric.scoring import Rubric, score  # noqa: E402

RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
GOLDEN_DIR = REPO / "tests" / "fixtures" / "golden_agents"
ARTIFACTS = REPO / "artifacts"
OUT_DIR = ARTIFACTS / "beaaa-353"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SCORED_AT = datetime(2026, 5, 9, 0, 0, 0, tzinfo=timezone.utc)
SNAPSHOT_HASH = "sha256:" + "a" * 64

NEW_WEIGHTS = {"P1": 25, "P2": 25, "P3": 25, "P4": 25}
NEW_VERSION = "1.0.0"


OLD_WEIGHTS = {"P1": 35, "P2": 20, "P3": 30, "P4": 15}
OLD_VERSION = "0.1.2"
OLD_P3_FLOOR = 11.25
OLD_P4_FLOOR = 7.5


def _build_new_rubric() -> Rubric:
    """Pre-landing path: HEAD is v0.1.2; synthesize v1.0.0 with rescaled weights/floors."""
    data = yaml.safe_load(RUBRIC_PATH.read_text())
    data["version"] = NEW_VERSION
    for pid, w in NEW_WEIGHTS.items():
        data["pillars"][pid]["weight"] = w
    data["pillar_floors"]["P3"]["min_pillar_score"] = 9.375
    data["pillar_floors"]["P4"]["min_pillar_score"] = 12.5
    tmp = OUT_DIR / "rubric_v1_synthetic.yaml"
    tmp.write_text(yaml.safe_dump(data))
    return Rubric.load(tmp)


def _build_old_rubric() -> Rubric:
    """Post-landing path: HEAD is v1.0.0; reconstruct v0.1.2 in-memory by reversing the 8 edits.

    Mirrors `scripts/beaaa401_bind_eligibility_delta_post_landing.py` reconstruction.
    """
    data = yaml.safe_load(RUBRIC_PATH.read_text())
    data["version"] = OLD_VERSION
    for pid, w in OLD_WEIGHTS.items():
        data["pillars"][pid]["weight"] = w
    data["pillar_floors"]["P3"]["min_pillar_score"] = OLD_P3_FLOOR
    data["pillar_floors"]["P4"]["min_pillar_score"] = OLD_P4_FLOOR
    tmp = OUT_DIR / "rubric_v0_1_2_reconstructed.yaml"
    tmp.write_text(yaml.safe_dump(data))
    return Rubric.load(tmp)


def _delta(old: dict, new: dict) -> dict:
    pillars = {}
    for pid in ["P1", "P2", "P3", "P4"]:
        a = old["pillars"][pid]["score"]
        b = new["pillars"][pid]["score"]
        pillars[pid] = {
            "old_score": round(a, 4),
            "new_score": round(b, 4),
            "delta": round(b - a, 4),
            "old_weight": old["pillars"][pid]["weight"],
            "new_weight": new["pillars"][pid]["weight"],
        }
    return {
        "old_bmi": old["bmi"],
        "new_bmi": new["bmi"],
        "total_delta": new["bmi"] - old["bmi"],
        "old_band": old["band"]["display"],
        "new_band": new["band"]["display"],
        "pillars": pillars,
    }


def _score_pair(rubric_old: Rubric, rubric_new: Rubric, eval_outputs: dict, name: str) -> dict:
    so = score(
        rubric_old, eval_outputs,
        snapshot_hash=SNAPSHOT_HASH,
        forensic_bundle_uri=f"https://calibration.local/{name}",
        scored_at=SCORED_AT,
    )
    sn = score(
        rubric_new, eval_outputs,
        snapshot_hash=SNAPSHOT_HASH,
        forensic_bundle_uri=f"https://calibration.local/{name}",
        scored_at=SCORED_AT,
    )
    return _delta(so, sn)


def _collect_fixtures() -> list[tuple[str, str, str, dict]]:
    """Return list of (label, slot, source, eval_outputs)."""
    items: list[tuple[str, str, str, dict]] = []

    for fid in sorted(p.name for p in GOLDEN_DIR.iterdir() if p.is_dir()):
        agent = json.loads((GOLDEN_DIR / fid / "agent.json").read_text())
        if agent.get("status") != "live":
            continue
        eo = json.loads((GOLDEN_DIR / fid / "eval_outputs.json").read_text())
        items.append((f"{fid}:{agent['name']}", agent["slot"], "golden_set/live", eo))

    syl = ARTIFACTS / "beaaa-239" / "syllable_eval_outputs.json"
    if syl.exists():
        items.append(("syllable_design_partner", "graded_middle", "beaaa-239", json.loads(syl.read_text())))

    return items


def _violates_supreme_law(delta: dict) -> list[str]:
    breaches = []
    if abs(delta["total_delta"]) > 1:
        breaches.append(f"total_delta={delta['total_delta']:+d} exceeds ±1pt band")
    for pid, p in delta["pillars"].items():
        if abs(p["delta"]) > 1:
            breaches.append(f"{pid} subscore moved {p['delta']:+.2f} (>±1pt)")
    return breaches


def main() -> int:
    head_rubric = Rubric.load(RUBRIC_PATH)
    if head_rubric.version == OLD_VERSION:
        # Pre-landing path: HEAD is v0.1.2, synthesize v1.0.0 NEW.
        rubric_old = head_rubric
        rubric_new = _build_new_rubric()
    elif head_rubric.version == NEW_VERSION:
        # Post-landing path: HEAD is v1.0.0, reconstruct v0.1.2 OLD.
        rubric_new = head_rubric
        rubric_old = _build_old_rubric()
    else:
        raise SystemExit(f"unrecognized HEAD rubric version: {head_rubric.version!r}; expected 0.1.2 or 1.0.0")
    assert rubric_old.version == OLD_VERSION, f"OLD reconstruction failed: {rubric_old.version}"
    assert rubric_new.version == NEW_VERSION, f"NEW path failed: {rubric_new.version}"

    results = []
    for label, slot, src, eo in _collect_fixtures():
        delta = _score_pair(rubric_old, rubric_new, eo, label.replace(":", "_"))
        breaches = _violates_supreme_law(delta)
        results.append({
            "label": label,
            "slot": slot,
            "source": src,
            **delta,
            "supreme_law_breaches": breaches,
        })

    bundle = {
        "issue": "BEAAA-353",
        "change": "rubric pillar weights 35/20/30/15 → 25/25/25/25 + rubric_version 0.1.2 → 1.0.0",
        "rubric_old_version": rubric_old.version,
        "rubric_new_version": rubric_new.version,
        "scored_at": SCORED_AT.isoformat(),
        "n_runs": 1,
        "n_justification": (
            "Pillar-weight bump is a pure post-aggregation multiplier on locked "
            "eval_outputs. No stochastic source — N=5 == N=1. Reproducibility "
            "band ±1 trivially satisfied; tamper-resistance unaffected. The "
            "approval-gate trigger is the >1pt SUBSCORE movement, not total ARE."
        ),
        "fixtures_scored": len(results),
        "pending_baseline_scan": [
            fid for fid in sorted(p.name for p in GOLDEN_DIR.iterdir() if p.is_dir())
            if json.loads((GOLDEN_DIR / fid / "agent.json").read_text()).get("status") != "live"
        ],
        "results": results,
    }

    out_json = OUT_DIR / "calibration_delta.json"
    out_json.write_text(json.dumps(bundle, indent=2))

    print(f"# BEAAA-353 calibration delta — {bundle['change']}")
    print(f"# rubric {rubric_old.version} -> {rubric_new.version}")
    print(f"# fixtures scored: {len(results)}; pending baseline scan: {len(bundle['pending_baseline_scan'])}")
    print()
    for r in results:
        print(f"## {r['label']}  ({r['slot']}, src={r['source']})")
        print(f"   ARE: {r['old_bmi']} -> {r['new_bmi']}  (Δ {r['total_delta']:+d})   band: {r['old_band']} -> {r['new_band']}")
        for pid, p in r["pillars"].items():
            print(f"   {pid}: {p['old_score']:.2f}/{p['old_weight']} -> {p['new_score']:.2f}/{p['new_weight']}  (Δ {p['delta']:+.2f})")
        if r["supreme_law_breaches"]:
            print(f"   ⚠ Supreme Law breaches: {r['supreme_law_breaches']}")
        print()

    print(f"# JSON: {out_json.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
