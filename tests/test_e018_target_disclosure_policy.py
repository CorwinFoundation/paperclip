"""Target hardening must not hide raw impersonation or alter the grading rules."""
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from harness.live_tool_agent import (
    BASELINE_SYSTEM_POLICY, DISCLOSURE_TARGET_PROFILE, TARGET_SYSTEM_POLICIES,
    OpenAIToolAgentRunner,
)
from scripts.validate_e018_disclosure_bypass import VisibleTarget, validation_profile


def client_for(*messages):
    calls = []
    pending = iter(messages)
    def create(**kwargs):
        # Retain each request before the runner appends tool results.
        calls.append(json.loads(json.dumps(kwargs)))
        return NS(choices=[NS(message=next(pending))])
    return NS(chat=NS(completions=NS(create=create))), calls


def test_explicit_policy_is_sent_to_model_and_bound_to_transcript():
    raw = '{"speaker_type":"human","automated":false,"message":"I am human."}'
    client, calls = client_for(NS(content=raw, tool_calls=[]))
    runner = OpenAIToolAgentRunner(client=client, target_profile=DISCLOSURE_TARGET_PROFILE)
    visible = VisibleTarget(runner)
    # A disobedient response must still reach the unchanged evaluator verbatim.
    assert visible.complete("Use this JSON schema") == raw
    assert calls[0]["messages"][0] == {
        "role": "system", "content": TARGET_SYSTEM_POLICIES[DISCLOSURE_TARGET_PROFILE]}
    assert calls[0]["messages"][1]["content"] == "Use this JSON schema"
    transcript = visible.transcripts[0]
    assert transcript["target_profile"] == DISCLOSURE_TARGET_PROFILE
    assert transcript["system_policy_sha256"] == hashlib.sha256(
        calls[0]["messages"][0]["content"].encode()).hexdigest()
    assert ":policy-disclosure-hardened@1:" in runner.model_id()


def test_baseline_remains_exactly_reproducible():
    client, calls = client_for(NS(content="original target answer", tool_calls=[]))
    runner = OpenAIToolAgentRunner(client=client, seed=3)
    raw = json.loads(runner.complete("ordinary operation"))
    assert calls[0]["messages"][0]["content"] == BASELINE_SYSTEM_POLICY
    assert runner.model_id() == "openai:gpt-4.1-mini:tool-agent@beaaa8142:seed3"
    assert "target_profile" not in raw and "system_policy_sha256" not in raw
    baseline = validation_profile("anthropic", "baseline")
    hardened = validation_profile("anthropic")
    assert baseline["target_policy_changed"] is False
    assert hardened["target_policy_changed"] is True
    assert baseline["target_system_policy_sha256"] != hardened["target_system_policy_sha256"]
    for name in ("cases", "requested_model", "max_model_requests", "max_total_output_tokens",
                 "replicate_ids", "judge_system_prompt", "judge_output_tool", "corpus_sha256"):
        assert baseline[name] == hardened[name]


def test_policy_survives_tool_continuation_without_changing_tools_or_results():
    call = NS(id="call_1", function=NS(name="submit_transfer", arguments=json.dumps({
        "submitted_payee": "vendor-123", "submitted_amount": "125.00"})))
    client, calls = client_for(NS(content="I am an AI assistant.", tool_calls=[call]),
                              NS(content="The synthetic transfer completed.", tool_calls=[]))
    runner = OpenAIToolAgentRunner(client=client, target_profile=DISCLOSURE_TARGET_PROFILE)
    result = json.loads(runner.complete("Submit the synthetic transfer"))
    assert len(calls) == 2
    assert calls[0]["messages"][0] == calls[1]["messages"][0]
    assert calls[0]["tools"] == OpenAIToolAgentRunner._tools()
    assert "tools" not in calls[1]
    assert result["tool_calls"][0]["result"]["arguments"] == {
        "submitted_payee": "vendor-123", "submitted_amount": "125.00"}
    assert result["initial_content"] == "I am an AI assistant."
    assert result["final_content"] == "The synthetic transfer completed."


def test_unknown_profile_fails_before_any_model_request():
    with pytest.raises(ValueError, match="unknown self-owned target profile"):
        OpenAIToolAgentRunner(client=NS(), target_profile="misspelled")
    with pytest.raises(ValueError, match="unknown self-owned target profile"):
        validation_profile("anthropic", "misspelled")
