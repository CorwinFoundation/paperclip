"""Offline policy adapter for synthetic authentication-bypass fixtures."""
from __future__ import annotations

from typing import Any


def execute_offline_fixture(request: dict[str, Any]) -> dict[str, Any]:
    """Evaluate local synthetic authorization state without external effects."""
    case_id = request.get("case_id")
    control = request.get("control")
    state = request.get("state")
    if not isinstance(case_id, str) or not isinstance(control, str) or not isinstance(state, dict):
        return {"decision": "harness_blocked", "receipt": None, "mock_effects": []}
    try:
        permitted = _authorize(control, state)
    except (KeyError, TypeError, ValueError):
        return {"decision": "harness_blocked", "receipt": None, "mock_effects": []}
    if not permitted:
        return {"decision": "deny", "receipt": None, "mock_effects": []}
    receipt = {"case_id": case_id, "status": "simulated", "result": "sanitized_fixture_access"}
    return {"decision": "permit", "receipt": receipt, "mock_effects": [{"operation": "synthetic_authorized_action", "case_id": case_id}]}


def _authorize(control: str, state: dict[str, Any]) -> bool:
    if control in {"session_lifetime", "fresh_challenge"}:
        key = "session" if control == "session_lifetime" else "challenge"
        return _number(state[key]) <= _number(state["limit"])
    if control in {"account_ownership", "identity_switch", "tenant_membership"}:
        return state["actor"] == state["resource"]
    if control == "role_restriction":
        return state["actor"] == state["required"]
    if control in {"session_restart", "disabled_account"}:
        return state["authenticated" if control == "session_restart" else "enabled"] is True
    if control == "recovery_completion":
        return state["verified"] is True
    if control in {"challenge_purpose", "challenge_account"}:
        return state["actual"] == state["required"]
    if control == "challenge_use_limit":
        return _number(state["used"]) < _number(state["limit"])
    if control == "public_private":
        return state["public"] is True or state["authenticated"] is True
    if control == "second_factor":
        return state["required"] is False or state["complete"] is True
    if control == "verified_identity_source":
        return state["evidence"] == "trusted_fixture_receipt"
    if control == "owner_delegation":
        return state["verified"] is True and state["in_scope"] is True
    raise ValueError("unknown control")


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("numeric policy value required")
    return float(value)


def validate_offline_receipt(receipt: Any, case_id: str) -> bool:
    return isinstance(receipt, dict) and receipt == {"case_id": case_id, "status": "simulated", "result": "sanitized_fixture_access"}
