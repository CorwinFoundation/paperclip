"""P2.1 demographic-parity-gap R2.BIAS Path B adapter — BEAAA-5209 / BEAAA-5238.

Mirrors the envelope shape of harness/bench/p1_3_consistency_golden.run().
Deterministic against the probe roster in
rubric/probe_mappings/p2_1_demographic_parity_gap.yaml and a per-fixture
agent-response label map. raw_score_formula = piecewise(max_axis_abs_gap).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.bench.r2_bias_common import (
    REPO_ROOT,
    envelope_for_dim,
    load_agent_fixture,
    load_probes,
    score_gap_family,
)


FAMILY_ID = "p2_1_demographic_parity_gap"
DIM_ID = "P2.1"
YAML_PATH = REPO_ROOT / "rubric" / "probe_mappings" / f"{FAMILY_ID}.yaml"


def run(
    *,
    yaml_path: str | Path = YAML_PATH,
    fixture_path: str | Path,
    scanner_version: str = "0.4.1",
) -> dict[str, Any]:
    probes = load_probes(yaml_path)
    responses = load_agent_fixture(fixture_path)
    scoring = score_gap_family(
        probes=probes, responses=responses, family_id=FAMILY_ID,
    )
    return envelope_for_dim(
        dim_id=DIM_ID,
        family_id=FAMILY_ID,
        scoring_result=scoring,
        scanner_version=scanner_version,
    )
