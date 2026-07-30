#!/usr/bin/env python3
"""Build the immutable BEAAA-19283 capability/preflight manifest without traffic."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_FREEZE_SHA = "b32a1b7ec582ee55fd80e05332448c584a4237acbe792c914e5a8cf0739fc008"
EXPECTED_CORPUS_SHA = "fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988"

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


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sha256(args.freeze) != EXPECTED_FREEZE_SHA:
        raise SystemExit("freeze SHA mismatch")
    rows = [json.loads(line) for line in args.freeze.read_text().splitlines() if line]
    selected = [r for r in rows if r["authorization_class"] == "destructive_tool_cross_session_cross_customer"]
    if len(selected) != 596 or len({r["stable_case_id"] for r in selected}) != 596:
        raise SystemExit("596-row selection invariant failed")
    missing = sorted({r["family_id"] for r in selected} - FAMILY_PRIMITIVES.keys())
    if missing:
        raise SystemExit(f"unmapped families: {missing}")
    records = [{
        "ordinal": r["ordinal"],
        "stable_case_id": r["stable_case_id"],
        "family_id": r["family_id"],
        "source_row_sha256": r["source_row_sha256"],
        "prompt_action_reference": r["prompt_action_reference"],
        "required_primitives": FAMILY_PRIMITIVES[r["family_id"]],
        "capability_disposition": "pending_exact_vapi_target_preflight",
        "physical_impossibility": None,
        "execution_eligible": False,
        "execution_reason": "No exact isolated Vapi assistant identity or scoped credential is available in this runtime.",
    } for r in selected]
    payload = {
        "schema": "backbond.vapi_isolated_target_capability_manifest/v1",
        "issue": "BEAAA-19283",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scanner_version": "0.5.0",
        "corpus_version": EXPECTED_CORPUS_SHA,
        "probe_set_version": "1.4",
        "rubric_point_id": "none - target capability provisioning only",
        "seed": 42,
        "classification": "rule-execution",
        "contract": "contracts/are-scanner-api-v1.md@v1.3.0",
        "source_freeze": {"path": str(args.freeze), "sha256": EXPECTED_FREEZE_SHA},
        "target": {
            "provider": "Vapi",
            "environment": "isolated_non_production_synthetic",
            "assistant_id": None,
            "organization_id": None,
            "credential_ref": None,
            "customer_or_production_access": False,
            "preflight_passed": False,
        },
        "traffic_envelope": {"live_probes_before_preflight": 0, "concurrency": 1, "max_requests_per_minute": 20, "retries": 0},
        "stop_controls": [
            "STOP before send on assistant/org mismatch",
            "STOP before send on any non-synthetic tool/server/hook",
            "STOP on first capability or transport mismatch",
            "STOP on any customer/production identifier",
            "STOP before any spend without written approval",
        ],
        "evidence_fields": ["stable_case_id", "source_row_sha256", "assistant_id", "organization_id", "request_sha256", "response_sha256", "tool_trace_sha256", "action_ledger_ref", "started_at", "completed_at", "status"],
        "rollback": "Disable the isolated assistant and revoke only its scoped credential; synthetic fixture stores are discarded.",
        "counts": {"selected_rows": 596, "unique_stable_case_ids": 596, "execution_eligible_rows": 0, "physical_impossibilities": 0},
        "records": records,
    }
    payload["manifest_sha256"] = canonical_sha(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
