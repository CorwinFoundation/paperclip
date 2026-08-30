#!/usr/bin/env python3
"""Validate a controlled post-deploy full-scan manifest for direct families."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from harness.direct_family_adapter import DIRECT_FAMILIES, case_for_id  # noqa: E402


def verify_manifest(
    manifest: Mapping[str, Any], *, require_all_pass: bool = False
) -> dict[str, Any]:
    all_rows = manifest.get("evidence_rows")
    errors: list[str] = []
    if not isinstance(all_rows, list):
        all_rows = []
        errors.append("manifest evidence_rows is missing or malformed")
    rows = [row for row in all_rows if row.get("test_id") in DIRECT_FAMILIES]
    identities = [(str(row.get("test_id")), str(row.get("probe_id"))) for row in rows]
    if len(rows) != 64:
        errors.append(f"expected 64 direct-family rows, found {len(rows)}")
    if len(set(identities)) != len(identities):
        errors.append("duplicate direct-family evidence identity")

    verdicts: Counter[str] = Counter()
    families: Counter[str] = Counter()
    for row in rows:
        family_id = str(row.get("test_id") or "")
        case_id = str(row.get("probe_id") or "")
        families[family_id] += 1
        verdict = str(row.get("verdict") or "")
        verdicts[verdict] += 1
        label = f"{family_id}:{case_id}"
        if row.get("status") != "complete":
            errors.append(f"{label} did not complete: {row.get('status')!r}")
        if verdict not in {"PASS", "FAIL"}:
            errors.append(f"{label} has non-behavioral verdict {verdict!r}")
        if require_all_pass and verdict != "PASS":
            errors.append(f"{label} did not pass controlled canary")
        if not str(row.get("evidence_ref") or "").startswith("evidence://"):
            errors.append(f"{label} has no evidence reference")
        if row.get("numeric_result") is not None:
            errors.append(f"{label} emitted a numeric score")
        if row.get("score_contribution") is not False:
            errors.append(f"{label} may contribute to score")
        if row.get("disposition") != "report-only":
            errors.append(f"{label} disposition is not report-only")
        if int(row.get("judge_calls") or 0) != 0:
            errors.append(f"{label} unexpectedly called a judge")
        try:
            expected_calls = case_for_id(family_id, case_id).replicate_count
        except KeyError:
            errors.append(f"{label} is not in the checked-in corpus")
            continue
        if int(row.get("target_calls") or 0) != expected_calls:
            errors.append(
                f"{label} target_calls={row.get('target_calls')!r}; expected {expected_calls}"
            )

    expected_families = {
        "authentication_bypass_resistance": 32,
        "sensitive_data_disclosure_resistance": 32,
    }
    if dict(families) != expected_families:
        errors.append(f"family evidence counts mismatch: {dict(families)}")

    return {
        "gate": "aiuc1-direct-families-canary/v1",
        "passed": not errors,
        "errors": errors,
        "families": dict(sorted(families.items())),
        "verdicts": dict(sorted(verdicts.items())),
        "require_all_pass": require_all_pass,
        "target_calls": sum(int(row.get("target_calls") or 0) for row in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--require-all-pass", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    summary = verify_manifest(manifest, require_all_pass=args.require_all_pass)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
