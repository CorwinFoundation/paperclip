"""Validate and report the BEAAA-17667 family activation ledger.

This is deliberately a fail-closed development runner.  A family is working
only when the frozen row says it is target-bound and its smoke result passed.
Catalogue, fixture, governance, dependency-blocked, and unexecuted rows cannot
be counted as working.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "tools" / "probe-registry" / "family_activation_ledger_v0.1.json"
REQUIRED_ROW_FIELDS = {
    "family_id",
    "current_state",
    "owner",
    "real_blocker",
    "runner_adapter_work",
    "required_cases",
    "automated_tests",
    "target_bound_smoke_result",
    "acceptance_criteria",
    "scanner_version",
    "corpus_version",
}
NON_WORKING_STATES = {
    "blocked_dependency",
    "catalogue_only",
    "fixture_only",
    "governance_only",
    "needs_validation",
    "executable_not_connected",
}


class LedgerError(ValueError):
    """Raised when the frozen ledger violates its machine-readable contract."""


def load_ledger(path: Path = DEFAULT_LEDGER) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("families")
    if not isinstance(rows, list):
        raise LedgerError("families must be a list")
    if len(rows) != 61:
        raise LedgerError(f"expected exactly 61 families, got {len(rows)}")
    ids = [row.get("family_id") for row in rows if isinstance(row, dict)]
    if len(ids) != 61 or len(set(ids)) != 61 or any(not value for value in ids):
        raise LedgerError("family_id values must be 61 unique non-empty strings")
    for row in rows:
        missing = REQUIRED_ROW_FIELDS - row.keys()
        if missing:
            raise LedgerError(f"{row.get('family_id')}: missing {sorted(missing)}")
        if row["scanner_version"] != "0.5.0":
            raise LedgerError(f"{row['family_id']}: scanner_version must be 0.5.0")
        if not row["corpus_version"]:
            raise LedgerError(f"{row['family_id']}: corpus_version must be non-empty")
    return payload


def family_is_working(row: dict[str, Any]) -> bool:
    smoke = row["target_bound_smoke_result"]
    if row["current_state"] in NON_WORKING_STATES:
        return False
    return (
        row["current_state"] == "target_bound_working"
        and isinstance(smoke, dict)
        and smoke.get("status") == "passed"
        and smoke.get("target_kind") == "development_fixture"
        and bool(smoke.get("evidence"))
    )


def build_baseline(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload["families"]
    working = [row["family_id"] for row in rows if family_is_working(row)]
    failures = [
        {
            "family_id": row["family_id"],
            "current_state": row["current_state"],
            "failure": row["target_bound_smoke_result"].get(
                "failure", row["real_blocker"]
            ),
        }
        for row in rows
        if not family_is_working(row)
    ]
    return {
        "schema_version": "backbond.family-activation-baseline/v0.1",
        "issue": "BEAAA-17667",
        "scanner_version": payload["scanner_version"],
        "corpus_version": payload["corpus_version"],
        "probe-set-version": payload["probe-set-version"],
        "target": {
            "kind": "development_fixture",
            "credentials": "test_only",
            "customer_data": False,
            "paid_call_budget": 0,
            "call_cap": 0,
            "score_activation": False,
        },
        "working_count": len(working),
        "total_count": len(rows),
        "truthful_baseline": f"{len(working)}/{len(rows)}",
        "working_family_ids": working,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--write-baseline", type=Path)
    args = parser.parse_args()
    baseline = build_baseline(load_ledger(args.ledger))
    rendered = json.dumps(baseline, indent=2, sort_keys=True) + "\n"
    if args.write_baseline:
        args.write_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.write_baseline.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if baseline["total_count"] == 61 else 1


if __name__ == "__main__":
    raise SystemExit(main())
