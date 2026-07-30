"""Assessment-local, network-free capability adapter for BEAAA-19288.

This module deliberately has no transport client.  It turns a frozen case into
an append-only synthetic trace using namespaced in-memory state only.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


ALLOWED_TOOLS = frozenset({
    "action", "approval", "authorization", "browser", "budget", "calendar",
    "cancel", "checkpoint", "clock", "handoff", "mailbox", "memory", "process",
    "recipient", "repo", "sandbox", "session", "state", "tenant", "tool",
    "transaction",
})
MAX_CASES = 596
MAX_EVENTS_PER_CASE = 8


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass
class SyntheticCapabilityAdapter:
    seed: int = 42
    events: list[dict[str, Any]] = field(default_factory=list)
    state: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    stopped: bool = False

    def _emit(self, case_id: str, kind: str, **data: Any) -> None:
        if self.stopped:
            raise RuntimeError("adapter stopped")
        if len([e for e in self.events if e["stable_case_id"] == case_id]) >= MAX_EVENTS_PER_CASE:
            self.stopped = True
            raise RuntimeError("per-case event budget exceeded")
        self.events.append({
            "sequence": len(self.events) + 1,
            "logical_time": f"T+{len(self.events):06d}",
            "stable_case_id": case_id,
            "kind": kind,
            **data,
        })

    def execute(self, case: dict[str, Any], required_tools: list[str]) -> dict[str, Any]:
        if self.stopped:
            raise RuntimeError("adapter stopped")
        if len({e["stable_case_id"] for e in self.events}) >= MAX_CASES:
            raise RuntimeError("case budget exceeded")
        unknown = sorted(set(required_tools) - ALLOWED_TOOLS)
        if unknown:
            self.stopped = True
            raise RuntimeError(f"tool not allowlisted: {unknown}")

        case_id = case["stable_case_id"]
        tenant = f"tenant-{digest([self.seed, case_id])[:12]}"
        session = f"session-{digest([case_id, self.seed])[:12]}"
        namespace = (tenant, session)
        self.state[namespace] = {"approval": "pending", "revoked": False, "cancelled": False}
        prompt_turn_sequence = case["prompt_turn_sequence"]
        self._emit(case_id, "case_started", tenant=tenant, session=session,
                   source_row_sha256=case["source_row_sha256"])
        for index, turn in enumerate(prompt_turn_sequence, 1):
            self._emit(case_id, "prompt_observed", turn=index, prompt=turn)
        for tool in required_tools:
            self._emit(case_id, "synthetic_operation", tool=tool, namespace=f"{tenant}/{session}",
                       idempotency_key=digest([case_id, tool])[:24], result="synthetic_only")
        trace = {
            "stable_case_id": case_id,
            "family_id": case["family_id"],
            "source_row_sha256": case["source_row_sha256"],
            "prompt_turn_sequence": prompt_turn_sequence,
            "tenant": tenant,
            "session": session,
            "required_tools": required_tools,
            "status": "dry_run_complete",
            "network_requests": 0,
            "retries": 0,
        }
        trace["trace_sha256"] = digest(trace)
        return trace

    def assert_isolation(self) -> None:
        if len(self.state) != len(set(self.state)):
            raise AssertionError("tenant/session namespace collision")

