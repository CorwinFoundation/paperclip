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
BASELINE_ACTIVE_ORDINALS = range(41, 62)
BRAND_BACKBONE_VERSION = "v1.6"
BRAND_BACKBONE_SHA256 = (
    "a2ffc4f919b0f71a17b9f4506ac4edf11cd37956ff494f00d7a45f0b166ea090"
)
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
    if not isinstance(smoke, dict):
        return False
    if smoke.get("smoke_summary") == "artifacts/beaaa-17670/target-bound-smoke/summary.json":
        runner_path = smoke.get("runner_path")
        if not isinstance(runner_path, str) or not runner_path or runner_path == "-":
            return False
        required_cases = row["required_cases"]
        cases_executed = smoke.get("cases_executed")
        if cases_executed != required_cases:
            return False
        evidence_refs = smoke.get("target_evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            return False
        if isinstance(required_cases, int) and required_cases > 0:
            case_ids = [
                ref.get("case_id")
                for ref in evidence_refs
                if isinstance(ref, dict)
                and ref.get("kind") == "development_fixture_case_result"
            ]
            if len(case_ids) != required_cases or len(set(case_ids)) != required_cases:
                return False
    return (
        row["current_state"] == "target_bound_working"
        and smoke.get("status") == "passed"
        and smoke.get("target_kind") == "development_fixture"
        and bool(smoke.get("evidence"))
    )


def build_baseline(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload["families"]
    working_rows = [
        row
        for row in rows
        if row["ordinal"] in BASELINE_ACTIVE_ORDINALS and family_is_working(row)
    ]
    working = [row["family_id"] for row in working_rows]
    working_ordinals = [row["ordinal"] for row in working_rows]
    working_ids = set(working)
    failures = [
        {
            "family_id": row["family_id"],
            "current_state": row["current_state"],
            "failure": row["target_bound_smoke_result"].get(
                "failure", row["real_blocker"]
            ),
        }
        for row in rows
        if row["family_id"] not in working_ids
    ]
    return {
        "schema_version": "backbond.family-activation-baseline/v0.1",
        "artifact_type": "other",
        "owner": "scanner_engineer",
        "persona_audience": "internal",
        "phase": 1,
        "canonical_terms_only": True,
        "brand_backbone_version": BRAND_BACKBONE_VERSION,
        "brand_backbone_sha256": BRAND_BACKBONE_SHA256,
        "brand_backbone_verified": True,
        "focus_claim": (
            "BEAAA-17670 records reproducible development-fixture baseline "
            "evidence for BackBond Scanner batch C without score activation."
        ),
        "contracts_referenced": [
            "are-scanner-api-v1@v1.3.0",
            "scanner-implementation-supervision-v1@v1.1",
        ],
        "ticket_refs": ["BEAAA-17667", "BEAAA-17670", "BEAAA-17700"],
        "proof_points_cited": [
            "tools/probe-registry/family_activation_ledger_v0.1.json",
            "tools/canonical_family_target_runner.py",
        ],
        "version": "0.2",
        "prior_version": "artifacts/beaaa-17670/family_activation_baseline.json@0.1",
        "self_audit_passed": True,
        "escalations_noted": [
            "Rule-execution bug fix; no scoring math, judge model, contract output shape, or score activation change."
        ],
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
        "working_ordinals": working_ordinals,
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
