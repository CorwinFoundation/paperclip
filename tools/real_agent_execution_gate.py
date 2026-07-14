"""BEAAA-14087 real-agent executed-count gate.

The gate counts only raw request/response evidence from an approved authenticated
real agent. Manifest labels and local harness assertions are not sufficient.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


DEFAULT_APPROVED_REAL_AGENT_TARGETS = frozenset(
    {
        "approved-demo-agent-v1",
        "backbond-approved-demo-agent-v1",
        "customer-approved-agent-v1",
    }
)

DISALLOWED_TEXT_MARKERS = (
    "127.0.0.1",
    "localhost",
    "mock:",
    " mock ",
    "echo",
    "replay",
    "deterministic fixture",
    "deterministic_local",
    "fixture_version",
    "generated local_target_response",
    "local_target_response",
    "prewritten response",
    "terminal-only",
    "terminal response captured",
    "non-empty",
    "non_empty",
    "assert response_received",
    "captured_response",
)


@dataclass(frozen=True)
class RealAgentGateResult:
    countable: bool
    reasons: tuple[str, ...]


def evaluate_real_agent_execution(
    row: Mapping[str, Any],
    approved_targets: Iterable[str] = DEFAULT_APPROVED_REAL_AGENT_TARGETS,
) -> RealAgentGateResult:
    """Return whether a row may increment executed_agent_test_count."""
    approved = {str(target) for target in approved_targets}
    reasons: list[str] = []

    target_type = _first_present(row, "target_type", "raw_target_type")
    if target_type != "real_agent":
        reasons.append("target_type_not_real_agent")

    target_identity = _first_present(row, "target_identity", "raw_target_identity")
    if target_identity not in approved:
        reasons.append("target_identity_not_allowlisted")

    raw_request = _mapping(row.get("raw_request"))
    raw_response = _mapping(row.get("raw_response"))
    if not raw_request:
        reasons.append("missing_raw_request")
    if not raw_response:
        reasons.append("missing_raw_response")

    request_identity = _first_present(raw_request, "target_identity", "agent_id")
    response_identity = _first_present(raw_response, "target_identity", "agent_id")
    if request_identity != target_identity or response_identity != target_identity:
        reasons.append("raw_identity_mismatch")

    if not _first_present(raw_request, "authorization_sha256", "auth_proof_sha256", "bearer_token_sha256"):
        reasons.append("missing_authenticated_request_proof")

    haystack = _serialize_for_marker_scan(row)
    for marker in DISALLOWED_TEXT_MARKERS:
        if marker in haystack:
            reasons.append(f"disallowed_class:{marker.strip()}")

    return RealAgentGateResult(countable=not reasons, reasons=tuple(dict.fromkeys(reasons)))


def quarantine_reason(row: Mapping[str, Any]) -> str:
    result = evaluate_real_agent_execution(row)
    if result.countable:
        return ""
    return ";".join(result.reasons)


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _first_present(row: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


def _serialize_for_marker_scan(row: Mapping[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, default=str).lower()
