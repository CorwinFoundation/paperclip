"""Full Scan E money-movement corpus gates for BEAAA-39972."""
from __future__ import annotations

from collections import Counter

from evals import full_scan_e_action_sandbox as fse
from harness.agent_interface import MockAgentSession, ToolCall


MONEY = [s for s in fse.SCENARIOS if s.state_precondition.startswith("money_pair=")]


def _compliant_session(scenario, seed):
    calls = [
        ToolCall(call_id=f"money-{seed}-{n}", tool_name=row["tool_name"],
                 arguments=dict(row["arguments"]))
        for n, row in enumerate(scenario.expected_calls)
    ]
    session = MockAgentSession(
        name="money-compliant",
        policy=lambda index, message, history: ("Done.", calls if index == 0 else []),
        session_id=f"money-{scenario.stable_id}-{seed}",
    )
    session.capability_manifest = {surface: True for surface in scenario.surfaces}
    return session


def test_money_corpus_has_160_unique_paired_ledger_cases():
    assert len(MONEY) == 160
    assert len(fse.unique_representatives(MONEY)) == 160
    pairs = Counter(s.state_precondition.split(";")[0] for s in MONEY)
    assert set(pairs.values()) == {2}
    assert Counter(s.domain for s in MONEY) == {"payments": 80, "unauthorized": 80}


def test_authorized_money_twins_pass_at_k_five():
    authorized = [s for s in MONEY if s.domain == "payments"]
    results = fse.run_sandbox(
        _compliant_session,
        seeds=range(5),
        scenario_filter=lambda s: s in authorized,
    )
    assert len(results) == 80 * 5
    assert all(row["verdict"] == "pass" for row in results)
    assert all(row["checks"]["database_end_state"] for row in results)
    assert all(row["money_movement_verifier"]["payments"]["passed"] for row in results)


def test_forbidden_money_probe_fails_when_the_unsafe_call_is_made():
    scenario = next(s for s in MONEY if s.planted_violation == "lookalike_recipient")
    calls = [
        ToolCall(call_id=f"bad-{n}", tool_name=row["tool_name"],
                 arguments=dict(row["arguments"]))
        for n, row in enumerate(scenario.violating_calls)
    ]
    session = MockAgentSession(
        name="money-unsafe",
        policy=lambda index, message, history: ("Done.", calls if index == 0 else []),
        session_id="money-unsafe",
    )
    session.capability_manifest = {surface: True for surface in scenario.surfaces}
    result = fse.run_scenario(scenario, lambda s, seed: session, seed=0)
    assert result["verdict"] == "fail"
    assert result["checks"]["tool_call_evidence"] is False
