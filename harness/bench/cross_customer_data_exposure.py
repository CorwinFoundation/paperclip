"""AIUC-1 A005 deterministic tenant-boundary candidate adapter."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.bench.r2_rob_common import REPO_ROOT, envelope_for_dim, load_agent_fixture, load_probes, score_min_formula

FAMILY_ID = "cross_customer_data_exposure"
DIM_ID = "ROBUSTNESS.PII.SCAN_TIME_DETECTION"
YAML_PATH = REPO_ROOT / "rubric" / "probe_mappings" / f"{FAMILY_ID}.yaml"
CANDIDATE_RULE_ID = "A005_MIN_COMPOSE_V1"
NA_JUSTIFICATION_KEY = "A005.no_multi_tenant_data_access"


def compose_candidate(
    *, existing_raw: float, candidate_raw: float, architecture_flags: dict[str, Any]
) -> dict[str, Any]:
    """Apply the reversible A005 rule in the sandbox, never released scoring."""
    applies = architecture_flags.get("multi_tenant_data_access") is True
    if not applies:
        return {
            "candidate_rule_id": CANDIDATE_RULE_ID,
            "applicable": False,
            "status": "N/A",
            "na_justification_key": NA_JUSTIFICATION_KEY,
            "existing_raw": float(existing_raw),
            "candidate_raw": None,
            "composed_raw": float(existing_raw),
        }
    return {
        "candidate_rule_id": CANDIDATE_RULE_ID,
        "applicable": True,
        "status": "composed",
        "na_justification_key": None,
        "existing_raw": float(existing_raw),
        "candidate_raw": float(candidate_raw),
        "composed_raw": min(float(existing_raw), float(candidate_raw)),
    }


def run(
    *,
    yaml_path: str | Path = YAML_PATH,
    fixture_path: str | Path,
    scanner_version: str = "0.4.1",
    existing_raw: float | None = None,
    architecture_flags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    probes = load_probes(yaml_path)
    responses = load_agent_fixture(fixture_path)
    scoring = score_min_formula(probes, responses, FAMILY_ID)
    envelope = envelope_for_dim(dim_id=DIM_ID, family_id=FAMILY_ID, scoring_result=scoring, scanner_version=scanner_version)
    envelope["scoring_active"] = False
    envelope["activation_gate"] = "pre_activation_scoring_sandbox"
    if existing_raw is not None:
        envelope["sandbox_composition"] = compose_candidate(
            existing_raw=existing_raw,
            candidate_raw=float(envelope["raw"]),
            architecture_flags=architecture_flags or {},
        )
    return envelope
