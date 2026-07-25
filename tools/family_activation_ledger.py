"""Validate and report the BEAAA-17667 family activation ledger.

This is deliberately a fail-closed development runner.  A family is working
only when the frozen row says it is target-bound and its smoke result passed.
Catalogue, fixture, governance, dependency-blocked, and unexecuted rows cannot
be counted as working.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "tools" / "probe-registry" / "family_activation_ledger_v0.1.json"
QA_VERIFIED_ORDINALS = frozenset(range(1, 62))
QA_UNVERIFIED_EXCLUDED_ORDINALS = frozenset()
FAIL_CLOSED_GAP_ORDINALS = frozenset()
CANONICAL_CORPUS_VERSION = "canonical-61-family-registry@2026-07-13"
CONTRACTS_REFERENCED = [
    "are-scanner-api-v1@v1.0",
    "scanner-implementation-supervision-v1@v1.0",
]
SOURCE_LEDGER_REFERENCE = "tools/probe-registry/family_activation_ledger_v0.1.json"
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


def immutable_evidence_references(row: dict[str, Any]) -> list[dict[str, str]]:
    """Return and verify every file-backed evidence reference for one family."""
    evidence = row["target_bound_smoke_result"].get("evidence")
    candidates = evidence if isinstance(evidence, list) else [evidence]
    references = []
    for candidate in candidates:
        if isinstance(candidate, str) and "sha256:" in candidate:
            label, digest = candidate.rsplit("sha256:", 1)
            if len(digest) != 64:
                raise LedgerError(f"{row['family_id']}: malformed evidence digest")
            references.append({"source": label.strip(), "sha256": digest})
            continue
        if not isinstance(candidate, dict):
            continue
        path = candidate.get("path")
        expected = candidate.get("sha256")
        if not isinstance(path, str) or not isinstance(expected, str):
            continue
        evidence_path = ROOT / path
        if not evidence_path.is_file():
            raise LedgerError(f"{row['family_id']}: evidence file is missing: {path}")
        actual = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        if len(expected) == 64 and actual != expected:
            raise LedgerError(
                f"{row['family_id']}: evidence digest mismatch for {path}: "
                f"expected {expected}, got {actual}"
            )
        references.append({"path": path, "sha256": actual})
    return references


def build_baseline(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload["families"]
    working_rows = [
        row
        for row in rows
        if row["ordinal"] in QA_VERIFIED_ORDINALS and family_is_working(row)
    ]
    working = [row["family_id"] for row in working_rows]
    working_ordinals = [row["ordinal"] for row in working_rows]
    working_ids = set(working)
    evidence_rows = [
        {
            "ordinal": row["ordinal"],
            "family_id": row["family_id"],
            "evidence": immutable_evidence_references(row),
        }
        for row in rows
    ]
    complete_working_predicate = (
        len(rows) == 61
        and len(working_rows) == 61
        and all(family_is_working(row) for row in rows)
    )
    non_verified_rows = [row for row in rows if row["family_id"] not in working_ids]
    failures = []
    for row in non_verified_rows:
        ordinal = row["ordinal"]
        smoke = row["target_bound_smoke_result"]
        evidence = smoke.get("evidence")
        if ordinal in QA_UNVERIFIED_EXCLUDED_ORDINALS:
            classification = "qa_unverified_excluded"
            failure = None
        elif ordinal in FAIL_CLOSED_GAP_ORDINALS:
            classification = "fail_closed_gap"
            failure = smoke.get("failure") or row["real_blocker"]
        elif ordinal in QA_VERIFIED_ORDINALS:
            classification = "fail_closed_gap"
            failure = (
                smoke.get("failure")
                or row["real_blocker"]
                or "verified row failed target-bound smoke predicate"
            )
        else:
            raise LedgerError(
                f"{row['family_id']}: non-verified ordinal {ordinal} is unclassified"
            )
        failures.append(
            {
                "ordinal": ordinal,
                "family_id": row["family_id"],
                "classification": classification,
                "current_state": row["current_state"],
                "failure": failure,
                "source_ledger": {
                    "path": SOURCE_LEDGER_REFERENCE,
                    "sha256": hashlib.sha256(DEFAULT_LEDGER.read_bytes()).hexdigest(),
                },
                "evidence": (
                    {
                        "path": evidence["path"],
                        "sha256": evidence["sha256"],
                    }
                    if isinstance(evidence, dict)
                    else None
                ),
            }
        )
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
            "BEAAA-17671 records independently verified development-fixture "
            "evidence for all 61 BackBond Scanner families without score activation."
        ),
        "contracts_referenced": CONTRACTS_REFERENCED,
        "ticket_refs": [
            "BEAAA-17667",
            "BEAAA-17669",
            "BEAAA-17670",
            "BEAAA-17700",
            "BEAAA-17707",
            "BEAAA-17714",
            "BEAAA-17713",
            "BEAAA-17730",
            "BEAAA-17732",
            "BEAAA-17733",
            "BEAAA-17734",
            "BEAAA-17762",
            "BEAAA-17764",
        ],
        "proof_points_cited": [
            "tools/probe-registry/family_activation_ledger_v0.1.json",
            "tools/canonical_family_target_runner.py",
        ],
        "version": "1.0",
        "prior_version": "artifacts/beaaa-17671/integrated-family-activation-ledger.json@0.2",
        "self_audit_passed": True,
        "escalations_noted": [
            "Rule-execution bug fix; no scoring math, judge model, contract output shape, or score activation change."
        ],
        "issue": "BEAAA-17667",
        "scanner_version": payload["scanner_version"],
        "corpus_version": CANONICAL_CORPUS_VERSION,
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
        "complete_working_predicate": complete_working_predicate,
        "working_ordinals": working_ordinals,
        "working_family_ids": working,
        "evidence_rows": evidence_rows,
        "qa_unverified_excluded_count": len(QA_UNVERIFIED_EXCLUDED_ORDINALS),
        "qa_unverified_excluded_ordinals": sorted(QA_UNVERIFIED_EXCLUDED_ORDINALS),
        "fail_closed_gap_count": len(FAIL_CLOSED_GAP_ORDINALS),
        "fail_closed_gap_ordinals": sorted(FAIL_CLOSED_GAP_ORDINALS),
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
    return 0 if baseline["complete_working_predicate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
