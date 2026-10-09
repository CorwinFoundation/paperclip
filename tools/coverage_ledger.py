"""Validate and project the Full Scan coverage ledger.

Inventories remain independent: this module deliberately exposes no grand total.
Scenario counts and attempt counts are also separate namespaces.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


OUTCOMES = ("pass", "fail", "flaky", "could_not_run")
REQUIRED_COUNTS = (
    "registered_unique_scenarios",
    "selectable",
    "selected_unique_scenarios",
    "evaluation_rows",
    "attempt_rows",
    "decisive",
    *OUTCOMES,
    "calibrated",
)


def validate_ledger(ledger: dict[str, Any]) -> None:
    if not ledger.get("as_of_date"):
        raise ValueError("as_of_date is required")
    if ledger.get("scoring_active") is not False:
        raise ValueError("coverage ledger must not activate scoring")
    if ledger.get("inventories_are_additive") is not False:
        raise ValueError("inventories_are_additive must be false")

    keys: set[tuple[str, str, str]] = set()
    for row in ledger.get("bindings", []):
        key = tuple(row.get(field, "") for field in ("pack_id", "family_id", "target_capability"))
        if not all(key) or key in keys:
            raise ValueError(f"binding key must be complete and unique: {key}")
        keys.add(key)
        counts = row.get("counts", {})
        missing = set(REQUIRED_COUNTS) - counts.keys()
        if missing:
            raise ValueError(f"{key}: missing counts {sorted(missing)}")
        if any(not isinstance(counts[name], int) or counts[name] < 0 for name in REQUIRED_COUNTS):
            raise ValueError(f"{key}: counts must be non-negative integers")
        if counts["attempt_rows"] != sum(counts[name] for name in OUTCOMES):
            raise ValueError(f"{key}: attempt_rows must equal pass+fail+flaky+could_not_run")
        if counts["decisive"] != counts["pass"] + counts["fail"]:
            raise ValueError(f"{key}: decisive must equal pass+fail")
        if counts["selected_unique_scenarios"] > counts["selectable"]:
            raise ValueError(f"{key}: selected unique scenarios exceed selectable")
        if counts["attempt_rows"] > counts["evaluation_rows"]:
            raise ValueError(f"{key}: attempt rows exceed evaluation rows")
        if row.get("scenario_id_namespace") == row.get("attempt_id_namespace"):
            raise ValueError(f"{key}: scenario and attempt namespaces must differ")
        eligibility = row.get("scoring_eligibility")
        if eligibility not in {"eligible", "ineligible", "unknown"}:
            raise ValueError(f"{key}: invalid scoring_eligibility")
        if eligibility == "eligible" and not row.get("calibration_evidence_ref"):
            raise ValueError(f"{key}: eligible rows require calibration evidence")
        semantic = row.get("semantic_uniqueness")
        if semantic is not None:
            qualified = semantic.get("qualified_behavioral_identities")
            unqualified = semantic.get("unqualified_source_identities")
            if not isinstance(qualified, int) or not isinstance(unqualified, int):
                raise ValueError(f"{key}: semantic identity counts must be integers")
            if qualified + unqualified != counts["registered_unique_scenarios"]:
                raise ValueError(f"{key}: semantic identity counts must reconcile to source identities")
            if unqualified and semantic.get("status") != "partially_verified":
                raise ValueError(f"{key}: unqualified semantic identities require partially_verified status")

    refuse_controls = [
        control for control in ledger.get("aggregate_controls", [])
        if control.get("control_type") == "refuse_everything"
    ]
    if len(refuse_controls) != 1:
        raise ValueError("exactly one refuse-everything aggregate control is required")
    for control in refuse_controls:
        if control.get("authorized_task_completion_rate") != 0.0:
            raise ValueError("refuse-everything control must complete zero authorized tasks")
        if control.get("aggregate_disposition") != "fail":
            raise ValueError("refuse-everything control must fail the aggregate projection")
        if control.get("pooled_pass_rate") is None:
            raise ValueError("refuse-everything control must retain the legacy pooled pass rate")


def project(ledger: dict[str, Any]) -> dict[str, Any]:
    validate_ledger(ledger)
    rows = []
    for row in ledger["bindings"]:
        counts = row["counts"]
        rows.append(
            {
                "binding": {name: row[name] for name in ("pack_id", "family_id", "target_capability")},
                "inventory": counts,
                "scoring_eligibility": row["scoring_eligibility"],
                "customer_applicability": row["customer_applicability"],
                "deployed_runtime_ready": row["deployed_runtime_ready"],
                "unsupported_capabilities": row.get("unsupported_capabilities", []),
                "semantic_uniqueness": row.get("semantic_uniqueness"),
                "last_evidenced_date": row.get("last_evidenced_date"),
            }
        )
    return {
        "ledger_id": ledger["ledger_id"],
        "aggregation_version": ledger["aggregation_version"],
        "as_of_date": ledger["as_of_date"],
        "scoring_active": False,
        "inventories_are_additive": False,
        "deployed_identity": ledger.get("deployed_identity"),
        "bindings": rows,
        "baseline_delta": ledger.get("baseline_delta", []),
        "aggregate_controls": ledger.get("aggregate_controls", []),
    }


def render_markdown(projection: dict[str, Any]) -> str:
    lines = [
        f"# Coverage ledger — {projection['as_of_date']}",
        "",
        "Inventories are independent and may overlap. No supported-total sum is reported. Scoring is inactive.",
        "",
        "| pack / family / capability | unique | selectable | selected unique | eval rows | attempt rows | decisive | pass | fail | flaky | CNR | calibrated | score | applicable | runtime |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for row in projection["bindings"]:
        binding, counts = row["binding"], row["inventory"]
        name = " / ".join(binding.values())
        lines.append(
            f"| {name} | {counts['registered_unique_scenarios']} | {counts['selectable']} | "
            f"{counts['selected_unique_scenarios']} | {counts['evaluation_rows']} | "
            f"{counts['attempt_rows']} | {counts['decisive']} | "
            f"{counts['pass']} | {counts['fail']} | {counts['flaky']} | "
            f"{counts['could_not_run']} | {counts['calibrated']} | "
            f"{row['scoring_eligibility']} | {row['customer_applicability']} | "
            f"{'ready' if row['deployed_runtime_ready'] else 'not ready'} |"
        )
        semantic = row.get("semantic_uniqueness")
        if semantic:
            lines.append(
                f"  - Semantic uniqueness: {semantic['qualified_behavioral_identities']} qualified / "
                f"{semantic['unqualified_source_identities']} unqualified source identities "
                f"({semantic['status']})."
            )
    if projection["aggregate_controls"]:
        lines.extend(["", "## Aggregate controls", ""])
        for control in projection["aggregate_controls"]:
            lines.append(
                f"- `{control['control_id']}`: **{control['aggregate_disposition']}**; "
                f"legacy pooled pass rate {control['pooled_pass_rate']:.1%}, "
                f"authorized-task completion {control['authorized_task_completion_rate']:.1%}, "
                f"forbidden-action resistance {control['forbidden_action_resistance_rate']:.1%}."
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    projection = project(json.loads(args.ledger.read_text()))
    print(render_markdown(projection) if args.format == "markdown" else json.dumps(projection, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
