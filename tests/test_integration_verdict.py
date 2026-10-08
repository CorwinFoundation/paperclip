import pytest

from harness.integration_verdict import IntegrationVerdictError, build_integration_verdict


def attempt(**overrides):
    row = {
        "scenario_id": "control-1", "attempt_id": "try-1", "engine": "promptfoo",
        "engine_version": "0.118.0", "target_version": "fixture-v1",
        "identity": {"session_id": "session-1", "caller_id": "scanner"},
        "payload": {"prompt": "do the task"}, "oracle": {"type": "exact", "expected": "done"},
        "seed": 7, "actual_turns": 1, "tool_calls": [], "before_state": {}, "after_state": {},
        "elapsed_ms": 12, "target_calls": 1, "judge_calls": 0, "cost_usd": 0,
        "outcome": "PASS", "reason": "exact oracle matched", "oracle_evaluated": True,
        "evidence_refs": ["evidence://attempts/try-1"], "evidence_origin": "reference_agent",
        "replay_command": "python -m runner --attempt try-1",
    }
    row.update(overrides)
    return row


def test_builds_valid_pass_with_evaluated_oracle_and_evidence():
    result = build_integration_verdict(attempt())
    assert result["schema_version"] == "integration_verdict_v1"
    assert len(result["payload_sha256"]) == 64
    assert result["outcome"] == "PASS"


@pytest.mark.parametrize("transport_fact", [
    {"http_status": 200}, {"response_text": "nonempty"}, {"exit_code": 0},
])
def test_transport_success_alone_cannot_become_pass(transport_fact):
    with pytest.raises(IntegrationVerdictError, match="evaluated oracle"):
        build_integration_verdict(attempt(oracle_evaluated=False, **transport_fact))


def test_pass_without_evidence_ref_is_rejected():
    with pytest.raises(IntegrationVerdictError, match="evidence ref"):
        build_integration_verdict(attempt(evidence_refs=[]))


def test_unsupported_is_preserved_without_pass_prerequisites():
    result = build_integration_verdict(attempt(
        outcome="UNSUPPORTED", reason="target lacks tool capability",
        oracle_evaluated=False, evidence_refs=[],
    ))
    assert result["outcome"] == "UNSUPPORTED"
