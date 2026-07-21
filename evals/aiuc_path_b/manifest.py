"""Full-scan manifest for AIUC Path B lane-C families (BEAAA-16272).

Turns the checked-in registry (rubric/aiuc_path_b/family_registry.yaml) into the
full-scan manifest section, honoring each family's `activation_enabled` flag (the
rollback lever). Cross-validates the registry against the live family SPECs so
the two can never silently drift — a mismatch raises, which the targeted CI turns
into a hard failure.

reported-not-scored: every manifest entry carries `scored: false`; this module
imports the family SPECs but NOT any scoring path, so it cannot move a golden
score.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from evals.aiuc_path_b import base
from evals.aiuc_path_b import consequential_action_confirmation as cac
from evals.aiuc_path_b import customer_defined_risk_pack as cdr
from evals.aiuc_path_b import human_escalation_flagging as hef
from evals.aiuc_path_b import mcp_tool_integrity as mcp

_REPO = Path(__file__).resolve().parents[2]
REGISTRY_PATH = _REPO / "rubric" / "aiuc_path_b" / "family_registry.yaml"

# family name -> live module (source of truth for SPEC + reference policies).
FAMILY_MODULES = {
    "human_escalation_flagging": hef,
    "consequential_action_confirmation": cac,
    "customer_defined_risk_pack": cdr,
    "mcp_tool_integrity": mcp,
}


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate_registry(registry: dict[str, Any] | None = None) -> None:
    """Assert each registry row agrees with its live family SPEC.

    Guards the six-point convention against registry/code drift: version pin,
    replication K, case count, rubric_point_ids (both namespaces), scoring mode,
    and applicability gate must all match the SPEC.
    """
    registry = registry or load_registry()
    rows = {r["family"]: r for r in registry["families"]}
    if set(rows) != set(FAMILY_MODULES):
        raise ValueError(f"registry families {set(rows)} != code families {set(FAMILY_MODULES)}")
    for name, module in FAMILY_MODULES.items():
        spec = module.SPEC
        row = rows[name]
        if row["version_pin"] != spec.version:
            raise ValueError(f"{name}: version_pin {row['version_pin']} != SPEC {spec.version}")
        if int(row["replication_required"]) != spec.replication_required:
            raise ValueError(f"{name}: replication_required mismatch")
        if int(row["case_count"]) != len(spec.cases):
            raise ValueError(f"{name}: case_count {row['case_count']} != {len(spec.cases)}")
        if list(row["rubric_point_ids"]) != list(spec.rubric_point_ids):
            raise ValueError(f"{name}: rubric_point_ids mismatch")
        if len(spec.rubric_point_ids) < 2:
            raise ValueError(f"{name}: needs both namespaces (P<n>.<m> + PILLAR.FAMILY.SUB)")
        if row["scoring"] != "reported_not_scored":
            raise ValueError(f"{name}: scoring must be reported_not_scored")
        gate = row.get("applicability_gate")
        expected_flag = spec.applicability_flag
        actual_flag = gate.get("flag") if isinstance(gate, dict) else None
        if actual_flag != expected_flag:
            raise ValueError(f"{name}: applicability gate {actual_flag} != SPEC {expected_flag}")


def full_scan_manifest(registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Full-scan manifest section: activation-enabled families, reported-not-scored."""
    registry = registry or load_registry()
    validate_registry(registry)
    entries = []
    for row in registry["families"]:
        if not row.get("activation_enabled", False):
            continue  # rollback lever: disabled families drop out of the manifest
        entries.append(
            {
                "family": row["family"],
                "tier": row["tier"],
                "probe_set_version": row["version_pin"],
                "corpus_version": row["corpus_version"],
                "rubric_point_ids": list(row["rubric_point_ids"]),
                "case_count": row["case_count"],
                "replication_required": row["replication_required"],
                "scored": False,
                "scoring": "reported_not_scored",
                "runner": row["runner"],
                "applicability_gate": (row.get("applicability_gate") or {}).get("flag"),
            }
        )
    return {
        "ticket_ref": registry["ticket_ref"],
        "scanner_version": registry["scanner_version_candidate"],
        "contract": registry["contract"],
        "reported_not_scored": True,
        "families": entries,
    }


def run_all(architecture_flags: dict[str, bool] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Execute every activation-enabled family under its compliant reference policy, K=5."""
    manifest = full_scan_manifest()
    out: dict[str, list[dict[str, Any]]] = {}
    for entry in manifest["families"]:
        module = FAMILY_MODULES[entry["family"]]
        out[entry["family"]] = base.replicate(
            module.SPEC, module.compliant_policy, architecture_flags=architecture_flags
        )
    return out
