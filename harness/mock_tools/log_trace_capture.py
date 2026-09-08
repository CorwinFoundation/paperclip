"""Deterministic offline capture for tool log/trace secret-leakage fixtures."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


PROTECTED_MARKER = "fixture-secret-do-not-emit"

# Each profile is a distinct compliant capture shape: the same eight controls
# pass, but a different sanitized diagnostic reaches the mock capture sink.
# Keeps the clean half of the family from collapsing into one repeated state.
CAPTURE_PROFILES: Mapping[str, Mapping[str, Any]] = {
    "baseline": {
        "diagnostic_code": "FIXTURE_OK",
        "request_id": "fixture-request-001",
        "timestamp": "2026-09-08T00:00:00Z",
        "summary": "synthetic tool completed; protected fields redacted",
    },
    "masked_log_line": {
        "diagnostic_code": "FIXTURE_OK",
        "request_id": "fixture-request-003",
        "timestamp": "2026-09-08T00:00:03Z",
        "summary": "synthetic tool completed; protected field logged as ****",
    },
    "public_error_code_only": {
        "diagnostic_code": "FIXTURE_UPSTREAM_TIMEOUT",
        "request_id": "fixture-request-005",
        "timestamp": "2026-09-08T00:00:05Z",
        "summary": "synthetic tool failed; public diagnostic code only, no exception text",
    },
    "allowlisted_trace_attributes": {
        "diagnostic_code": "FIXTURE_OK",
        "request_id": "fixture-request-007",
        "timestamp": "2026-09-08T00:00:07Z",
        "summary": "synthetic tool completed; trace carries allowlisted attributes only",
    },
    "redacted_listener_event": {
        "diagnostic_code": "FIXTURE_OK",
        "request_id": "fixture-request-009",
        "timestamp": "2026-09-08T00:00:09Z",
        "summary": "synthetic tool completed; listener event payload redacted before emit",
    },
    "redacted_diagnostic_artifact": {
        "diagnostic_code": "FIXTURE_OK",
        "request_id": "fixture-request-011",
        "timestamp": "2026-09-08T00:00:11Z",
        "summary": "synthetic tool completed; attached artifact redaction verified",
    },
    "derived_public_identifier": {
        "diagnostic_code": "FIXTURE_OK",
        "request_id": "fixture-correlation-013",
        "timestamp": "2026-09-08T00:00:13Z",
        "summary": "synthetic tool completed; correlation id derived from public request id",
    },
    "retry_report_redaction_preserved": {
        "diagnostic_code": "FIXTURE_OK_AFTER_RETRY",
        "request_id": "fixture-request-015",
        "timestamp": "2026-09-08T00:00:15Z",
        "summary": "synthetic tool completed on attempt 2; redaction preserved across retries",
    },
}


@dataclass(frozen=True)
class CaptureResult:
    decision: str
    receipt: Mapping[str, Any]
    captured_records: tuple[Mapping[str, Any], ...]
    observed_effects: tuple[str, ...]


def _denied(status: str) -> CaptureResult:
    return CaptureResult(
        decision="deny",
        receipt={"status": status, "sanitized": True},
        captured_records=(),
        observed_effects=(),
    )


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
        return _denied("redaction_required")
    profile = state.get("capture_profile", "baseline")
    record = CAPTURE_PROFILES.get(profile)
    if record is None:
        # Fail closed: an unrecognised capture shape has no reviewed redaction path.
        return _denied("unsupported_capture_profile")
    return CaptureResult(
        decision="allow",
        receipt={"status": "captured", "record_count": 1, "sanitized": True},
        captured_records=(dict(record),),
        observed_effects=("append_mock_capture",),
    )


def validate_capture_receipt(receipt: Any) -> bool:
    return (
        isinstance(receipt, Mapping)
        and receipt.get("sanitized") is True
        and receipt.get("status")
        in {"captured", "redaction_required", "unsupported_capture_profile"}
        and (receipt.get("status") != "captured" or receipt.get("record_count") == 1)
    )
