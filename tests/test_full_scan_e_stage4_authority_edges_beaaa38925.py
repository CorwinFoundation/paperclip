from evals import full_scan_e_action_sandbox as fse
from evals.full_scan_e_catalogue_stage4 import UNSUPPORTED_FAMILIES
from harness.agent_interface import MockAgentSession, ToolCall

STAGE4 = [s for s in fse.SCENARIOS if s.state_precondition.startswith("stage4:")]

def _agent(mode):
    def factory(scenario, seed):
        specs = (() if mode == "refuse" or (mode == "compliant" and scenario.planted_violation)
                 else scenario.violating_calls if scenario.planted_violation else scenario.expected_calls)
        def policy(index, message, history):
            calls = [ToolCall(f"{mode}-{seed}-{n}", r["tool_name"], dict(r["arguments"]))
                     for n, r in enumerate(specs)] if index == 0 else []
            return ("Done" if calls else "Refused", calls)
        session = MockAgentSession(name=mode, policy=policy, session_id=f"{mode}-{seed}")
        session.capability_manifest = {surface: True for surface in scenario.surfaces}
        return session
    return factory

def test_stage4_claims_only_runtime_enforced_cross_tenant_slice():
    assert len(STAGE4) == len({s.uniqueness_key for s in STAGE4}) == 4
    assert len(UNSUPPORTED_FAMILIES) == 5
    fingerprints = [fse.behavioral_fingerprint(s) for s in STAGE4]
    assert None not in fingerprints
    assert len(set(fingerprints)) == 4
    assert fse.behavioral_fingerprint(next(s for s in fse.SCENARIOS if s not in STAGE4)) is None

def test_compliant_controls_and_observed_bindings():
    rows = fse.run_sandbox(_agent("compliant"), seeds=range(1), scenario_filter=lambda s: s in STAGE4)
    assert {r["verdict"] for r in rows} == {"pass"}
    assert all(b["runtime_tenant_id"] == "tenant_a" for r in rows
               for b in r["runtime_bindings_observed"])
    assert len({r["behavioral_fingerprint"] for r in rows}) == 4

def test_bypass_is_blocked_by_runtime_and_fails_oracle():
    rows = fse.run_sandbox(_agent("bypass"), seeds=range(1),
                           scenario_filter=lambda s: s in STAGE4 and s.planted_violation)
    assert {r["verdict"] for r in rows} == {"fail"}
    assert all(r["action_outcomes"]["blocked"] == 1 for r in rows)
    assert all(r["before_state"] == r["observed_state"] for r in rows)
    assert all(r["runtime_bindings_observed"][0]["resource_tenant_id"] == "tenant_b" for r in rows)

def test_refuse_everything_fails_legitimate_utility():
    rows = fse.run_sandbox(_agent("refuse"), seeds=range(1),
                           scenario_filter=lambda s: s in STAGE4 and not s.planted_violation)
    assert {r["verdict"] for r in rows} == {"fail"}
