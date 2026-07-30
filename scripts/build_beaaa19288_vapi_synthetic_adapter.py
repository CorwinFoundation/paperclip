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
# Keep this historical evidence-tree restoration self-contained. The BEAAA-19283
# preflight builder was not one of the ten approved BEAAA-19288 paths and is not
# present on canonical master.
FAMILY_PRIMITIVES = {
    "agent_identity_signed_handoff": ["session_store", "signed_handoff", "synthetic_agent_directory"],
    "approval_freshness_replay": ["approval_store", "clock_control", "action_ledger"],
    "approval_preview_mutation_binding": ["approval_store", "preview_binding", "synthetic_action"],
    "authorization_revocation_race": ["authorization_store", "scheduler_control", "action_ledger"],
    "browser_surface_action_integrity": ["synthetic_browser", "synthetic_action", "action_ledger"],
    "checkpoint_restore_integrity": ["checkpoint_store", "session_store", "clock_control"],
    "code_agent_supply_chain": ["synthetic_repo", "package_registry_fixture", "sandbox"],
    "code_patch_scope_preservation": ["synthetic_repo", "sandbox", "diff_ledger"],
    "concurrent_shared_state_isolation": ["session_store", "scheduler_control", "tenant_store"],
    "consequential_action_confirmation": ["approval_store", "synthetic_action", "action_ledger"],
    "cross_session_data_leak": ["session_store", "tenant_store", "synthetic_secret_store"],
    "data_residency_locality_boundary": ["regional_fixture_store", "egress_deny", "action_ledger"],
    "email_calendar_injection": ["synthetic_mailbox", "synthetic_calendar", "action_ledger"],
    "human_override_takeover_relinquishment": ["override_control", "session_store", "action_ledger"],
    "idempotency_retry_double_execution": ["idempotency_store", "fault_injection", "action_ledger"],
    "long_running_cancellation_race": ["scheduler_control", "cancellation_control", "action_ledger"],
    "mcp_tool_integrity": ["synthetic_mcp_server", "tool_schema_control", "action_ledger"],
    "multi_agent_handoff_integrity": ["synthetic_agent_directory", "signed_handoff", "session_store"],
    "peril_recipient_integrity": ["synthetic_recipient_directory", "synthetic_action", "action_ledger"],
    "peril_tool_grounded_factuality": ["synthetic_tool", "fixture_store", "tool_result_ledger"],
    "persistent_memory_poisoning": ["persistent_memory_store", "session_store", "reset_control"],
    "recipient_identity_integrity": ["synthetic_recipient_directory", "synthetic_action", "action_ledger"],
    "resource_budget_enforcement": ["resource_meter", "budget_control", "cancellation_control"],
    "sandbox_process_network_boundary": ["sandbox", "egress_deny", "process_ledger"],
    "stop_resume_memory_boundary": ["session_store", "stop_control", "reset_control"],
    "tenant_account_boundary": ["tenant_store", "authorization_store", "action_ledger"],
    "tool_authorization_confused_deputy": ["synthetic_tool", "authorization_store", "action_ledger"],
    "tool_schema_drift_partial_failure": ["synthetic_tool", "tool_schema_control", "fault_injection"],
    "transaction_end_state": ["synthetic_action", "transaction_store", "action_ledger"],
}

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
    if payload.get("schema") == "backbond.vapi_prior_request_deny_ledger/v1":
        keys = set(payload["request_keys"])
    else:
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
