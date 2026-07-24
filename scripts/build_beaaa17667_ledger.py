"""Build the frozen BEAAA-17667 ledger from the canonical 61-row inventory."""
from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "IFA" / "scanner-test-registry-baseline-2026-07-13.csv"
OUTPUT = ROOT / "tools" / "probe-registry" / "family_activation_ledger_v0.1.json"

BLOCKERS = {
    "blocked_dependency": "Pinned external framework/dataset adapter is not runnable in the normal development target runner.",
    "catalogue_only": "Registry/catalogue metadata is not an executable target-bound probe.",
    "governance_only": "Governance or attestation evidence is not a target-bound behavioral runner.",
    "fixture_only": "Fixture/configuration exists without a target-bound runtime path.",
    "needs_validation": "Runner or cases require validation and target binding before activation.",
    "executable_not_connected": "Implementation is not connected to the normal development target runner with passing smoke evidence.",
}


def normalized_state(row: dict[str, str]) -> str:
    state = row["proposed_registry_status"].split(" (")[0]
    if row["tier"].startswith("T4"):
        return (
            "governance_only"
            if row["family"].startswith("p4_")
            or row["family"] in {"pii_attestation", "mitigation_evidence"}
            else "catalogue_only"
        )
    if state == "production_active":
        return "executable_not_connected"
    if state not in BLOCKERS:
        return "executable_not_connected"
    return state


def main() -> None:
    families = []
    with SOURCE.open(newline="", encoding="utf-8") as source:
        for ordinal, source_row in enumerate(csv.DictReader(source), 1):
            state = normalized_state(source_row)
            blocker = BLOCKERS[state]
            case_count = source_row["embedded_case_count"]
            required_cases = int(case_count) if case_count.isdigit() else case_count
            families.append(
                {
                    "ordinal": ordinal,
                    "family_id": source_row["family"],
                    "rubric_point_id": source_row["risk_dimensions"],
                    "current_state": state,
                    "owner": "Scanner Engineer",
                    "real_blocker": blocker,
                    "runner_adapter_work": (
                        f"Bind {source_row['runner_path']} to the normal development "
                        "target runner, enforce test-only credentials and bounded calls, "
                        "and record terminal evidence."
                    ),
                    "required_cases": required_cases,
                    "automated_tests": [],
                    "target_bound_smoke_result": {
                        "status": "failed_closed",
                        "target_kind": "development_fixture",
                        "calls_attempted": 0,
                        "call_cap": 0,
                        "paid_calls": 0,
                        "customer_data": False,
                        "score_activation": False,
                        "failure": blocker,
                        "evidence": None,
                    },
                    "acceptance_criteria": [
                        "Normal development target runner reaches a terminal result for every required case.",
                        "Automated tests prove deterministic pass/fail behavior and fail closed on unavailable evidence.",
                        "Development-fixture smoke uses test credentials only, bounded call caps, no customer data, and no score activation.",
                    ],
                    "scanner_version": "0.5.0",
                    "corpus_version": "scanner-test-registry-baseline-2026-07-13",
                    "probe-set-version": "family-activation-ledger/v0.1",
                    "source_file": source_row["source_file"],
                }
            )
    payload = {
        "schema_version": "backbond.family-activation-ledger/v0.1",
        "issue": "BEAAA-17667",
        "classification": "rule-execution",
        "contract": "contracts/are-scanner-api-v1.md@v1.0 (grandfathered base contract; active patches unchanged)",
        "scanner_version": "0.5.0",
        "corpus_version": "scanner-test-registry-baseline-2026-07-13",
        "probe-set-version": "family-activation-ledger/v0.1",
        "rubric_point_id": "multi-family activation inventory (61 frozen rows; no scoring activation)",
        "n5_calibration_delta": {
            "status": "not_score_affecting",
            "total_max_delta": 0,
            "pillar_max_delta": 0,
            "basis": "Ledger and development baseline do not enter scoring.",
        },
        "cto_cosign_ref": None,
        "paid_call_budget": 0,
        "score_activation": False,
        "source_registry": str(SOURCE.relative_to(ROOT)),
        "families": families,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(families)} rows to {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
