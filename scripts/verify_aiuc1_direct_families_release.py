#!/usr/bin/env python3
"""Fail closed unless every AIUC-1 Day-1 family is production-dispatchable."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from harness.direct_family_adapter import (  # noqa: E402
    AUTHENTICATION_FAMILY,
    DIRECT_FAMILIES,
    DISCLOSURE_FAMILY,
    case_for_id,
)
from tools.active_test_registry import build_registry, count_active_executable  # noqa: E402
from tools.framework_worker_registry import WORKERS, worker_id_for_registry_row  # noqa: E402
from tools.tranche1_observational import load_config  # noqa: E402


EXPECTED_CASE_COUNTS = {
    AUTHENTICATION_FAMILY: 32,
    DISCLOSURE_FAMILY: 32,
    "tool_misuse_param_tamper": 12,
    "cross_customer_data_exposure": 8,
    "tool_log_trace_secret_leakage": 8,
    "delegated_authority_confused_deputy": 8,
    "revocation_execution_time_authorization": 8,
    "file_temporary_link_leakage": 8,
}
EXPECTED_REPLICATE_CALLS = {
    AUTHENTICATION_FAMILY: 128,
    DISCLOSURE_FAMILY: 80,
    "tool_misuse_param_tamper": 60,
    "cross_customer_data_exposure": 40,
    "tool_log_trace_secret_leakage": 40,
    "delegated_authority_confused_deputy": 40,
    "revocation_execution_time_authorization": 40,
    "file_temporary_link_leakage": 40,
}
LEGACY_SUPPORTING_ROW_EXPECTATIONS = {
    "tool_misuse_param_tamper": {
        "runner": "content_candidate_worker",
        "execution_evidenced": False,
        "execution_evidence_status": "runtime_bound_evidence_pending",
        "scoring_active": True,
        "reporting_status": "zero_weight_observational",
        "execution_mode": "content_candidate_inventory_adapter",
    },
    "cross_customer_data_exposure": {
        "runner": "bench_wrapper",
        "execution_evidenced": True,
        "execution_evidence_status": "legacy_target_bound_production_counted",
        "scoring_active": False,
        "reporting_status": "reported_not_scored",
        "execution_mode": "legacy_registered_case_executor",
    },
}


def verify() -> dict[str, object]:
    # This validates every tamper-evidence pin, including the shared registered
    # executor changed by this release.
    load_config()
    rows = build_registry()
    direct = [row for row in rows if row.get("test_id") in DIRECT_FAMILIES]
    errors: list[str] = []
    counts = Counter(str(row.get("test_id")) for row in direct)

    if dict(counts) != EXPECTED_CASE_COUNTS:
        errors.append(f"direct family case counts mismatch: {dict(counts)}")

    selection_mode = "production-registry-selector"
    try:
        from api.full_scan_executor import registry_cases_from_rows

        selected = {case.case_id for case in registry_cases_from_rows(rows)}
    except ModuleNotFoundError as exc:
        if exc.name != "fcntl":
            raise
        # full_scan_job_persistence intentionally uses fcntl in the Linux
        # production image. Keep the developer gate usable on Windows while
        # CI/image verification exercises the actual production selector.
        selection_mode = "windows-production-selector-equivalent"
        selected = {
            str(row.get("runtime_case_identity") or row.get("case_id") or "")
            for row in rows
            if row.get("status") == "active"
            and row.get("production_runner_executable") is True
        }
    replicate_calls: Counter[str] = Counter()
    for row in direct:
        family_id = str(row.get("test_id") or "")
        case_id = str(row.get("case_id") or "")
        production_case_id = str(
            row.get("runtime_case_identity")
            or row.get("case_id")
            or row.get("case_id_or_probe_id")
            or ""
        )
        if production_case_id not in selected:
            errors.append(f"production dispatcher omitted {family_id}:{case_id}")
        expected = {
            "status": "active",
            "production_runner_executable": True,
            "target_bound": True,
            **LEGACY_SUPPORTING_ROW_EXPECTATIONS.get(
                family_id,
                {
                    "runner": "direct_family_adapter",
                    "execution_evidenced": False,
                    "execution_evidence_status": "runtime_bound_evidence_pending",
                    "scoring_active": False,
                    "reporting_status": "reported_not_scored",
                    "execution_mode": "synthetic_direct_family_adapter",
                },
            ),
        }
        for field, value in expected.items():
            if row.get(field) != value:
                errors.append(
                    f"{family_id}:{case_id} {field}={row.get(field)!r}; expected {value!r}"
                )
        if worker_id_for_registry_row(row) != "day1_report_only":
            errors.append(f"{family_id}:{case_id} is not bound to day1_report_only")
        try:
            case = case_for_id(family_id, case_id)
        except KeyError:
            errors.append(f"{family_id}:{case_id} has no checked-in adapter case")
            continue
        if int(row.get("k_replicates") or 0) != case.replicate_count:
            errors.append(f"{family_id}:{case_id} replicate count drift")
        replicate_calls[family_id] += case.replicate_count

    if dict(replicate_calls) != EXPECTED_REPLICATE_CALLS:
        errors.append(f"direct family replicate totals mismatch: {dict(replicate_calls)}")
    worker = WORKERS.get("day1_report_only")
    if worker is None or worker.reported_not_scored_only is not True:
        errors.append("day1_report_only worker is missing or may emit measured score")

    # These are registered and runnable but do not alter the public executed-test
    # claim before target-derived evidence is produced and promoted separately.
    published_executed_count = count_active_executable(rows)
    if published_executed_count != 2_346:
        errors.append(
            f"published executed-test count changed: {published_executed_count} != 2346"
        )

    summary: dict[str, object] = {
        "gate": "aiuc1-direct-families-release/v2",
        "passed": not errors,
        "errors": errors,
        "families": dict(sorted(counts.items())),
        "registered_cases": len(direct),
        "planned_target_calls": sum(replicate_calls.values()),
        "planned_target_calls_by_family": dict(sorted(replicate_calls.items())),
        "published_executed_test_count": published_executed_count,
        "selection_mode": selection_mode,
        "numeric_score_output": False,
        "score_contribution": False,
        "legacy_zero_weight_observational_families": ["tool_misuse_param_tamper"],
        "disposition": "day1-supporting-evidence",
    }
    return summary


def main() -> int:
    summary = verify()
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
