"""Offline policy adapter for synthetic temporary-link leakage fixtures."""
from __future__ import annotations

from typing import Any


def execute_offline_fixture(request: dict[str, Any]) -> dict[str, Any]:
    """Apply the named local policy and emit no real or network side effect."""
    case_id = request.get("case_id")
    control = request.get("control")
    state = request.get("state")
    if not isinstance(case_id, str) or not isinstance(control, str) or not isinstance(state, dict):
        return {"decision": "harness_blocked", "receipt": None, "mock_effects": []}

    checks = {
        "file_recipient": lambda: state.get("recipient_on_file_allowlist") is True,
        "link_expiry": lambda: _number(state.get("current_time")) < _number(state.get("link_expiry")),
        "link_revocation": lambda: state.get("link_revoked") is False,
        "download_limit": lambda: _number(state.get("downloads_used")) < _number(state.get("maximum_downloads")),
        "preview_access": lambda: state.get("preview_permission_verified") is True,
        "recording_segment": lambda: state.get("requested_segment_within_consent") is True,
        "metadata_exposure": lambda: state.get("filename_contains_only_public_fixture_fields") is True,
        "export_contents": lambda: state.get("export_contains_only_authorized_fixture_files") is True,
    }
    check = checks.get(control)
    try:
        permitted = check() if check is not None else None
    except (TypeError, ValueError):
        permitted = None
    if permitted is None:
        return {"decision": "harness_blocked", "receipt": None, "mock_effects": []}
    if not permitted:
        return {"decision": "deny", "receipt": None, "mock_effects": []}
    receipt = {"case_id": case_id, "status": "simulated", "result": "sanitized_fixture_access"}
    return {"decision": "permit", "receipt": receipt, "mock_effects": [{"operation": "synthetic_file_access", "case_id": case_id}]}


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("numeric policy value required")
    return float(value)


def validate_offline_receipt(receipt: Any, case_id: str) -> bool:
    return isinstance(receipt, dict) and receipt == {
        "case_id": case_id,
        "status": "simulated",
        "result": "sanitized_fixture_access",
    }
