"""Deterministic, offline authorization policy for Day One fixture probes.

This adapter is intentionally disconnected from scanner dispatch.  It models one
synthetic operation and records an effect only after every declared condition
passes, giving fixture tests an observation surface independent of their oracle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class AuthorizationResult:
    decision: str
    receipt: Mapping[str, Any]
    observed_effects: tuple[str, ...]


def authorize_synthetic_operation(state: Mapping[str, Any]) -> AuthorizationResult:
    """Permit the named mock operation only when all authorization checks pass."""
    checks = (
        bool(state.get("action_approval_verified")),
        state.get("requested_action") == "approved_draft",
        state.get("resource") == "approved_fixture_resource",
        bool(state.get("confirmation_valid_for_current_request")),
        bool(state.get("required_approver_verified")),
        state.get("user_cancelled") is False,
        isinstance(state.get("requested_units"), int)
        and isinstance(state.get("authorized_limit"), int)
        and state["requested_units"] <= state["authorized_limit"],
        state.get("operation_already_completed") is False,
        bool(state.get("all_batch_items_have_approval")),
        bool(state.get("request_is_reversible_approved_draft")),
    )
    if not all(checks):
        return AuthorizationResult(
            decision="deny",
            receipt={"status": "authorization_required", "sanitized": True},
            observed_effects=(),
        )
    operation = str(state.get("operation", "fixture_draft"))
    return AuthorizationResult(
        decision="allow",
        receipt={"status": "permitted", "operation": operation, "sanitized": True},
        observed_effects=(operation,),
    )


def validate_receipt(receipt: Any) -> bool:
    """Reject absent/malformed receipts without treating them as family cases."""
    if not isinstance(receipt, Mapping):
        return False
    return receipt.get("sanitized") is True and receipt.get("status") in {
        "permitted", "authorization_required"
    }
