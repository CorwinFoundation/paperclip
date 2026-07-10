"""ROBUSTNESS.TOOL_MISUSE scope-escape R2.ROB Path B adapter."""
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


FAMILY_ID = "tool_misuse_scope_escape"
DIM_ID = "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL"
YAML_PATH = REPO_ROOT / "rubric" / "probe_mappings" / f"{FAMILY_ID}.yaml"


def run(
    *,
    yaml_path: str | Path = YAML_PATH,
    fixture_path: str | Path,
    scanner_version: str = "0.4.1",
) -> dict[str, Any]:
    probes = load_probes(yaml_path)
    responses = load_agent_fixture(fixture_path)
    scoring = score_min_formula(probes, responses, FAMILY_ID)
    return envelope_for_dim(
        dim_id=DIM_ID,
        family_id=FAMILY_ID,
        scoring_result=scoring,
        scanner_version=scanner_version,
    )
