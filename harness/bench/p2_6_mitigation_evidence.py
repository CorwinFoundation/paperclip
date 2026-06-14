"""P2.6 mitigation-evidence R2.BIAS Path B adapter — BEAAA-5209 / BEAAA-5238.

Deterministic against the probe roster in
rubric/probe_mappings/p2_6_mitigation_evidence.yaml and a per-fixture
agent-response label map.
raw_score_formula = min(coverage_grade, quality_grade).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.bench.r2_bias_common import (
    REPO_ROOT,
    envelope_for_dim,
    load_agent_fixture,
    load_probes,
    score_evidence_family,
)


FAMILY_ID = "p2_6_mitigation_evidence"
DIM_ID = "P2.6"
YAML_PATH = REPO_ROOT / "rubric" / "probe_mappings" / f"{FAMILY_ID}.yaml"


def run(
    *,
    yaml_path: str | Path = YAML_PATH,
    fixture_path: str | Path,
    scanner_version: str = "0.4.1",
) -> dict[str, Any]:
    probes = load_probes(yaml_path)
    responses = load_agent_fixture(fixture_path)
    scoring = score_evidence_family(
        probes=probes, responses=responses, family_id=FAMILY_ID,
    )
    return envelope_for_dim(
        dim_id=DIM_ID,
        family_id=FAMILY_ID,
        scoring_result=scoring,
        scanner_version=scanner_version,
    )
