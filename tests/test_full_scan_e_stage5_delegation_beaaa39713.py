from evals import full_scan_e_action_sandbox as fse
from harness.agent_interface import MockAgentSession, ToolCall

STAGE5 = [s for s in fse.SCENARIOS if s.state_precondition.startswith("stage5:")]

def _agent(mode):
    def factory(scenario, seed):
        calls = (() if mode == "refuse" else
                 scenario.violating_calls if scenario.planted_violation else scenario.expected_calls)
        def policy(index, message, history):
            emitted = [ToolCall(f"{mode}-{seed}-{n}", c["tool_name"], dict(c["arguments"]))
                       for n, c in enumerate(calls)] if index == 0 else []
            return ("Done" if emitted else "Refused", emitted)
        session = MockAgentSession(name=mode, policy=policy, session_id=f"{mode}-{seed}")
        session.capability_manifest = {surface: True for surface in scenario.surfaces}
        return session
    return factory

def test_eight_distinct_runtime_delegation_scenarios():
    assert len(STAGE5) == 8
    fingerprints = [fse.behavioral_fingerprint(s) for s in STAGE5]
    assert None not in fingerprints
    assert len(set(fingerprints)) == 8

def test_compliant_agent_commits_authorized_and_runtime_blocks_forbidden():
    rows = fse.run_sandbox(_agent("compliant"), seeds=range(1),
                           scenario_filter=lambda s: s in STAGE5)
    assert {r["verdict"] for r in rows} == {"pass", "fail"}
    authorized = [r for r in rows if not r["planted_violation"]]
    forbidden = [r for r in rows if r["planted_violation"]]
    assert all(r["verdict"] == "pass" and r["action_outcomes"]["committed"] == 1
               for r in authorized)
    assert all(r["verdict"] == "fail" and r["action_outcomes"]["blocked"] == 1
               and r["before_state"] == r["observed_state"] for r in forbidden)
    assert all(r["runtime_bindings_observed"][0]["delegation_grant_id"] == "grant_runtime"
               for r in rows)

def test_refuse_everything_fails_authorized_utility():
    rows = fse.run_sandbox(_agent("refuse"), seeds=range(1),
                           scenario_filter=lambda s: s in STAGE5 and not s.planted_violation)
    assert {r["verdict"] for r in rows} == {"fail"}
