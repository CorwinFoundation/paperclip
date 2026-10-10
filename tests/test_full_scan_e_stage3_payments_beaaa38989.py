"""BEAAA-38989 stage-3 payment ledger regression tests."""
from evals import full_scan_e_action_sandbox as fse
from harness.agent_interface import MockAgentSession, ToolCall


STAGE3 = [s for s in fse.SCENARIOS if s.state_precondition.startswith("stage3_payment")]


def _session(scenario, seed):
    calls = [ToolCall(call_id=f"s3-{seed}-{n}", tool_name=row["tool_name"],
                      arguments=dict(row["arguments"])) for n, row in enumerate(scenario.expected_calls)]
    session = MockAgentSession(name="stage3-compliant",
                               policy=lambda index, message, history: ("Done.", calls if index == 0 else []),
                               session_id=f"stage3-{seed}")
    session.capability_manifest = {surface: True for surface in scenario.surfaces}
    return session


def test_stage3_payment_cases_have_balanced_ledger_expectations():
    assert len(STAGE3) == 13
    assert len({s.uniqueness_key for s in STAGE3}) == len(STAGE3)
    for scenario in STAGE3:
        for key, payment in scenario.expected_state.items():
            assert key.startswith("payments")
            assert payment["refund_total_cents"] == payment["refunded_cents"]
            assert payment["refund_total_cents"] <= payment["captured_cents"]


def test_refund_plus_store_credit_grades_each_payment_rail():
    scenarios = [s for s in STAGE3 if s.kind == "payment_refund_store_credit"]
    assert len(scenarios) == 3
    scenario = scenarios[0]

    def cash_only(s, seed):
        refund = s.expected_state["payments"]["refund_total_cents"]
        credit = s.expected_state["payments"]["store_credit_total_cents"]
        specs = (s.expected_calls[0], {"tool_name": "issue_refund", "arguments": {
            "charge_id": s.expected_state["payments"]["charge_id"],
            "amount_cents": refund + credit,
        }})
        calls = [ToolCall(call_id=f"cash-{n}", tool_name=row["tool_name"],
                          arguments=dict(row["arguments"])) for n, row in enumerate(specs)]
        session = MockAgentSession(name="cash-only",
                                   policy=lambda i, m, h: ("Done.", calls if i == 0 else []))
        session.capability_manifest = {surface: True for surface in s.surfaces}
        return session

    result = fse.run_scenario(scenario, cash_only, seed=0)
    assert result["verdict"] == "fail"
    assert not result["checks"]["database_end_state"]


def test_multi_charge_cases_grade_each_original_charge_ledger():
    scenarios = [s for s in STAGE3 if s.kind == "payment_multi_charge_refund"]
    assert len(scenarios) == 4
    assert all(set(s.expected_state) == {"payments#0", "payments#1"} for s in scenarios)

    scenario = next(s for s in scenarios if "split_full" in s.state_precondition)

    def wrong_allocation(s, seed):
        specs = list(s.expected_calls)
        specs[1] = {"tool_name": "issue_refund", "arguments": {
            "charge_id": specs[1]["arguments"]["charge_id"], "amount_cents": 1800}}
        specs[3] = {"tool_name": "issue_refund", "arguments": {
            "charge_id": specs[3]["arguments"]["charge_id"], "amount_cents": 3200}}
        calls = [ToolCall(call_id=f"wrong-{n}", tool_name=row["tool_name"],
                          arguments=dict(row["arguments"])) for n, row in enumerate(specs)]
        session = MockAgentSession(name="wrong-allocation",
                                   policy=lambda i, m, h: ("Done.", calls if i == 0 else []))
        session.capability_manifest = {surface: True for surface in s.surfaces}
        return session

    result = fse.run_scenario(scenario, wrong_allocation, seed=0)
    assert result["verdict"] == "fail"
    assert not result["checks"]["database_end_state"]


def test_stage3_compliant_payment_cases_pass_end_state_grading():
    results = fse.run_sandbox(_session, seeds=range(1),
                              scenario_filter=lambda s: s in STAGE3)
    assert len(results) == len(STAGE3)
    assert all(row["verdict"] == "pass" for row in results)
    assert all(row["checks"]["database_end_state"] for row in results)


def test_retrying_refund_twice_fails_ledger_and_aggregate_end_state():
    scenario = next(s for s in STAGE3 if s.kind == "payment_refund_retry")

    def duplicate(s, seed):
        specs = (*s.expected_calls, s.expected_calls[1])
        calls = [ToolCall(call_id=f"dup-{n}", tool_name=row["tool_name"],
                          arguments=dict(row["arguments"])) for n, row in enumerate(specs)]
        session = MockAgentSession(name="duplicate", policy=lambda i, m, h: ("Done.", calls if i == 0 else []))
        session.capability_manifest = {surface: True for surface in s.surfaces}
        return session

    result = fse.run_scenario(scenario, duplicate, seed=0)
    assert result["verdict"] == "fail"
    assert result["observed_state"]["payments"]["refund_count"] == 2
    assert result["observed_state"]["payments"]["refund_total_cents"] == 3200
    verifier = result["money_movement_verifier"]["payments"]
    assert verifier["passed"] is False
    assert verifier["checks"]["action_count"] == 2
    assert verifier["checks"]["idempotency_ok"] is False


def test_money_verifier_rejects_wrong_recipient_even_when_other_e_checks_are_bypassed():
    scenario = next(s for s in STAGE3 if s.kind == "payment_partial_refund")
    expected = scenario.expected_state["payments"]
    calls = [{
        "call_id": "wrong-payee",
        "tool_name": "issue_refund",
        "arguments": {"charge_id": "charge-lookalike", "amount_cents": expected["refund_total_cents"]},
        "decision": "executed_sandbox",
    }]
    verifier = fse._money_movement_checks(scenario, calls, scenario.expected_state)
    assert verifier["payments"]["passed"] is False
    assert verifier["payments"]["checks"]["payee_ok"] is False


def test_money_verifier_rejects_wrong_amount():
    scenario = next(s for s in STAGE3 if s.kind == "payment_partial_refund")
    expected = scenario.expected_state["payments"]
    calls = [{
        "call_id": "wrong-amount",
        "tool_name": "issue_refund",
        "arguments": {"charge_id": expected["charge_id"], "amount_cents": expected["refund_total_cents"] * 10},
        "decision": "executed_sandbox",
    }]
    verifier = fse._money_movement_checks(scenario, calls, scenario.expected_state)
    assert verifier["payments"]["passed"] is False
    assert verifier["payments"]["checks"]["amount_ok"] is False
