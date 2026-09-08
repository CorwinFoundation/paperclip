"""Offline policy adapter for synthetic delegated-authority fixtures."""
from __future__ import annotations

from typing import Any


def execute_offline_fixture(request: dict[str, Any]) -> dict[str, Any]:
    """Evaluate synthetic delegation state and perform mocked effects only."""
    case_id = request.get("case_id")
    control = request.get("control")
    state = request.get("state")
    if not isinstance(case_id, str) or not isinstance(control, str) or not isinstance(state, dict):
        return {"decision": "harness_blocked", "receipt": None, "mock_effects": []}

    checks = {
        "principal_binding": lambda: state.get("downstream_principal") == state.get("original_principal"),
        "scope_intersection": lambda: state.get("action_within_every_delegation_scope") is True,
        "delegation_lifetime": lambda: state.get("delegation_valid_at_execution") is True,
        "tenant_propagation": lambda: state.get("downstream_tenant") == state.get("original_tenant"),
        "purpose_propagation": lambda: state.get("downstream_purpose") == state.get("approved_purpose"),
        "delegate_identity": lambda: state.get("executing_delegate") == state.get("approved_delegate"),
        "delegation_depth": lambda: _integer(state.get("delegation_depth")) <= _integer(state.get("maximum_depth")),
        "approval_identity": lambda: state.get("approver_verified_for_original_principal") is True,
    }
    check = checks.get(control)
    try:
        permitted = check() if check is not None else None
    except TypeError:
        permitted = None
    if permitted is None:
        return {"decision": "harness_blocked", "receipt": None, "mock_effects": []}
    if not permitted:
        return {"decision": "deny", "receipt": None, "mock_effects": []}
    receipt = {"case_id": case_id, "status": "simulated", "result": "sanitized_delegation_action"}
    return {"decision": "permit", "receipt": receipt, "mock_effects": [{"operation": "synthetic_delegated_action", "case_id": case_id}]}


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("integer policy value required")
    return value


def validate_offline_receipt(receipt: Any, case_id: str) -> bool:
    return isinstance(receipt, dict) and receipt == {
        "case_id": case_id,
        "status": "simulated",
        "result": "sanitized_delegation_action",
    }
