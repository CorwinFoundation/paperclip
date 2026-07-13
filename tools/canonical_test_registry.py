"""BEAAA-13762 Phase 0 canonical scanner test registry emitter.

This freezes the machine-readable registry shape required by the CEO taxonomy.
It is metadata-only: no scoring math, rubric weights, judge models, or contract
output fields change.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools import probe_registry
from tools.active_test_registry import build_registry as build_active_test_registry


REGISTRY_VERSION = "scanner-test-registry-baseline-2026-07-13/v0.1"
CONTRACT_VERSION = "are-scanner-api-v1@v1.0"
CEO_DISPOSITIONS = {
    "production_active",
    "executable_not_connected",
    "blocked_dependency",
    "needs_validation",
    "quarantined",
    "duplicate",
    "fixture_only",
    "retired",
    "roadmap",
}
COUNTABLE_DISPOSITION = "production_active"
REQUIRED_FIELDS = {
    "asset_id",
    "case_id",
    "disposition",
    "rubric_point_id",
    "source",
    "framework",
    "version_pin",
    "countable_as_active_executable",
}
APPENDIX_A_CANDIDATES = (
    Path("IFA/scanner-test-registry-baseline-2026-07-13.csv"),
    Path("backbond/operations/paperclip/paperclip_updates_2026-07-13/scanner-test-registry-baseline-2026-07-13.csv"),
)


def default_appendix_a_path(repo: Path) -> Path | None:
    for candidate in APPENDIX_A_CANDIDATES:
        path = repo / candidate
        if path.exists():
            return path
    return None


def build_registry(repo: Path = probe_registry.REPO_DEFAULT, appendix_csv: Path | None = None) -> dict[str, Any]:
    source_path = appendix_csv or default_appendix_a_path(repo)
    if source_path is not None:
        rows = _rows_from_appendix_csv(source_path, repo)
        source_mode = "appendix_a_family_ledger"
        source_inputs = [str(_repo_relative(source_path, repo))]
    else:
        rows = _rows_from_probe_registry(repo)
        source_mode = "probe_registry_fallback_appendix_a_source_missing"
        source_inputs = list(probe_registry.SOURCE_INPUTS)

    rows = _mark_duplicate_executable_case_ids(rows)
    active_rows = [
        row
        for row in rows
        if row["disposition"] == COUNTABLE_DISPOSITION
        and row["countable_as_active_executable"] is True
    ]
    active_test_rows = build_active_test_registry(repo / "rubric" / "probe_mappings")
    active_registry_path = repo / "tools" / "probe-registry" / "active_registry.json"
    active_registry = _load_json_if_present(active_registry_path)
    errors = validate_registry(rows)
    payload = {
        "metadata": {
            "ticket_ref": "BEAAA-13762",
            "parent_ticket_ref": "BEAAA-13761",
            "registry_version": REGISTRY_VERSION,
            "contract": CONTRACT_VERSION,
            "classification": "rule-execution: metadata registry/count gate; no scoring path change",
            "scanner_version": "1.2.0",
            "corpus_version": "scanner-test-registry-baseline-2026-07-13",
            "probe_set_version": REGISTRY_VERSION,
            "rubric_point_id": "ALL_ACTIVE_DIMENSIONS",
            "n5_calibration_delta": "not_applicable_metadata_only_no_score_path_change",
            "source_mode": source_mode,
            "source_inputs": source_inputs,
            "counting_unit": "registry rows count unique mapping/probe rows; embedded corpus items are recorded separately and do not inflate registry row counts",
            "ceo_dispositions": sorted(CEO_DISPOSITIONS),
            "countable_disposition": COUNTABLE_DISPOSITION,
            "unique_active_executable_case_count": len({row["case_id"] for row in active_rows}),
            "appendix_family_count": _sum_int_field(rows, "appendix_family_row"),
            "appendix_embedded_case_count_total": _sum_int_field(rows, "embedded_case_count"),
            "appendix_t1_embedded_case_count": _sum_int_field(rows, "t1_embedded_case_count"),
            "appendix_production_active_embedded_case_count": _sum_int_field(
                rows, "production_active_embedded_case_count"
            ),
            "active_test_registry_rows": len(active_test_rows),
            "active_test_registry_active_executable_count": sum(
                1
                for row in active_test_rows
                if row.get("status") == "active" and row.get("production_runner_executable") is True
            ),
            "active_registry_point_count": len(active_registry.get("active_rubric_points", []))
            if isinstance(active_registry, dict)
            else 0,
        },
        "rows": rows,
    }
    payload["metadata"]["registry_sha256"] = _sha256_json(rows)
    if errors:
        payload["errors"] = errors
    return payload


def validate_registry(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen_asset_ids: set[str] = set()
    seen_active_case_ids: set[str] = set()
    for index, row in enumerate(rows):
        missing = sorted(field for field in REQUIRED_FIELDS if row.get(field) in (None, ""))
        if missing:
            errors.append(f"row {index} missing required fields: {', '.join(missing)}")
        disposition = row.get("disposition")
        if disposition not in CEO_DISPOSITIONS:
            errors.append(f"row {index} invalid disposition: {disposition!r}")
        asset_id = str(row.get("asset_id") or "")
        if asset_id in seen_asset_ids:
            errors.append(f"duplicate asset_id: {asset_id}")
        seen_asset_ids.add(asset_id)
        countable = row.get("countable_as_active_executable") is True
        if countable and disposition != COUNTABLE_DISPOSITION:
            errors.append(f"{asset_id} is countable but disposition is {disposition!r}")
        if disposition == COUNTABLE_DISPOSITION:
            case_id = str(row.get("case_id") or "")
            if case_id in seen_active_case_ids:
                errors.append(f"duplicate executable case_id: {case_id}")
            seen_active_case_ids.add(case_id)
    return errors


def _rows_from_probe_registry(repo: Path) -> list[dict[str, Any]]:
    baseline = probe_registry.build_registry(repo)
    rows = []
    for row in baseline["rows"]:
        disposition = _disposition_from_bucket(row)
        case_id = str(row.get("case_id_or_probe_id") or "")
        normalized = {
            "asset_id": str(row.get("stable_executable_key") or _row_asset_id(row)),
            "case_id": case_id,
            "disposition": disposition,
            "rubric_point_id": str(row.get("rubric_point_id") or "n/a"),
            "source": str(row.get("source") or ""),
            "framework": str(row.get("framework") or ""),
            "version_pin": str(row.get("version_pin") or _framework_pin(row)),
            "countable_as_active_executable": bool(
                disposition == COUNTABLE_DISPOSITION and row.get("countable_as_executable") is True
            ),
            "source_bucket": str(row.get("bucket") or ""),
            "reason": str(row.get("reason") or ""),
        }
        rows.append(normalized)
    return sorted(rows, key=lambda item: (item["disposition"], item["framework"], item["case_id"]))


def _rows_from_appendix_csv(path: Path, repo: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for index, row in enumerate(reader):
            if "family" in row and "embedded_case_count" in row:
                rows.append(_appendix_family_row(index, row, path, repo))
                continue
            disposition = _normalize_disposition(_first_present(row, "disposition", "ceo_disposition", "status"))
            case_id = _first_present(row, "case_id", "case_id_or_probe_id", "probe_id", "asset")
            normalized = {
                "asset_id": _first_present(row, "asset_id", "stable_executable_key") or _csv_asset_id(index, row),
                "case_id": case_id,
                "disposition": disposition,
                "rubric_point_id": _first_present(row, "rubric_point_id", "risk_or_rubric_id", "dim") or "n/a",
                "source": _first_present(row, "source", "source_pack", "path") or str(_repo_relative(path, repo)),
                "framework": _first_present(row, "framework", "runner", "benchmark") or "unknown",
                "version_pin": _first_present(row, "version_pin", "framework_version") or "unpinned",
                "countable_as_active_executable": _truthy(
                    _first_present(row, "countable_as_active_executable", "countable_as_executable")
                ),
                "source_bucket": _first_present(row, "bucket", "tier") or "appendix_a",
                "reason": _first_present(row, "reason", "notes") or "",
            }
            rows.append(normalized)
    return sorted(rows, key=lambda item: (item["disposition"], item["framework"], item["case_id"]))


def _appendix_family_row(index: int, row: Mapping[str, Any], path: Path, repo: Path) -> dict[str, Any]:
    family = _first_present(row, "family") or f"appendix_a_row_{index}"
    disposition_raw = _first_present(row, "proposed_registry_status", "disposition", "status")
    disposition = _normalize_disposition(disposition_raw)
    embedded_case_count_raw = _first_present(row, "embedded_case_count")
    embedded_case_count = _safe_int(embedded_case_count_raw)
    tier = _first_present(row, "tier")
    is_t1 = tier.startswith("T1 ")
    is_production_active = disposition == COUNTABLE_DISPOSITION
    return {
        "asset_id": f"appendix_a:{family}",
        "case_id": family,
        "disposition": disposition,
        "rubric_point_id": _first_present(row, "risk_dimensions") or "n/a",
        "source": _first_present(row, "source_file") or str(_repo_relative(path, repo)),
        "framework": _first_present(row, "runner_path") or "appendix_a_family_ledger",
        "version_pin": "appendix_a@2026-07-13",
        "countable_as_active_executable": is_production_active,
        "source_bucket": tier or "appendix_a",
        "reason": _first_present(row, "source_status_field") or "",
        "appendix_family_row": 1,
        "embedded_case_count": embedded_case_count,
        "embedded_case_count_raw": embedded_case_count_raw,
        "t1_embedded_case_count": embedded_case_count if is_t1 else 0,
        "production_active_embedded_case_count": embedded_case_count if is_production_active else 0,
        "proposed_registry_status_raw": disposition_raw,
        "runner_path": _first_present(row, "runner_path"),
    }


def _mark_duplicate_executable_case_ids(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for row in rows:
        row = dict(row)
        if row["disposition"] == COUNTABLE_DISPOSITION and row["case_id"] in seen:
            row["disposition"] = "duplicate"
            row["countable_as_active_executable"] = False
            row["reason"] = (row.get("reason") or "") + "; duplicate production_active case_id"
        if row["disposition"] == COUNTABLE_DISPOSITION:
            seen.add(row["case_id"])
        result.append(row)
    return result


def _disposition_from_bucket(row: Mapping[str, Any]) -> str:
    bucket = str(row.get("bucket") or "")
    if bucket == "runnable_today" and row.get("countable_as_executable") is True:
        return COUNTABLE_DISPOSITION
    if bucket == "fixture_test_only":
        return "fixture_only"
    if bucket == "inactive_deferred":
        return "roadmap"
    if bucket == "attestation_only":
        return "needs_validation"
    if bucket in {"declared_minimum_only", "proxy_grade_non_customer_measured"}:
        return "executable_not_connected"
    return "needs_validation"


def _first_present(row: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _normalize_disposition(value: str) -> str:
    normalized = value.strip()
    if normalized.startswith("production_active"):
        return COUNTABLE_DISPOSITION
    return normalized


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _sum_int_field(rows: Iterable[Mapping[str, Any]], field: str) -> int:
    return sum(_safe_int(str(row.get(field, 0))) for row in rows)


def _framework_pin(row: Mapping[str, Any]) -> str:
    framework = str(row.get("framework") or "unknown")
    version = str(row.get("framework_version") or "unpinned")
    return f"{framework}@{version}"


def _row_asset_id(row: Mapping[str, Any]) -> str:
    return _sha256_json({key: str(value) for key, value in row.items()})


def _csv_asset_id(index: int, row: Mapping[str, Any]) -> str:
    return _sha256_json({"index": index, **{key: str(value) for key, value in row.items()}})


def _sha256_json(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _repo_relative(path: Path, repo: Path) -> Path:
    try:
        return path.resolve().relative_to(repo.resolve())
    except ValueError:
        return path


def _load_json_if_present(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(probe_registry.REPO_DEFAULT))
    parser.add_argument("--appendix-csv", type=Path)
    parser.add_argument("--emit", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    registry = build_registry(Path(args.repo), appendix_csv=args.appendix_csv)
    args.emit.parent.mkdir(parents=True, exist_ok=True)
    args.emit.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(registry["metadata"], indent=2, sort_keys=True))
    if args.check and registry.get("errors"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
