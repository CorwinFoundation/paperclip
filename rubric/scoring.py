"""BMI Scanner v0.1 deterministic scoring engine.

Loads rubric YAML, accepts per-dim raw scores (0-4), produces BMI(0-100)
plus per-pillar/per-dim breakdown matching api/openapi.yaml ScoreResponse.

Red-line invariants enforced here (BEAAA-29 non-negotiable):
- snapshot_hash and forensic_bundle_uri are required and pass through unchanged
- pillar weights must sum to 100 on rubric load
- every active dim must have a score; missing dims raise (no silent zero)
- raw scores must be in [0, 4]; out-of-range raises
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .bands import assign_band, band_to_dict


class RubricError(ValueError):
    pass


class ScoringError(ValueError):
    pass


@dataclass(frozen=True)
class Rubric:
    version: str
    locked_through: str
    pillars: dict[str, dict[str, Any]]

    @classmethod
    def load(cls, path: str | Path) -> "Rubric":
        data = yaml.safe_load(Path(path).read_text())
        pillars: dict[str, dict[str, Any]] = {}
        total_weight = 0
        seen_dims: set[str] = set()
        for pid, pdata in data["pillars"].items():
            weight = int(pdata["weight"])
            total_weight += weight
            dim_ids = [d["id"] for d in pdata["dimensions"]]
            for did in dim_ids:
                if did in seen_dims:
                    raise RubricError(f"duplicate dim id across pillars: {did}")
                seen_dims.add(did)
            pillars[pid] = {
                "name": pdata["name"],
                "weight": weight,
                "dim_ids": dim_ids,
            }
        if total_weight != 100:
            raise RubricError(f"pillar weights sum to {total_weight}, expected 100")
        return cls(
            version=data["version"],
            locked_through=data["locked_through"],
            pillars=pillars,
        )

    @property
    def active_dim_ids(self) -> list[str]:
        return [d for p in self.pillars.values() for d in p["dim_ids"]]


def score(
    rubric: Rubric,
    eval_outputs: dict[str, dict[str, Any]],
    *,
    snapshot_hash: str,
    forensic_bundle_uri: str,
    scored_at: datetime | None = None,
) -> dict[str, Any]:
    """Score eval outputs against rubric.

    eval_outputs: { dim_id: { "raw": float in [0,4], "evidence_uri": str } }
    Returns ScoreResponse-shaped dict (see api/openapi.yaml).
    """
    if not snapshot_hash:
        raise ScoringError("snapshot_hash required (red line)")
    if not forensic_bundle_uri:
        raise ScoringError("forensic_bundle_uri required (red line)")

    missing = [d for d in rubric.active_dim_ids if d not in eval_outputs]
    if missing:
        raise ScoringError(f"missing dim scores: {missing}")

    pillars_out: dict[str, dict[str, Any]] = {}
    bmi_total = 0.0
    for pid, pdata in rubric.pillars.items():
        dim_block: dict[str, dict[str, Any]] = {}
        raws: list[float] = []
        for did in pdata["dim_ids"]:
            entry = eval_outputs[did]
            raw = float(entry["raw"])
            if not 0 <= raw <= 4:
                raise ScoringError(f"{did} raw={raw} out of [0,4]")
            raws.append(raw)
            dim_block[did] = {
                "raw": raw,
                "evidence_uri": entry.get("evidence_uri", ""),
            }
        normalized = (sum(raws) / len(raws)) / 4.0
        pillar_score = normalized * pdata["weight"]
        bmi_total += pillar_score
        pillars_out[pid] = {
            "score": round(pillar_score, 4),
            "weight": pdata["weight"],
            "dimensions": dim_block,
        }

    bmi_int = int(round(bmi_total))
    return {
        "bmi": bmi_int,
        "band": band_to_dict(assign_band(bmi_int)),
        "pillars": pillars_out,
        "forensic_bundle_uri": forensic_bundle_uri,
        "snapshot_hash": snapshot_hash,
        "scored_at": (scored_at or datetime.now(timezone.utc)).isoformat(),
        "rubric_version": rubric.version,
    }
