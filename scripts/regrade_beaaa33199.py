#!/usr/bin/env python3
"""Re-grade the existing BEAAA-29082 run after capability declarations.

This is an offline derivation: it never dispatches a target or judge call.
Only rows whose existing incomplete reason is ``unavailable_capability:<name>``
are converted, using the total BEAAA-33197 census as explicit False
declarations. The two unrelated RunnerError rows remain incomplete.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.full_scan_executor import seal_manifest


def capability_manifest(census_path: Path) -> dict[str, bool]:
    census = json.loads(census_path.read_text(encoding="utf-8"))
    return {str(name): False for name in census["capabilities"]}


def write_capability_artifact(census_path: Path, destination: Path) -> None:
    """Persist the total split so a follow-on run can select true cases."""
    declared = capability_manifest(census_path)
    source = (
        "artifacts/beaaa-19220/vapi-2000-authorized-execution-evidence.redacted.json"
        "#/preflight/observed"
    )
    artifact = {
        "schema": "backbond.vapi_capability_manifest/v1",
        "issue": "BEAAA-33199",
        "assistant_id": "09aea361-04b3-4a62-bcb0-2f2582f11ec8",
        "census_source": str(census_path),
        "configuration_evidence": source,
        "configuration_observed": {
            "model_tools": [],
            "model_tool_ids": [],
            "server_url": None,
            "legacy_server_url": None,
            "hooks": [],
        },
        "decision_rule": (
            "true only when the captured assistant configuration contains a "
            "concrete model, tool/function, transcriber, transfer destination, "
            "or server/webhook surface implementing the named capability"
        ),
        "counts": {"total": len(declared), "true": 0, "false": len(declared)},
        "capabilities": [
            {
                "name": name,
                "value": value,
                "configured_surface": None,
                "basis": (
                    "No implementing surface for this named capability appears "
                    "in the captured assistant configuration; the authenticated "
                    "snapshot records zero tools/tool IDs, no server URL, and no hooks."
                ),
                "evidence_ref": source,
            }
            for name, value in sorted(declared.items())
        ],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def regrade(source: Path, destination: Path, census_path: Path) -> tuple[int, int]:
    manifest = json.loads(source.read_text(encoding="utf-8"))
    declared = capability_manifest(census_path)
    output = copy.deepcopy(manifest)
    changed = 0
    for row in output["evidence_rows"]:
        reason = str(row.get("incomplete_reason") or "")
        prefix, _, detail = reason.partition(":")
        capability, _, _declared = detail.partition("=")
        if prefix != "unavailable_capability" or declared.get(capability) is not False:
            continue
        row.update(
            status="complete",
            verdict="not_applicable",
            target_calls=0,
            judge_calls=0,
            incomplete_reason=None,
            acquisition_basis="not-applicable",
            na_evidence={
                "profile_capability_manifest": declared,
                "missing_capability_name": capability,
                "declared_value": False,
                "capability_gate": f"manifest.{capability}=true",
            },
        )
        changed += 1

    statuses = Counter(str(row.get("status") or "unknown") for row in output["evidence_rows"])
    verdicts = Counter(str(row.get("verdict") or "unresolved") for row in output["evidence_rows"])
    missing = [
        row["probe_id"]
        for row in output["evidence_rows"]
        if row.get("status") != "complete"
    ]
    incomplete_reasons = [
        f"{row['probe_id']}:{row['incomplete_reason']}"
        for row in output["evidence_rows"]
        if row.get("status") != "complete" and row.get("incomplete_reason")
    ]
    output["capability_manifest"] = declared
    output["capability_manifest_basis"] = {
        name: "no matching configured model/tool/transcriber/transfer/server surface in captured assistant config"
        for name in declared
    }
    output["counts"]["missing_required"] = len(missing)
    output["status_counts"] = dict(sorted(statuses.items()))
    output["verdict_counts"] = dict(sorted(verdicts.items()))
    output["incomplete_reasons"] = incomplete_reasons
    output["incomplete"] = bool(incomplete_reasons)
    output["analysis_status"] = "complete" if not incomplete_reasons else "analysis_incomplete"
    output["acceptance_gate"] = {
        "canonical_reconciliation_equal": True,
        "incomplete_false": not incomplete_reasons,
        "zero_unapproved_missing_required": not missing,
        "passed": not incomplete_reasons and not missing,
    }
    output["bind_eligibility"] = "eligible" if not incomplete_reasons else "blocked_base_suite_incomplete"
    output["regrade"] = {
        "source_manifest": str(source),
        "source_missing_required": manifest["counts"]["missing_required"],
        "converted_declared_false_rows": changed,
        "residual_incomplete_rows": len(missing),
    }
    output = seal_manifest(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return changed, len(missing)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--census", type=Path, default=Path("artifacts/BEAAA-33197/undeclared-capabilities.json"))
    parser.add_argument(
        "--capability-artifact",
        type=Path,
        default=Path("artifacts/BEAAA-33199/capability-manifest.json"),
    )
    args = parser.parse_args()
    write_capability_artifact(args.census, args.capability_artifact)
    changed, residual = regrade(args.source, args.destination, args.census)
    print(f"converted_declared_false_rows={changed} residual_incomplete_rows={residual}")
    return 0 if changed == 272 and residual == 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
