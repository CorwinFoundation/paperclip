"""Derive the read-only release marker from the generated production registry."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tools.active_test_registry import (
    _is_countable_active_executable,
    build_registry,
    distinct_case_counts,
    load_published_count,
)


def _normalized_registry_bytes(rows: list[dict[str, Any]]) -> bytes:
    return json.dumps(
        rows,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _excluded_row(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("status") != "active":
        disposition = "disabled"
    elif row.get("production_runner_executable") is not True:
        disposition = "incompatible"
    else:
        disposition = "reported_not_scored"
    return {
        "test_id": str(row.get("test_id") or ""),
        "case_id": str(row.get("case_id") or ""),
        "disposition": disposition,
    }


def build_release_registry_marker(repo: Path) -> dict[str, Any]:
    """Return identity and counts from one in-memory generated registry."""
    rows = build_registry(repo / "rubric" / "probe_mappings")
    published_count = load_published_count(repo / "tools" / "active_test_registry_claims.json")
    executable = [row for row in rows if _is_countable_active_executable(row)]
    excluded = [_excluded_row(row) for row in rows if not _is_countable_active_executable(row)]
    marker = {
        "schema_version": "backbond-scanner-registry-marker@1",
        "normalized_registry_sha256": hashlib.sha256(
            _normalized_registry_bytes(rows)
        ).hexdigest(),
        "published_executable_count": published_count,
        "generated_executable_count": len(executable),
        "distinct_runtime_count": distinct_case_counts(rows)["total"],
        "excluded_rows": excluded,
    }
    if marker["generated_executable_count"] != published_count:
        raise ValueError(
            "generated executable count does not match published count: "
            f"{marker['generated_executable_count']} != {published_count}"
        )
    return marker
