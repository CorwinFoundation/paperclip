#!/usr/bin/env python3
"""Build and dry-run the BEAAA-19288 isolated synthetic capability adapter."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.vapi_synthetic_capability_adapter import (
    ALLOWED_TOOLS, SyntheticCapabilityAdapter, canonical_bytes, digest,
)
from scripts.build_beaaa19283_vapi_capability_manifest import FAMILY_PRIMITIVES

FREEZE_SHA = "b32a1b7ec582ee55fd80e05332448c584a4237acbe792c914e5a8cf0739fc008"
CORPUS_SHA = "fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988"

PRIMITIVE_TOOL = {
    primitive: next(tool for tool in sorted(ALLOWED_TOOLS) if tool in primitive or {
        "synthetic_action": "action", "action_ledger": "action",
        "approval_store": "approval", "authorization_store": "authorization",
        "scheduler_control": "clock", "cancellation_control": "cancel",
        "fixture_store": "state", "regional_fixture_store": "state",
        "synthetic_agent_directory": "handoff", "synthetic_recipient_directory": "recipient",
        "synthetic_secret_store": "state", "persistent_memory_store": "memory",
        "package_registry_fixture": "repo", "diff_ledger": "repo",
        "egress_deny": "sandbox", "fault_injection": "tool",
        "idempotency_store": "transaction", "override_control": "authorization",
        "preview_binding": "approval", "process_ledger": "process",
        "reset_control": "memory", "resource_meter": "budget",
        "signed_handoff": "handoff", "stop_control": "cancel",
        "synthetic_agent_directory": "handoff", "synthetic_browser": "browser",
        "synthetic_calendar": "calendar", "synthetic_mailbox": "mailbox",
        "synthetic_mcp_server": "tool", "synthetic_repo": "repo",
        "synthetic_tool": "tool", "tenant_store": "tenant",
        "tool_result_ledger": "tool", "tool_schema_control": "tool",
        "transaction_store": "transaction",
    }.get(primitive) == tool)
    for primitive in {p for values in FAMILY_PRIMITIVES.values() for p in values}
}


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prior_request_keys(path: Path) -> set[str]:
    payload = json.loads(path.read_text())
    keys = set()
    for record in payload["records"]:
        for response in record.get("responses") or []:
            keys.add(digest([record["stable_case_id"], response["request_index"]]))
    if len(keys) != 453:
        raise SystemExit(f"prior deny ledger expected 453 requests, got {len(keys)}")
    return keys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--prior-ledger", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if file_sha(args.freeze) != FREEZE_SHA:
        raise SystemExit("freeze SHA mismatch")
    rows = [json.loads(line) for line in args.freeze.read_text().splitlines() if line]
    selected = [r for r in rows if r["authorization_class"] == "destructive_tool_cross_session_cross_customer"]
    if len(selected) != 596 or len({r["stable_case_id"] for r in selected}) != 596:
        raise SystemExit("596 unique selected rows required")
    deny = prior_request_keys(args.prior_ledger)

    adapter = SyntheticCapabilityAdapter(seed=42)
    traces = []
    analysis = []
    for row in selected:  # concurrency=1; STOP-on-first-error by uncaught exception
        primitives = FAMILY_PRIMITIVES[row["family_id"]]
        tools = list(dict.fromkeys(PRIMITIVE_TOOL[p] for p in primitives))
        proposed = {digest([row["stable_case_id"], i]) for i in range(1, len(row["prompt_turn_sequence"]) + 1)}
        if proposed & deny:
            raise SystemExit(f"deny/replay ledger match: {row['stable_case_id']}")
        traces.append(adapter.execute(row, tools))
        analysis.append({
            "stable_case_id": row["stable_case_id"],
            "family_id": row["family_id"],
            "existing_assistant_executable": False,
            "adapter_required": True,
            "reason": "Frozen case requires synthetic tool/action/state evidence; existing assistant exposes text chat only.",
        })
    adapter.assert_isolation()
    family_counts = dict(sorted(Counter(r["family_id"] for r in selected).items()))
    ledger = {
        "schema": "backbond.vapi_synthetic_capability_dry_run/v1",
        "issue": "BEAAA-19288",
        "scanner_version": "0.5.0", "corpus_version": CORPUS_SHA,
        "probe_set_version": "1.4", "rubric_point_id": "none - non-score-bearing synthetic harness",
        "contract": "contracts/are-scanner-api-v1.md@v1.3.0",
        "seed": 42, "concurrency": 1, "maximum_requests_per_minute": 30, "retries": 0,
        "network_requests": 0, "cases": 596, "prior_deny_request_count": 453,
        "source_freeze_sha256": FREEZE_SHA, "traces": traces, "events": adapter.events,
    }
    capability = {
        "schema": "backbond.vapi_capability_analysis/v1", "issue": "BEAAA-19288",
        "existing_assistant_executable": 0, "adapter_required": 596,
        "family_counts": family_counts, "cases": analysis,
    }
    selection = {
        "schema": "backbond.vapi_frozen_selection/v1", "issue": "BEAAA-19288",
        "source_sha256": FREEZE_SHA, "count": 596,
        "stable_case_ids": [r["stable_case_id"] for r in selected],
        "selection_sha256": digest([r["stable_case_id"] for r in selected]),
    }
    deny_payload = {"schema": "backbond.vapi_prior_request_deny_ledger/v1", "count": 453,
                    "request_keys": sorted(deny)}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in [
        ("selection-ledger.json", selection), ("capability-analysis.json", capability),
        ("prior-request-deny-ledger.json", deny_payload), ("dry-run-ledger.json", ledger),
    ]:
        (args.output_dir / name).write_bytes(canonical_bytes(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
