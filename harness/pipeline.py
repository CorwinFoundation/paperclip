"""Hybrid pipeline — combine real benchmark measurements with directional
priors to produce a complete ARE raw_scores dict.

POC scope: only P1.1 has a real benchmark adapter. The other 20 dims fall
through to the prior. The output is a `(raw_scores, provenance)` tuple
where `provenance[dim_id]` is either "measured:<benchmark>" or "prior".
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def merge_measurements_with_priors(
    measurements: list[dict[str, Any]],
    priors: dict[str, float],
) -> tuple[dict[str, float], dict[str, str]]:
    raw_scores: dict[str, float] = dict(priors)
    provenance: dict[str, str] = {dim: "prior" for dim in priors}
    for m in measurements:
        dim = m["dim_id"]
        raw_scores[dim] = float(m["raw"])
        provenance[dim] = f"measured:{m['benchmark']}"
    return raw_scores, provenance


def load_priors(prior_path: str | Path) -> tuple[str, dict[str, float]]:
    data = json.loads(Path(prior_path).read_text())
    return data["display_name"], {k: float(v) for k, v in data["raw_scores"].items()}
