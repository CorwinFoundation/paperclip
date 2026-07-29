#!/usr/bin/env python3
"""Deterministic content-only validator for the BEAAA-18741 catalog."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "artifacts/beaaa-18720/batches/batch-3-residency-resources-sandbox-patch-review.jsonl"
PARENT = ROOT / "artifacts/beaaa-18715/unified-probe-inventory.jsonl"
EVIDENCE = ROOT / "artifacts/beaaa-18720/batches/batch-3-validation-evidence.json"
FAMILIES = {
    "data_residency_locality_boundary",
    "resource_budget_enforcement",
    "sandbox_process_network_boundary",
    "code_patch_scope_preservation",
    "code_review_regression_detection",
}
REQUIRED = {
    "stable_case_id",
    "case_slug",
    "family_id",
    "substantive_cluster",
    "actor_trust_boundary",
    "design_partner_profiles",
    "applicability",
    "preconditions",
    "prompt_turn_sequence",
    "actual_state_sequence",
    "expected_observable_behavior",
    "failure_condition",
    "deterministic_oracle",
    "evidence_fields",
    "control_type",
    "provenance",
    "reporting_status",
}
COMMON = {
    "active", "actual", "after", "against", "agent", "baseline", "behavior",
    "boundary", "bounded", "capture", "case", "commit", "condition", "confirm",
    "current", "declared", "effect", "effects", "equals", "evidence", "expected",
    "fixture", "loaded", "make", "observed", "operation", "oracle", "policy",
    "preview", "prohibited", "record", "receipt", "required", "scenario",
    "specific", "state", "synthetic", "transition", "user", "value", "zero",
}


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z]{3,}", text.lower().replace("_", " "))
        if token not in COMMON
    }


def behavioral_skeleton(row: dict) -> frozenset[str]:
    """Ignore IDs, slug field, numerals, and shared wrapper vocabulary."""
    oracle = row["deterministic_oracle"]
    parts = [
        row["substantive_cluster"],
        row["actor_trust_boundary"],
        row["expected_observable_behavior"],
        row["failure_condition"],
        oracle["field"],
        " ".join(turn["content"] for turn in row["prompt_turn_sequence"]),
        " ".join(row["actual_state_sequence"]),
    ]
    return frozenset(tokens(" ".join(parts)))


def expected_skeleton(row: dict) -> frozenset[str]:
    """Expected behavior only, with identifiers and wrapper terms absent."""
    return frozenset(tokens(row["expected_observable_behavior"]))


def failure_skeleton(row: dict) -> frozenset[str]:
    """Failure predicate only, with identifiers and wrapper terms absent."""
    return frozenset(tokens(row["failure_condition"]))


def old_skeleton(row: dict) -> frozenset[str]:
    oracle = row.get("deterministic_oracle", row.get("oracle", {}))
    parts = [
        str(row.get("substantive_cluster", row.get("cluster", ""))),
        str(row.get("actor_trust_boundary", "")),
        str(row.get("expected_observable_behavior", row.get("expected_behavior", ""))),
        str(row.get("failure_condition", "")),
        str(oracle.get("field", "")) if isinstance(oracle, dict) else "",
    ]
    return frozenset(tokens(" ".join(parts)))


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def main() -> int:
    rows = load(CATALOG)
    parent = load(PARENT)
    errors: list[str] = []

    if len(rows) != 125:
        errors.append(f"expected 125 rows, found {len(rows)}")
    counts = Counter(row.get("family_id") for row in rows)
    if set(counts) != FAMILIES or any(counts[family] != 25 for family in FAMILIES):
        errors.append(f"family counts invalid: {dict(counts)}")

    for index, row in enumerate(rows, 1):
        missing = REQUIRED - set(row)
        if missing:
            errors.append(f"row {index}: missing {sorted(missing)}")
        if len(row.get("prompt_turn_sequence", [])) < 3:
            errors.append(f"row {index}: fewer than three explicit turns")
        if len(row.get("actual_state_sequence", [])) < 5:
            errors.append(f"row {index}: fewer than five actual states")
        if row.get("reporting_status") != "reported_not_scored":
            errors.append(f"row {index}: reporting status changed")
        if row.get("applicability", {}).get("na_is_pass") is not False:
            errors.append(f"row {index}: N/A can pass")

    for field in ("stable_case_id", "dedupe_sha256", "semantic_behavior_fingerprint"):
        values = [row.get(field) for row in rows]
        if len(set(values)) != 125:
            errors.append(f"{field} is not unique")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["family_id"]].append(row)
    for family, family_rows in grouped.items():
        clusters = {row["substantive_cluster"] for row in family_rows}
        positives = sum(row["control_type"] == "positive" for row in family_rows)
        if len(clusters) < 8:
            errors.append(f"{family}: only {len(clusters)} clusters")
        if positives < 3:
            errors.append(f"{family}: only {positives} positive controls")

    skeletons = [behavioral_skeleton(row) for row in rows]
    expected_skeletons = [expected_skeleton(row) for row in rows]
    failure_skeletons = [failure_skeleton(row) for row in rows]
    if len(set(skeletons)) != 125:
        errors.append("identity-insensitive behavioral skeletons are not unique")
    if len(set(expected_skeletons)) != 125:
        errors.append("identifier/slug/wrapper-stripped expected skeletons are not unique")
    if len(set(failure_skeletons)) != 125:
        errors.append("identifier/slug/wrapper-stripped failure skeletons are not unique")

    old_skeletons = [old_skeleton(row) for row in parent]
    overlaps = []
    maximum = (0.0, "", "")
    for new_index, new in enumerate(skeletons):
        for old_index, old in enumerate(old_skeletons):
            score = jaccard(new, old)
            if score > maximum[0]:
                maximum = (
                    score,
                    rows[new_index]["stable_case_id"],
                    str(parent[old_index].get("stable_case_id", parent[old_index].get("case_id", ""))),
                )
            if score >= 0.50:
                overlaps.append((score, rows[new_index]["stable_case_id"], old_index + 1))
    if overlaps:
        errors.append(f"{len(overlaps)} unexplained old-vs-new token overlaps >= 0.50")

    report = {
        "status": "FAIL" if errors else "PASS",
        "rows": len(rows),
        "family_counts": dict(sorted(counts.items())),
        "clusters_per_family": {
            family: len({row["substantive_cluster"] for row in family_rows})
            for family, family_rows in sorted(grouped.items())
        },
        "positive_controls_per_family": {
            family: sum(row["control_type"] == "positive" for row in family_rows)
            for family, family_rows in sorted(grouped.items())
        },
        "unique_behavioral_skeletons": len(set(skeletons)),
        "unique_expected_skeletons": len(set(expected_skeletons)),
        "unique_failure_skeletons": len(set(failure_skeletons)),
        "old_new_pairs_at_or_above_0_50": len(overlaps),
        "maximum_old_new_similarity": maximum,
        "errors": errors,
    }
    EVIDENCE.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
