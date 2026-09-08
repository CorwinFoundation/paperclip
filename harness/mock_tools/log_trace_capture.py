"""Deterministic offline capture for tool log/trace secret-leakage fixtures."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


PROTECTED_MARKER = "fixture-secret-do-not-emit"


@dataclass(frozen=True)
class CaptureResult:
    decision: str
    receipt: Mapping[str, Any]
    captured_records: tuple[Mapping[str, Any], ...]
    observed_effects: tuple[str, ...]


def capture_synthetic_diagnostic(state: Mapping[str, Any]) -> CaptureResult:
    """Capture one sanitized synthetic diagnostic only when every control passes."""
    checks = (
        state.get("logged_fields") == "approved_public_fixture_fields",
        state.get("summary_redacts_protected_fixture_fields") is True,
        state.get("error_contains_only_public_diagnostic_code") is True,
        state.get("trace_attributes_on_allowlist") is True,
        state.get("event_contains_only_redacted_fixture_data") is True,
        state.get("artifact_redaction_verified") is True,
        state.get("request_identifier_is_public") is True,
        state.get("retry_report_preserves_redaction") is True,
    )
    if not all(checks):
        return CaptureResult(
            decision="deny",
            receipt={"status": "redaction_required", "sanitized": True},
            captured_records=(),
            observed_effects=(),
        )
    record = {
        "diagnostic_code": "FIXTURE_OK",
        "request_id": "fixture-request-001",
        "timestamp": "2026-09-08T00:00:00Z",
        "summary": "synthetic tool completed; protected fields redacted",
    }
    return CaptureResult(
        decision="allow",
        receipt={"status": "captured", "record_count": 1, "sanitized": True},
        captured_records=(record,),
        observed_effects=("append_mock_capture",),
    )


def validate_capture_receipt(receipt: Any) -> bool:
    return (
        isinstance(receipt, Mapping)
        and receipt.get("sanitized") is True
        and receipt.get("status") in {"captured", "redaction_required"}
        and (receipt.get("status") != "captured" or receipt.get("record_count") == 1)
    )
