"""BEAAA-13678 active BackBond Scanner probe registry emitter."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

from tools.active_test_registry import (
    _is_countable_active_executable,
    build_registry as build_active_test_registry,
)
from tools.source_identity import repo_head


REPO_DEFAULT = Path.cwd()
PY_YAML_PIN = "6.0.2"
FRAMEWORK_PINS = {
    "promptfoo": "0.115.0",
    "garak": "0.10.0",
    "inspect-ai": "0.3.220",
    "pyrit": "0.6.0",
    "agentdojo": "0.1.35",
    "llm-guard": "0.3.16",
    "presidio-analyzer": "2.2.358",
    "presidio-anonymizer": "2.2.358",
    "tau-bench": "59a200c6...",
    "tau2-bench": "5ebebbe8...",
}
SOURCE_INPUTS = [
    "rubric/probe_mappings/*.yaml",
    "corpus/**",
    "harness/bench/**",
    "scanners/*_runner.py",
    "tests/fixtures/golden_agents/**",
]
DEDUPE_FIELDS = (
    "framework",
    "corpus_or_family",
    "case_id_or_probe_id",
    "rubric_point_id",
    "mode",
)


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _repo_head(repo: Path) -> str:
    return repo_head(repo)


def _stable_key(row: dict[str, Any]) -> str:
    payload = {field: str(row.get(field, "")) for field in DEDUPE_FIELDS}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _active_rubric_points(repo: Path) -> list[dict[str, str]]:
    rubric = _load_yaml(repo / "rubric" / "rubric.yaml")
    points: list[dict[str, str]] = []
    for pillar, pdata in sorted((rubric.get("pillars") or {}).items()):
        for dim in pdata.get("dimensions") or []:
            rubric_point_id = str(dim["id"])
            points.append(
                {
                    "rubric_point_id": rubric_point_id,
                    "pillar": pillar,
                    "name": str(dim.get("name", "")),
                }
            )
    return sorted(points, key=lambda item: item["rubric_point_id"])


def _bucket_for_mapping(path: Path, data: dict[str, Any]) -> str:
    provenance = data.get("provenance") or {}
    label = str(provenance.get("label_template", ""))
    rule = str(provenance.get("rule", ""))
    status = str(data.get("status", "")).lower()
    if "deferred" in status or "scaffold" in status or path.name.endswith(".schema.yaml"):
        return "inactive_deferred"
    if "attestation" in label or "operator_evidence" in label or data.get("evidence_tiers"):
        return "attestation_only"
    if "category-proxy" in label or "never label as" in rule:
        return "proxy_grade_non_customer_measured"
    return "declared_minimum_only"


def _mapping_rows(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((repo / "rubric" / "probe_mappings").glob("*.yaml")):
        data = _load_yaml(path)
        bucket = _bucket_for_mapping(path, data)
        benchmark = str(data.get("benchmark") or path.stem)
        version = str(data.get("version") or "unversioned")
        for probe in data.get("probes") or []:
            probe_id = str(probe.get("probe_id") or probe.get("id") or "")
            dim = str(probe.get("dim") or probe.get("rubric_point_id") or "")
            if not probe_id or not dim:
                continue
            rows.append(
                {
                    "bucket": bucket,
                    "framework": benchmark,
                    "framework_version": version,
                    "corpus_or_family": path.stem,
                    "case_id_or_probe_id": probe_id,
                    "rubric_point_id": dim,
                    "mode": "mapping",
                    "source": path.relative_to(repo).as_posix(),
                    "countable_as_executable": False,
                    "reason": "mapping/corpus declaration is not a customer-true full-scan execution row",
                }
            )
    return rows


def _fixture_rows(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    roots = [repo / "tests" / "fixtures", repo / "tests" / "probe_sets", repo / "harness" / "fixtures"]
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in {".json", ".jsonl", ".yaml", ".yml"}:
                continue
            rows.append(
                {
                    "bucket": "fixture_test_only",
                    "framework": "fixture",
                    "framework_version": "n/a",
                    "corpus_or_family": root.relative_to(repo).as_posix(),
                    "case_id_or_probe_id": str(path.relative_to(root)),
                    "rubric_point_id": "n/a",
                    "mode": "fixture",
                    "source": path.relative_to(repo).as_posix(),
                    "countable_as_executable": False,
                    "reason": "fixture/test asset is not a production full-scan execution row",
                }
            )
    return rows


def _runnable_today_rows(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    wrap = repo / "scanners" / "wraps" / "inspect_section1_v0.py"
    dims = {
        "P4.1": "inspect_ai.evals.alignment.section1.instruction_following_minimal",
        "P1.1": "inspect_ai.evals.safety.section1.behavioral_safety_minimal",
        "P2.1": "inspect_ai.evals.privacy.section1.pii_disclosure_minimal",
        "P3.1": "inspect_ai.evals.truthfulness.section1.uncertainty_minimal",
    }
    for dim, upstream_task_id in sorted(dims.items()):
        rows.append(
            {
                "bucket": "inactive_deferred",
                "framework": "inspect_ai",
                "framework_version": "0.3.220",
                "corpus_or_family": "inspect_section1_v0",
                "case_id_or_probe_id": upstream_task_id,
                "rubric_point_id": dim,
                "mode": "customer_agent_walker_v0.3",
                "source": wrap.relative_to(repo).as_posix(),
                "countable_as_executable": False,
                "reason": "legacy smoke wrapper is not part of the production full-scan execution path",
            }
        )
    loop_scorer = repo / "harness" / "bench" / "loop_stability_budget_guard.py"
    if loop_scorer.exists():
        rows.append(
            {
                "bucket": "inactive_deferred",
                "framework": "backbond_loop_stability_budget_guard",
                "framework_version": "0.1",
                "corpus_or_family": "loop_stability_budget_guard",
                "case_id_or_probe_id": "loop_stability_budget_guard_v0_1",
                "rubric_point_id": "OPS.LOOP_STABILITY.BUDGET_GUARD",
                "mode": "deterministic_bench_worker_v0.1",
                "source": loop_scorer.relative_to(repo).as_posix(),
                "countable_as_executable": False,
                "reason": "conditional benchmark requires pre-existing loop evidence and is not invoked by the production full scan",
                "evidence_uri_template": "evidence://{scan_id}/loop_stability_budget_guard/loop_stability_budget_guard_v0_1.json",
                "worker_runner": "bench_wrapper",
                "version_pin": "backbond_loop_stability_budget_guard@0.1",
            }
        )
    return rows


def _active_test_executable_rows(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in build_active_test_registry(repo / "rubric" / "probe_mappings"):
        if not _is_countable_active_executable(row):
            continue
        version_pin = str(row.get("version_pin") or "")
        _, _, framework_version = version_pin.partition("@")
        source = str(row.get("source_pack") or "")
        rows.append(
            {
                "bucket": "runnable_today",
                "framework": str(row.get("runner") or row.get("test_id") or "unknown"),
                "framework_version": framework_version or "unpinned",
                "corpus_or_family": str(row.get("test_id") or ""),
                "case_id_or_probe_id": str(row.get("case_id") or ""),
                "rubric_point_id": str(row.get("risk_or_rubric_id") or ""),
                "mode": "production_worker_registry_v0.1",
                "source": source,
                "countable_as_executable": True,
                "reason": "BEAAA-13644 active-test registry marks this row active with a pinned production runner",
                "evidence_uri_template": str(row.get("evidence_uri_template") or "evidence://{scan_id}/{test_id}/{case_id}.json"),
                "worker_runner": str(row.get("runner") or ""),
                "version_pin": version_pin,
            }
        )
    return rows


def build_registry(repo: Path = REPO_DEFAULT) -> dict[str, Any]:
    if getattr(yaml, "__version__", None) != PY_YAML_PIN:
        raise RuntimeError(f"PyYAML=={PY_YAML_PIN} required for deterministic registry parsing")
    repo_head = _repo_head(repo)
    active_points = _active_rubric_points(repo)
    rows = _runnable_today_rows(repo) + _active_test_executable_rows(repo) + _mapping_rows(repo) + _fixture_rows(repo)
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        row["stable_executable_key"] = _stable_key(row)
        unique.setdefault(row["stable_executable_key"], row)
    rows = sorted(unique.values(), key=lambda row: tuple(str(row.get(f, "")) for f in DEDUPE_FIELDS))
    runnable = [row for row in rows if row["bucket"] == "runnable_today" and row["countable_as_executable"]]
    runnable_by_dim: dict[str, list[str]] = {}
    for row in runnable:
        runnable_by_dim.setdefault(row["rubric_point_id"], []).append(row["stable_executable_key"])
    point_status = []
    for point in active_points:
        dim = point["rubric_point_id"]
        keys = sorted(runnable_by_dim.get(dim, []))
        point_status.append(
            {
                **point,
                "runnable_entries": keys,
                "status": "executable" if keys else "fail_closed_unavailable",
                "unavailable_reason": None if keys else f"no current customer-true walker row in WRAP_MODULES at {repo_head[:7]}",
            }
        )
    bucket_counts: dict[str, int] = {}
    for row in rows:
        bucket_counts[row["bucket"]] = bucket_counts.get(row["bucket"], 0) + 1
    return {
        "metadata": {
            "ticket_ref": "BEAAA-13678",
            "repo": str(repo),
            "repo_head": repo_head,
            "contract": "are-scanner-api-v1@v1.0",
            "scanner_version": "1.2.0",
            "corpus_version": f"registry-baseline-{repo_head[:7]}",
            "probe_set_version": "active-probe-registry@0.1.0",
            "rubric_point_id": "ALL_ACTIVE_DIMENSIONS",
            "active_rubric_point_count": len(active_points),
            "n5_calibration_delta": "not_applicable_metadata_only_no_score_path_change",
            "classification": "rule-execution: refactor/metadata registry that does not touch scoring behavior",
            "remediation_ticket_ref": "BEAAA-13739",
            "parser_dependency": f"PyYAML=={PY_YAML_PIN}",
            "stdlib_dependencies": ["pathlib", "json", "csv", "hashlib"],
            "open_source_first_constraint": "Parse existing corpus/mapping/harness/scanner assets as sources; only BackBond stable key, disposition buckets, and fail-closed reasons are hand-rolled.",
        },
        "source_inputs": SOURCE_INPUTS,
        "upstream_framework_pins": dict(sorted(FRAMEWORK_PINS.items())),
        "dedupe_key_fields": list(DEDUPE_FIELDS),
        "bucket_definitions": {
            "runnable_today": "Customer-true walker emits the row at current HEAD; countable as executable.",
            "declared_minimum_only": "Mapping or corpus row exists, but no current full-scan execution row proves it runs.",
            "attestation_only": "Governance/operator evidence row; countable as evidence requirement, not an executable probe.",
            "proxy_grade_non_customer_measured": "Category-proxy or non-customer-measured row; visible but excluded from executable count.",
            "fixture_test_only": "Test fixture or harness corpus row; excluded from executable count.",
            "inactive_deferred": "Schema, scaffold, deferred, or inactive row; excluded from executable count.",
        },
        "unique_executable_count": len(runnable),
        "bucket_counts": dict(sorted(bucket_counts.items())),
        "active_rubric_points": point_status,
        "claimed_1180_disposition": {
            "claimed_count": 1180,
            "supported_unique_executable_count": len(runnable),
            "claim_supported": False,
            "disposition": "unsupported_claim_capped_to_runnable_today_until_signed_full_scan_manifest_exists",
            "unsupported_rows_count": sum(1 for row in rows if not row["countable_as_executable"]),
        },
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=str(REPO_DEFAULT))
    parser.add_argument("--emit", required=True)
    args = parser.parse_args()
    registry = build_registry(Path(args.repo))
    output = Path(args.emit)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
