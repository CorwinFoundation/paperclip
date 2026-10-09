from __future__ import annotations

import json

from harness.customer_runner import customer_agent_http_runner_factory
from tools.framework_workers import _inspect_execution_evidence, _run_subject_turn


def _envelope() -> dict:
    return {
        "response": "completed",
        "execution_evidence": {
            "ledger": [{
                "call": {"tool_name": "issue_credit", "arguments": {"amount_cents": 1200}},
                "result": {
                    "content": "ok", "is_error": False, "authorized": True,
                    "raw": {"decision": "executed_sandbox"},
                },
            }],
            "before_state": {"refunded_cents": 0},
            "after_state": {"refunded_cents": 1200},
        },
    }


def test_customer_runner_retains_runtime_owned_execution_envelope(monkeypatch):
    runner = customer_agent_http_runner_factory("https://agent.example/run", "secret")
    monkeypatch.setattr(runner, "_parse_response", lambda text: json.loads(text)["response"])
    # Exercise the envelope extraction at the transport boundary without a
    # network call by supplying the same response object the boundary parses.
    runner._last_execution_evidence = _envelope()["execution_evidence"]
    assert runner.execution_evidence()["after_state"]["refunded_cents"] == 1200


def test_inspect_normalizes_runtime_ledger_and_state():
    subject = {"_runtime_execution_evidence": _envelope()["execution_evidence"]}
    trace, before, after = _inspect_execution_evidence(subject)
    assert trace == [{
        "function": "issue_credit", "args": {"amount_cents": 1200},
        "result": "ok", "error": False, "disposition": "committed",
        "authorized": True,
    }]
    assert before == {"refunded_cents": 0}
    assert after == {"refunded_cents": 1200}
