import pytest

from artifacts.beaaa_15510_import import contract


@pytest.mark.parametrize("raw", [
    "PASS because safe", '```json\n{"verdict":"PASS","rationale":"ok"}\n```',
    '{"verdict":"PASS","rationale":"ok"} trailing', '{"verdict":"PASS",',
    '{"verdict":"PASS","verdict":"FAIL","rationale":"ok"}',
    '{"verdict":"UNKNOWN","rationale":"ok"}', '{"verdict":"PASS"}',
    '{"verdict":"PASS","rationale":"ok","score":1}',
])
def test_malformed_output_is_incomplete_not_a_verdict(raw):
    result = contract.run_judge_vote(lambda _seed: raw, seed=0, max_attempts=1)
    assert result.status == "incomplete"
    assert result.verdict is None
    assert result.attempts[0]["error_class"]


@pytest.mark.parametrize("verdict", contract.VERDICTS)
def test_canonical_verdicts_retain_semantics(verdict):
    raw = '{"verdict":"%s","rationale":"bounded reason"}' % verdict
    result = contract.run_judge_vote(lambda _seed: raw, seed=3)
    assert (result.status, result.verdict, result.rationale) == ("complete", verdict, "bounded reason")
    assert len(result.attempts) == 1


def test_provider_error_retries_then_remains_incomplete():
    calls = []
    def refuse(seed):
        calls.append(seed)
        raise RuntimeError("structured-output refusal")
    result = contract.run_judge_vote(refuse, seed=4)
    assert calls == [4, 4]
    assert result.status == "incomplete"
    assert result.verdict is None
    assert [a["status"] for a in result.attempts] == ["provider_error", "provider_error"]


def test_anthropic_request_uses_strict_schema():
    config = contract.anthropic_output_config()
    schema = config["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["verdict", "rationale"]
