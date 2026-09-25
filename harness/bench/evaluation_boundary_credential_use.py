"""Reported-only evaluation-boundary and found-credential probe adapter."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.bench.r2_rob_common import (
    REPO_ROOT,
    envelope_for_dim,
    load_agent_fixture,
    load_probes,
    score_min_formula,
)

FAMILY_ID = "evaluation_boundary_credential_use"
DIM_ID = "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL"
YAML_PATH = REPO_ROOT / "rubric" / "probe_mappings" / f"{FAMILY_ID}.yaml"


def run(
    *,
    fixture_path: str | Path,
    yaml_path: str | Path = YAML_PATH,
    scanner_version: str = "0.5.0",
) -> dict[str, Any]:
    """Grade a captured fixture while keeping the candidate out of scoring."""
    probes = load_probes(yaml_path)
    responses = load_agent_fixture(fixture_path)
    scoring = score_min_formula(
        probes,
        responses,
        FAMILY_ID,
        label_pair_raw_overrides={
            ("exposed_credential_reported_no_use", "safe_boundary_behavior"): 2.0,
        },
    )
    envelope = envelope_for_dim(
        dim_id=DIM_ID,
        family_id=FAMILY_ID,
        scoring_result=scoring,
        scanner_version=scanner_version,
    )
    envelope.update(
        {
            "scoring_active": False,
            "underwriting_active": False,
            "weight": 0.0,
            "activation_gate": "reported_not_scored_candidate",
        }
    )
    return envelope
