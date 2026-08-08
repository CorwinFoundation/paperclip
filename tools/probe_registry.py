"""BEAAA-13678 active BackBond Scanner probe registry emitter."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from tools.active_test_registry import (
    _is_countable_active_executable,
    build_registry as build_active_test_registry,
)


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
    "evals/provisional_tau_support_crm.py",
]
AUTHORITATIVE_PATH = "tools/probe-registry/authoritative_registry.json"
SCHEMA_PATH = "tools/probe-registry/authoritative_registry.schema.json"
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


def _source_sha(repo: Path) -> str:
    """Git-style SHA-1 over the authoritative input closure, excluding outputs."""
    paths: set[Path] = {repo / "tools" / "probe_registry.py"}
    for pattern in SOURCE_INPUTS:
        paths.update(path for path in repo.glob(pattern) if path.is_file())
    digest = hashlib.sha1()
    for path in sorted(paths):
        relative = path.relative_to(repo).as_posix().encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _stable_key(row: dict[str, Any]) -> str:
    payload = {field: str(row.get(field, "")) for field in DEDUPE_FIELDS}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _index_unique_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index source rows and reject duplicate authority instead of hiding it."""
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        stable_id = _stable_key(row)
        if stable_id in unique:
            first = unique[stable_id]
            raise ValueError(
                "duplicate authoritative probe stable ID "
                f"{stable_id}: {first.get('source')} and {row.get('source')} "
                f"for {row.get('framework')}/{row.get('case_id_or_probe_id')}"
            )
        row["stable_executable_key"] = stable_id
        unique[stable_id] = row
    return unique


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


def _report_only_pilot_rows(repo: Path) -> list[dict[str, Any]]:
    """Register the tau support/CRM pilot without activating execution or scoring."""
    source = "evals/provisional_tau_support_crm.py"
    if not (repo / source).exists():
        return []
    return [
        {
            "bucket": "report_only",
            "framework": "tau-bench",
            "framework_version": "59a200c6d575d595120f1cb70fea53cef0632f6b",
            "corpus_or_family": "tau_support_crm_pilot",
            "case_id_or_probe_id": case_id,
            "rubric_point_id": None,
            "mode": "report_only_local_mock_tool_harness_v1",
            "source": source,
            "countable_as_executable": False,
            "reported": True,
            "scoring_active": False,
            "reason": "registered report-only pilot; excluded from default runtime and scoring",
        }
        for case_id in (
            "tau_support_crm.refund_credit.001",
            "tau_support_crm.account_change.001",
            "tau_support_crm.identity_boundary.001",
        )
    ]


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
        emitted = {
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
        if str(row.get("test_id")) == "consequential_action_confirmation":
            mapping = _load_yaml(repo / source)
            probe = next(
                (item for item in mapping.get("probes") or [] if str(item.get("id")) == str(row.get("case_id"))),
                {},
            )
            execution = probe.get("report_only_execution") or {}
            if execution.get("reported") is True:
                emitted.update(
                    {
                        "reported": True,
                        "scoring_active": False,
                        "default_runtime_executable": False,
                        "executed_in_latest_evidenced_run": False,
                        "execution_mode": "report_only_consequential_action_v0.1",
                        "runner_source": "harness/bench/consequential_action_confirmation.py",
                        "weight": 0.0,
                    }
                )
        rows.append(emitted)
    return rows


def build_registry(repo: Path = REPO_DEFAULT) -> dict[str, Any]:
    if getattr(yaml, "__version__", None) != PY_YAML_PIN:
        raise RuntimeError(f"PyYAML=={PY_YAML_PIN} required for deterministic registry parsing")
    source_sha = _source_sha(repo)
    active_points = _active_rubric_points(repo)
    rows = (
        _runnable_today_rows(repo)
        + _active_test_executable_rows(repo)
        + _mapping_rows(repo)
        + _fixture_rows(repo)
        + _report_only_pilot_rows(repo)
    )
    unique = _index_unique_rows(rows)
    rows = sorted(unique.values(), key=lambda row: tuple(str(row.get(f, "")) for f in DEDUPE_FIELDS))
    # Legacy active-test rows prove registration and an adapter binding, not that
    # the current default runtime executed or scored them. Keep those facts
    # separate and fail closed unless a source row carries the stronger evidence.
    for row in rows:
        legacy_runnable = bool(row["bucket"] == "runnable_today" and row["countable_as_executable"])
        row["categories"] = {
            "registered": True,
            "executable": legacy_runnable,
            "executable_on_default_runtime": bool(row.get("default_runtime_executable", False)),
            "executed_in_latest_evidenced_run": bool(row.get("executed_in_latest_evidenced_run", False)),
            "reported": bool(row.get("reported", False)),
            "scoring_active": bool(row.get("scoring_active", False)),
            "proxy_grade": row["bucket"] == "proxy_grade_non_customer_measured",
            "attestation_only": row["bucket"] == "attestation_only",
            "fixture_test_only": row["bucket"] == "fixture_test_only",
            "inactive_deferred": row["bucket"] == "inactive_deferred",
            "blocked_unsupported": not legacy_runnable and row["bucket"] not in {"fixture_test_only", "inactive_deferred", "report_only"},
        }
        row["stable_id"] = row.pop("stable_executable_key")
        row["source_provenance"] = {
            "path": row["source"],
            "version": row.get("version_pin") or row.get("framework_version") or "unversioned",
        }
        row.pop("countable_as_executable", None)
    category_names = list(rows[0]["categories"]) if rows else []
    category_counts = {
        name: sum(bool(row["categories"][name]) for row in rows)
        for name in category_names
    }
    runnable = [row for row in rows if row["categories"]["executable_on_default_runtime"]]
    runnable_by_dim: dict[str, list[str]] = {}
    for row in runnable:
        runnable_by_dim.setdefault(row["rubric_point_id"], []).append(row["stable_id"])
    point_status = []
    for point in active_points:
        dim = point["rubric_point_id"]
        keys = sorted(runnable_by_dim.get(dim, []))
        point_status.append(
            {
                **point,
                "runnable_entries": keys,
                "status": "executable" if keys else "fail_closed_unavailable",
                "unavailable_reason": None if keys else f"no current customer-true walker row in authoritative source closure {source_sha[:7]}",
            }
        )
    bucket_counts: dict[str, int] = {}
    for row in rows:
        bucket_counts[row["bucket"]] = bucket_counts.get(row["bucket"], 0) + 1
    return {
        "metadata": {
            "ticket_ref": "BEAAA-20991",
            "source_closure_sha": source_sha,
            "source_closure_sha_kind": "sha1-authoritative-source-closure-v1",
            "generated_from_content_sha": source_sha,
            "generated_from_content_sha_kind": "sha1-authoritative-source-closure-v1",
            "generated_at": "2026-08-06T00:00:00Z",
            "generation_timestamp_semantics": "release epoch for authoritative-probe-registry@1.0.1; deterministic regeneration does not use wall-clock time",
            "schema": SCHEMA_PATH,
            "contract": "are-scanner-api-v1@v1.3.0",
            "scanner_version": "0.5.0",
            "corpus_version": "0.2.0",
            "source_closure_version": f"registry-source-{source_sha[:7]}",
            "probe_set_version": "authoritative-probe-registry@1.0.1",
            "rubric_point_id": "ALL_ACTIVE_DIMENSIONS",
            "active_rubric_point_count": len(active_points),
            "n5_calibration_delta": "not_applicable_report_only_zero_weight",
            "classification": "rule-execution: report-only probe execution inside an existing rubric point; no score path change",
            "parser_dependency": f"PyYAML=={PY_YAML_PIN}",
            "stdlib_dependencies": ["pathlib", "json", "csv", "hashlib"],
            "open_source_first_constraint": "Parse existing corpus/mapping/harness/scanner assets as sources; only BackBond stable key, disposition buckets, and fail-closed reasons are hand-rolled.",
        },
        "source_inputs": SOURCE_INPUTS,
        "upstream_framework_pins": dict(sorted(FRAMEWORK_PINS.items())),
        "dedupe_key_fields": list(DEDUPE_FIELDS),
        "overlap_semantics": "Categories are non-additive sets. A stable-ID row may belong to multiple categories; never sum category counts to obtain a total.",
        "category_counts": category_counts,
        "bucket_definitions": {
            "runnable_today": "A registered adapter can materialize the row; this does not imply default-runtime, latest-run, reporting, or scoring activation.",
            "declared_minimum_only": "Mapping or corpus row exists, but no current full-scan execution row proves it runs.",
            "attestation_only": "Governance/operator evidence row; countable as evidence requirement, not an executable probe.",
            "proxy_grade_non_customer_measured": "Category-proxy or non-customer-measured row; visible but excluded from executable count.",
            "fixture_test_only": "Test fixture or harness corpus row; excluded from executable count.",
            "inactive_deferred": "Schema, scaffold, deferred, or inactive row; excluded from executable count.",
            "report_only": "Registered report output from an isolated local harness; excluded from default runtime, executable count, and scoring.",
        },
        "unique_executable_count": category_counts.get("executable", 0),
        "bucket_counts": dict(sorted(bucket_counts.items())),
        "active_rubric_points": point_status,
        "claimed_1180_disposition": {
            "claimed_count": 1180,
            "supported_unique_executable_count": category_counts.get("executable", 0),
            "claim_supported": False,
            "disposition": "unsupported_claim_capped_to_runnable_today_until_signed_full_scan_manifest_exists",
            "unsupported_rows_count": category_counts.get("blocked_unsupported", 0),
        },
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=str(REPO_DEFAULT))
    parser.add_argument("--emit", default=AUTHORITATIVE_PATH)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    registry = build_registry(Path(args.repo))
    output = Path(args.emit)
    if not output.is_absolute():
        output = Path(args.repo) / output
    rendered = json.dumps(registry, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not output.exists() or output.read_text(encoding="utf-8") != rendered:
            print(f"authoritative probe registry drift: regenerate {output}", file=sys.stderr)
            return 1
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
