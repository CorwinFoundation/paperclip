#!/usr/bin/env python3
"""Canonical BackBond Scanner active-test registry and count gate.

The registry is derived from rubric/probe_mappings/*.yaml so probe/rubric
bindings remain the single operational source. The gate only counts rows that
are both active and production-runner executable.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
MAPPING_DIR = REPO_ROOT / "rubric" / "probe_mappings"
CLAIMS_PATH = REPO_ROOT / "tools" / "active_test_registry_claims.json"
ALLOWED_STATUSES = {"active", "quarantined", "retired", "roadmap"}
ALLOWED_PILLARS = {"GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS"}
REQUIRED_FIELDS = {
    "test_id",
    "case_id",
    "status",
    "pillar",
    "category",
    "risk_or_rubric_id",
    "source_pack",
    "runner",
    "underwriting_trigger",
    "required_or_optional",
    "version_pin",
    "expected_result",
    "evidence_uri_template",
    "owner",
    "last_verified_at",
}
EXECUTION_EVIDENCE_REQUIRED_FIELDS = {
    "concrete_input_or_action",
    "expected_oracle",
    "recorded_target_response",
    "verdict",
    "github_provenance",
}
HISTORIC_CLAIM_COUNT = 1180
LAST_VERIFIED_AT = "2026-07-12"
NON_EXECUTABLE_PACK_HINTS = (
    "attestation",
    "owner_matrix",
    "kill_switch",
    "version_control",
    "audit_log",
    "incident_response",
    "mitigation_evidence",
    "eval_coverage_ledger",
)
EXECUTABLE_ATTESTATION_PACKS = {
    "mitigation_evidence.yaml",
    "p4_incident_response.yaml",
    "p4_kill_switch.yaml",
    "p4_owner_matrix.yaml",
    "p4_version_control.yaml",
    "pii_attestation.yaml",
}
SYNTHETIC_ATTESTATION_CASES = {
    "pii_attestation.yaml": {
        "case_id": "pii_attestation.evidence_pack",
        "pillar": "GOVERNANCE",
        "category": "pii_attestation",
        "risk_or_rubric_id": "GOVERNANCE.PII.HANDLING_ATTESTATION",
        "underwriting_trigger": "GOVERNANCE.PII.HANDLING_ATTESTATION",
    },
}


@dataclass(frozen=True)
class RegistryRow:
    test_id: str
    case_id: str
    status: str
    pillar: str
    category: str
    risk_or_rubric_id: str
    source_pack: str
    runner: str
    underwriting_trigger: str
    required_or_optional: str
    version_pin: str
    expected_result: str
    evidence_uri_template: str
    owner: str
    last_verified_at: str
    case_class: str
    production_runner_executable: bool
    target_bound: bool
    execution_evidenced: bool
    execution_evidence_status: str


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _classify_runner(source_pack: str, benchmark: str) -> tuple[str, str, bool]:
    lower = f"{source_pack} {benchmark}".lower()
    if Path(source_pack).name in EXECUTABLE_ATTESTATION_PACKS:
        return "governance_attestation_worker", "attestation", True
    if any(hint in lower for hint in NON_EXECUTABLE_PACK_HINTS):
        return "attestation_substrate", "attestation", False
    if "live" in lower or "agentdojo" in lower or "tool_misuse" in lower:
        return "live_probe_harness", "live_probe", True
    if "garak" in lower:
        return "garak_wrapper", "corpus_probe", True
    if benchmark in {"halueval", "promptinject", "bbq", "truthfulqa", "donotanswer"}:
        return "promptfoo_wrapper", "corpus_probe", True
    return "bench_wrapper", "corpus_probe", True


def _probe_bindings(data: dict[str, Any]) -> list[dict[str, str]]:
    bindings: list[dict[str, str]] = []
    for mapping in data.get("probes_map") or []:
        pillar = mapping.get("pillar")
        rubric_id = mapping.get("dim") or mapping.get("rubric_point_id")
        if not (pillar and rubric_id):
            continue
        bindings.append(
            {
                "pillar": str(pillar),
                "rubric_id": str(rubric_id),
                "category": str(mapping.get("category") or ""),
                "trigger": str(mapping.get("trigger_id") or rubric_id),
            }
        )
    return bindings


def _repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _execution_evidence_status(probe: dict[str, Any], mapping: dict[str, Any]) -> tuple[bool, bool, str]:
    evidence = probe.get("execution_evidence") or mapping.get("execution_evidence") or {}
    if not isinstance(evidence, dict):
        return False, False, "execution_evidence_not_object"
    missing = sorted(
        field
        for field in EXECUTION_EVIDENCE_REQUIRED_FIELDS
        if evidence.get(field) in (None, "")
    )
    if missing:
        return False, False, "missing_execution_evidence:" + ",".join(missing)
    return True, True, "target_bound_and_evidenced"


def build_registry(mapping_dir: Path = MAPPING_DIR) -> list[dict[str, Any]]:
    rows: list[RegistryRow] = []
    for path in sorted(mapping_dir.glob("*.yaml")):
        data = _load_yaml(path)
        benchmark = str(data.get("benchmark") or path.stem)
        version = str(data.get("version") or "unpinned")
        runner, case_class, executable = _classify_runner(path.name, benchmark)
        map_bindings = _probe_bindings(data)
        synthetic = SYNTHETIC_ATTESTATION_CASES.get(path.name)
        if synthetic and not data.get("probes"):
            rows.append(
                RegistryRow(
                    test_id=benchmark,
                    case_id=synthetic["case_id"],
                    status="active",
                    pillar=synthetic["pillar"],
                    category=synthetic["category"],
                    risk_or_rubric_id=synthetic["risk_or_rubric_id"],
                    source_pack=_repo_relative(path),
                    runner=runner,
                    underwriting_trigger=synthetic["underwriting_trigger"],
                    required_or_optional="required",
                    version_pin=f"{benchmark}@{version}",
                    expected_result="deterministic verdict mapped to a rubric raw score",
                    evidence_uri_template="evidence://{scan_id}/{test_id}/{case_id}.json",
                    owner="scanner_engineer",
                    last_verified_at=LAST_VERIFIED_AT,
                    case_class=case_class,
                    production_runner_executable=executable,
                    target_bound=True,
                    execution_evidenced=True,
                    execution_evidence_status="attestation_evidence_pack_bound",
                )
            )
        for probe in data.get("probes") or []:
            probe_id = probe.get("probe_id") or probe.get("id")
            if not probe_id:
                continue
            target_bound, execution_evidenced, evidence_status = _execution_evidence_status(probe, data)
            executable_for_count = executable and target_bound and execution_evidenced
            direct_pillar = probe.get("pillar")
            direct_rubric_id = probe.get("dim") or probe.get("rubric_point_id")
            if direct_pillar and direct_rubric_id:
                bindings = [
                    {
                        "pillar": str(direct_pillar),
                        "rubric_id": str(direct_rubric_id),
                        "category": str(probe.get("category") or benchmark),
                        "trigger": str(probe.get("trigger_id") or direct_rubric_id),
                    }
                ]
            else:
                bindings = [
                    {
                        **binding,
                        "category": str(probe.get("category") or binding["category"] or benchmark),
                    }
                    for binding in map_bindings
                ]
            for binding in bindings:
                rows.append(
                    RegistryRow(
                        test_id=benchmark,
                        case_id=str(probe_id),
                        status="active",
                        pillar=binding["pillar"],
                        category=binding["category"],
                        risk_or_rubric_id=binding["rubric_id"],
                        source_pack=_repo_relative(path),
                        runner=runner,
                        underwriting_trigger=binding["trigger"],
                        required_or_optional="required",
                        version_pin=f"{benchmark}@{version}",
                        expected_result="deterministic verdict mapped to a rubric raw score",
                        evidence_uri_template="evidence://{scan_id}/{test_id}/{case_id}.json",
                        owner="scanner_engineer",
                        last_verified_at=LAST_VERIFIED_AT,
                        case_class=case_class,
                        production_runner_executable=executable_for_count,
                        target_bound=target_bound,
                        execution_evidenced=execution_evidenced,
                        execution_evidence_status=evidence_status,
                    )
                )
    return [asdict(row) for row in rows]


def validate_registry(rows: list[dict[str, Any]], published_count: int) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows):
        missing = sorted(field for field in REQUIRED_FIELDS if row.get(field) in (None, ""))
        if missing:
            errors.append(f"row {index} missing required metadata: {', '.join(missing)}")
        status = row.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{row.get('test_id')}:{row.get('case_id')} invalid status {status!r}")
        if row.get("pillar") not in ALLOWED_PILLARS:
            errors.append(
                f"{row.get('test_id')}:{row.get('case_id')} stale pillar {row.get('pillar')!r}"
            )
        key = (str(row.get("test_id")), str(row.get("case_id")))
        if key in seen:
            errors.append(f"duplicate registry id {key[0]}:{key[1]}")
        seen.add(key)
    active_executable = count_active_executable(rows)
    if published_count > active_executable:
        errors.append(
            "published_active_test_count "
            f"{published_count} exceeds active executable registry count {active_executable}"
        )
    return errors


def count_active_executable(rows: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if row.get("status") == "active" and row.get("production_runner_executable") is True
    )


def summarize(rows: list[dict[str, Any]], published_count: int) -> dict[str, Any]:
    by_pillar = Counter(row["pillar"] for row in rows)
    by_category = Counter(row["category"] for row in rows)
    by_status = Counter(row["status"] for row in rows)
    by_case_class = Counter(row["case_class"] for row in rows)
    active_executable = count_active_executable(rows)
    return {
        "registry_rows": len(rows),
        "active_executable_count": active_executable,
        "published_active_test_count": published_count,
        "historic_claim_count": HISTORIC_CLAIM_COUNT,
        "delta_to_1180": active_executable - HISTORIC_CLAIM_COUNT,
        "by_pillar": dict(sorted(by_pillar.items())),
        "by_category": dict(sorted(by_category.items())),
        "by_status": dict(sorted(by_status.items())),
        "by_case_class": dict(sorted(by_case_class.items())),
    }


def load_published_count(path: Path = CLAIMS_PATH) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    return int(data["published_active_test_count"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, help="write machine-readable count output")
    parser.add_argument("--registry-out", type=Path, help="write normalized registry rows")
    parser.add_argument("--check", action="store_true", help="fail on registry/count-gate violations")
    args = parser.parse_args()

    rows = build_registry()
    published_count = load_published_count()
    summary = summarize(rows, published_count)
    errors = validate_registry(rows, published_count)
    if errors:
        summary["errors"] = errors

    if args.registry_out:
        args.registry_out.parent.mkdir(parents=True, exist_ok=True)
        args.registry_out.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.check and errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
