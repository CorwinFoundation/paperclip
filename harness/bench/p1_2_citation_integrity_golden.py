"""BEAAA-23857 P1.2 citation-integrity golden adapter.

Deterministic, single-turn, judge-model-free. All loading, oracle evaluation,
and envelope construction live in `harness/bench/scored_lane_common.py`; this
module only binds the family identifier so the registered-case executor and
the regression tests have a stable per-family entry point.

Source:
  - rubric/probe_mappings/p1_2_citation_integrity_golden.yaml@0.1.0
  - contracts/are-scanner-api-v1.md@v1.3.0
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.bench.scored_lane_common import (
    FAMILY_CORPUS_VERSIONS,
    ScoredLaneProbe,
    evaluate,
    family_yaml_path,
    load_agent_fixture,
    load_probes,
    run as _run_family,
    score_family,
)


FAMILY_ID = "p1_2_citation_integrity_golden"
DIM_ID = "P1.2"
CORPUS_VERSION = FAMILY_CORPUS_VERSIONS[FAMILY_ID]
YAML_PATH = family_yaml_path(FAMILY_ID)

__all__ = [
    "FAMILY_ID",
    "DIM_ID",
    "CORPUS_VERSION",
    "YAML_PATH",
    "ScoredLaneProbe",
    "evaluate",
    "load_agent_fixture",
    "load_probes",
    "run",
    "score_family",
]


def run(
    *,
    fixture_path: str | Path,
    yaml_path: str | Path = YAML_PATH,
    scanner_version: str | None = None,
) -> dict[str, Any]:
    """Score the family against a recorded agent fixture."""

    return _run_family(
        family_id=FAMILY_ID,
        fixture_path=fixture_path,
        yaml_path=yaml_path,
        scanner_version=scanner_version,
    )
