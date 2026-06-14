"""ROBUSTNESS.PII.SCAN_TIME_DETECTION R2.ROB Path B adapter.

Routes through ``score_pii_threshold_table`` (NOT ``score_min_formula``)
per the BEAAA-5208 family spec — raw is the threshold-table lookup over
the (high_sev_fn_count, lower_sev_recall) tuple.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.bench.r2_rob_common import (
    REPO_ROOT,
    envelope_for_dim,
    load_agent_fixture,
    load_probes,
    score_pii_threshold_table,
)


FAMILY_ID = "pii_scan_time_detection"
DIM_ID = "ROBUSTNESS.PII.SCAN_TIME_DETECTION"
YAML_PATH = REPO_ROOT / "rubric" / "probe_mappings" / f"{FAMILY_ID}.yaml"


def run(
    *,
    yaml_path: str | Path = YAML_PATH,
    fixture_path: str | Path,
    scanner_version: str = "0.4.1",
) -> dict[str, Any]:
    probes = load_probes(yaml_path)
    responses = load_agent_fixture(fixture_path)
    scoring = score_pii_threshold_table(probes, responses, family_id=FAMILY_ID)
    return envelope_for_dim(
        dim_id=DIM_ID,
        family_id=FAMILY_ID,
        scoring_result=scoring,
        scanner_version=scanner_version,
    )
