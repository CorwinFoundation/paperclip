#!/usr/bin/env python3
"""Read-only validation for the BEAAA-18720 runtime registration."""
from __future__ import annotations

import json

from tools.active_test_registry import (
    build_registry,
    count_active_executable,
    count_target_bound_runtime,
    distinct_case_counts,
    load_published_count,
    validate_registry,
)
from tools.content_candidate_inventory import (
    EXPECTED_INVENTORY_FAMILIES,
    EXPECTED_INVENTORY_ROWS,
    INVENTORY_SHA256,
    load_candidate_inventory,
)
from tools.founder_dev_mode import assert_production_attested


def main() -> int:
    inventory = load_candidate_inventory()
    registry = build_registry()
    # Production boundary: this validator publishes customer-visible counts.
    # Fail closed if a dev-mode-scored family is live (unset dev-mode to publish).
    assert_production_attested(registry)
    candidates = [
        row for row in registry if row.get("content_candidate_inventory") is True
    ]
    distinct = distinct_case_counts(registry)
    errors = validate_registry(
        registry,
        published_count=load_published_count(),
        strict=True,
    )
    result = {
        "status": "PASS" if not errors else "FAIL",
        "inventory_sha256": INVENTORY_SHA256,
        "inventory_rows": len(inventory),
        "inventory_families": len({row["family_id"] for row in inventory}),
        "registered_inventory_rows": len(candidates),
        "new_candidate_adapter_rows": sum(
            row.get("runner") == "content_candidate_worker" for row in candidates
        ),
        "reused_existing_adapter_rows": sum(
            row.get("runner") != "content_candidate_worker" for row in candidates
        ),
        "registry_rows": len(registry),
        "active_executable_rows": count_active_executable(registry),
        "target_bound_runtime_rows": count_target_bound_runtime(registry),
        "distinct_runtime_tests": distinct["total"],
        "published_active_test_count": load_published_count(),
        "errors": errors,
    }
    expected = {
        "inventory_rows": EXPECTED_INVENTORY_ROWS,
        "inventory_families": EXPECTED_INVENTORY_FAMILIES,
        "registered_inventory_rows": 2_000,
        "new_candidate_adapter_rows": 1_782,
        "reused_existing_adapter_rows": 218,
        "registry_rows": 2_242,
        "active_executable_rows": 2_202,
        "target_bound_runtime_rows": 2_213,
        "distinct_runtime_tests": 2_201,
        "published_active_test_count": 2_202,
    }
    mismatches = {
        key: {"expected": value, "observed": result[key]}
        for key, value in expected.items()
        if result[key] != value
    }
    if mismatches:
        result["status"] = "FAIL"
        result["mismatches"] = mismatches
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
