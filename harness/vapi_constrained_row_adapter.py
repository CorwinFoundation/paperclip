"""Network-free adapter for the BEAAA-19391 constrained Vapi row lane."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def adapt_turns(prompt_turn_sequence: list[Any]) -> list[dict[str, Any]]:
    """Preserve authored roles/content and structured values without flattening."""
    events: list[dict[str, Any]] = []
    for index, turn in enumerate(prompt_turn_sequence, 1):
        if not isinstance(turn, dict):
            raise ValueError(f"turn {index} is not an object")
        if set(turn) == {"content", "role"}:
            role = turn["role"]
            content = turn["content"]
            if not isinstance(role, str) or not role:
                raise ValueError(f"turn {index} has invalid role")
            event = {
                "sequence": index,
                "kind": "role_bearing_turn",
                "role": role,
                "content": content,
            }
        else:
            event = {
                "sequence": index,
                "kind": "structured_probe_input",
                "role": "probe_fixture",
                "content": turn,
            }
        event["content_sha256"] = digest(event["content"])
        events.append(event)
    return events


def execute(case: dict[str, Any]) -> dict[str, Any]:
    events = adapt_turns(case["prompt_turn_sequence"])
    trace = {
        "stable_case_id": case["stable_case_id"],
        "family_id": case["family_id"],
        "source_row_sha256": case["source_row_sha256"],
        "source_turns_sha256": digest(case["prompt_turn_sequence"]),
        "events": events,
        "status": "adapter_execution_complete",
        "network_requests": 0,
        "retries": 0,
    }
    trace["trace_sha256"] = digest(trace)
    return trace
