import copy
from types import SimpleNamespace

from evals import full_scan_e_action_sandbox as fse
from evals.full_scan_e_connection import bind_capabilities, http_session
from harness.agent_interface import MockAgentSession


def test_missing_or_undeclared_surface_is_not_applicable():
    case = next(s for s in fse.SCENARIOS if s.kind == "order_lookup")
    missing = MockAgentSession(name="missing", policy=lambda *_: ("", []))
    envelope = fse.run_scenario(case, lambda *_: missing, seed=0)
    assert envelope["verdict"] == "not_applicable"
    assert envelope["kind"] == case.kind
    assert envelope["planted_violation"] == case.planted_violation
    assert envelope["checks"] == {}
    bind_capabilities(missing, {"payments": True})
    assert fse.run_scenario(case, lambda *_: missing, seed=0)["verdict"] == "not_applicable"


def test_http_adapter_receives_scanner_tools_and_runs_end_to_end():
    case = next(s for s in fse.SCENARIOS if s.kind == "order_lookup")
    order_id = case.expected_state["orders"]["order_id"]
    calls = []

    def poster(url, **kwargs):
        # The HTTP adapter retains and extends its messages list between hops.
        # Snapshot each outbound body so this assertion observes wire state.
        calls.append(copy.deepcopy(kwargs["json"]))
        if len(calls) == 1:
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "lookup-1", "type": "function", "function": {
                    "name": "get_order", "arguments": f'{{"order_id":"{order_id}"}}'}}
            ]}
        else:
            msg = {"role": "assistant", "content": "active"}
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": msg}]})

    tools = ({"type": "function", "function": {"name": "get_order", "parameters": {"type": "object"}}},)
    session = http_session(url="https://agent.invalid/v1/chat", model="customer-agent",
                           capability_manifest={"orders": True}, tools=tools, poster=poster)
    result = fse.run_scenario(case, lambda *_: session, seed=0)
    assert result["verdict"] == "pass"
    assert calls[0]["tools"] == list(tools)
    assert calls[1]["messages"][-1]["role"] == "tool"
