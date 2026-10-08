from evals import full_scan_e_action_sandbox as fse
from harness.agent_interface import MockAgentSession
from scripts import beaaa38922_conv_live_run as conv


def test_conversation_could_not_run_inputs_are_never_verdicts():
    assert conv.could_not_run_reason("hello", "") == "empty_reply"
    assert conv.could_not_run_reason("hello there", "hello there") == "prompt_echo_only"
    assert conv.could_not_run_reason("hello", TimeoutError("late")) == "transport_error:TimeoutError"
    assert conv.could_not_run_reason("hello", "A substantive answer") is None


def test_conversation_k3_reduction_preserves_raw_verdicts():
    rows = [{"case_id": "A", "family": "f", "seed": seed, "verdict": verdict}
            for seed, verdict in zip((42, 43, 44), ("pass", "fail", "pass"))]
    result = conv.aggregate_case_rows(rows)[0]
    assert result == {"case_id": "A", "family": "f", "verdict": "flaky",
                      "raw_verdicts": ["pass", "fail", "pass"], "seeds": [42, 43, 44]}


def test_conversation_k3_could_not_run_is_not_pass_or_fail():
    rows = [{"case_id": "A", "family": "f", "seed": seed, "verdict": verdict}
            for seed, verdict in zip((42, 43, 44), ("pass", "could_not_run", "pass"))]
    assert conv.aggregate_case_rows(rows)[0]["verdict"] == "could_not_run"


def test_action_sandbox_empty_and_echo_only_are_could_not_run():
    case = next(s for s in fse.SCENARIOS if s.kind == "order_lookup")

    def run(text):
        session = MockAgentSession(name="silent", policy=lambda *_: (text, []))
        session.capability_manifest = {"orders": True}
        return fse.run_scenario(case, lambda *_: session, seed=0)

    empty = run("")
    echoed = run("\n".join(case.user_turns))
    assert (empty["verdict"], empty["reason"]) == ("could_not_run", "empty_reply")
    assert (echoed["verdict"], echoed["reason"]) == ("could_not_run", "prompt_echo_only")
    result = fse.summary([empty, echoed])
    assert result["eligible"] == result["passed"] == result["failed"] == 0
    assert result["could_not_run"] == 2
