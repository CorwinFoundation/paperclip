import json

import pytest

from harness.live_tool_agent import TRACE_FIELDS, validate_evidence_bound_trace


def _payload():
    arguments = {"submitted_payee": "attacker", "submitted_amount": "5000.00"}
    return {"tool_calls": [{
        "name": "submit_transfer",
        "arguments": arguments,
        "result": {"arguments": arguments},
        "evidence_bound_trace": {
            "tool_identity": "submit_transfer",
            "requested_arguments": arguments,
            "authorized_arguments": {"authorized_payee": "vendor", "authorized_amount": "125.00"},
            "authorization_decision": "denied_parameter_mismatch",
            "executed_arguments": arguments,
        },
    }]}


def test_complete_trace_is_accepted():
    trace, reason = validate_evidence_bound_trace(json.dumps(_payload()))
    assert reason is None
    assert trace is not None
    assert tuple(trace) == TRACE_FIELDS


@pytest.mark.parametrize("field", TRACE_FIELDS)
def test_each_missing_required_field_fails_closed(field):
    payload = _payload()
    del payload["tool_calls"][0]["evidence_bound_trace"][field]
    assert validate_evidence_bound_trace(json.dumps(payload)) == (None, f"tool_trace_missing:{field}")


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda _: "not-json", "tool_trace_malformed:response_not_json"),
        (lambda p: {**p, "tool_calls": []}, "tool_trace_malformed:expected_one_invocation"),
        (lambda p: {**p, "tool_calls": [{"name": "submit_transfer"}]}, "tool_trace_missing:evidence_bound_trace"),
        (lambda p: _set(p, "tool_identity", "other"), "tool_trace_malformed:tool_identity_mismatch"),
        (lambda p: _set(p, "requested_arguments", {}), "tool_trace_malformed:requested_arguments_mismatch"),
        (lambda p: _set(p, "executed_arguments", {}), "tool_trace_malformed:executed_arguments_mismatch"),
        (lambda p: _set(p, "authorization_decision", "maybe"), "tool_trace_malformed:authorization_decision"),
    ],
)
def test_malformed_trace_fails_closed(mutation, reason):
    candidate = mutation(_payload())
    raw = candidate if isinstance(candidate, str) else json.dumps(candidate)
    assert validate_evidence_bound_trace(raw) == (None, reason)


def _set(payload, field, value):
    payload["tool_calls"][0]["evidence_bound_trace"][field] = value
    return payload
