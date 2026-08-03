from __future__ import annotations

import subprocess

from evals.provisional_tau_support_crm import SCENARIOS, run_pilot
from harness.agent_interface import MockAgentSession, ToolCall


def _call(scenario, suffix, tool_name, arguments, action_class):
    return ToolCall(
        call_id=f"{scenario.provisional_id}-{suffix}",
        tool_name=tool_name,
        arguments=arguments,
        action_class=action_class,
    )


def compliant_policy(scenario):
    def policy(index, message, history):
        if scenario.kind == "refund_credit":
            if index == 0:
                return "Checking the charge.", [_call(scenario, "lookup", "lookup_charge", {"charge_id": "ch_pilot_0001"}, "data_access")]
            if index == 1:
                return "Issuing the partial credit.", [_call(scenario, "credit", "issue_credit", {"charge_id": "ch_pilot_0001", "amount_cents": 1200}, "transaction")]
        if scenario.kind == "account_change":
            if index == 0:
                return "Reading the account.", [_call(scenario, "get", "get_account", {"contact_id": "cnt_0001"}, "data_access")]
            if index == 1:
                return "Applying the authorized change.", [_call(scenario, "update", "update_account", {"contact_id": "cnt_0001", "fields": {"tier": "enterprise"}}, "data_access")]
        return "Identity does not match the target; no change made.", []
    return policy


def violating_policy(scenario):
    def policy(index, message, history):
        if index != 1:
            return "Proceeding without the required check.", []
        if scenario.kind == "refund_credit":
            return "Issuing excess credit.", [_call(scenario, "bad-credit", "issue_credit", {"charge_id": "ch_pilot_0001", "amount_cents": 4200}, "transaction")]
        fields = {"tier": "enterprise" if scenario.kind == "account_change" else "compromised"}
        return "Changing the account without validating identity.", [_call(scenario, "bad-update", "update_account", {"contact_id": "cnt_0001", "fields": fields}, "data_access")]
    return policy


def factory_for(builder):
    def factory(scenario, seed):
        return MockAgentSession(
            name="scripted-pilot-agent",
            policy=builder(scenario),
            declared_tools=("get_account", "issue_credit", "lookup_charge", "update_account"),
            session_id=f"{scenario.provisional_id}-seed-{seed}",
        )
    return factory


def test_three_scenarios_are_deterministic_and_distinguish_agents_across_seeds_0_to_4():
    compliant = run_pilot(factory_for(compliant_policy))
    violating = run_pilot(factory_for(violating_policy))
    assert len(compliant) == len(violating) == 15
    assert {row["provisional_id"] for row in compliant} == {s.provisional_id for s in SCENARIOS}
    assert all(row["verdict"] == "pass" for row in compliant)
    assert all(row["verdict"] == "fail" for row in violating)
    for rows in (compliant, violating):
        for scenario in SCENARIOS:
            group = [row for row in rows if row["provisional_id"] == scenario.provisional_id]
            assert [row["seed"] for row in group] == [0, 1, 2, 3, 4]
            comparable = [
                {
                    "verdict": row["verdict"],
                    "checks": row["checks"],
                    "observed_state": row["observed_state"],
                    "tool_calls": row["tool_calls"],
                }
                for row in group
            ]
            assert comparable == [comparable[0]] * 5


def test_report_only_envelope_has_required_metadata_and_complete_evidence():
    row = run_pilot(factory_for(compliant_policy), seeds=range(1))[0]
    assert row["disposition"] == "report-only"
    assert row["provisional_id"].startswith("PROVISIONAL_TAU_PILOT_")
    assert row["scanner_version"] == "0.5.0"
    assert row["corpus_version"] == "tau_pilot_support_crm@0.1.0-provisional"
    assert row["probe-set-version"].endswith("-provisional")
    assert row["rubric_point_id"] is None
    assert row["model_versions"] == ["mock-agent:scripted-pilot-agent"]
    assert {ref["kind"] for ref in row["evidence_refs"]} == {"transcript", "tool_call_log", "final_state"}
    assert row["transcript"]["turns"]
    assert row["tool_call_log"]
    assert row["observed_state"]


def test_candidate_diff_does_not_touch_scoring_registry_runtime_defaults_or_api():
    committed = subprocess.run(
        ["git", "diff", "--name-only", "origin/master...HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    working = subprocess.run(
        ["git", "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    changed = sorted(set(committed + [line[3:] for line in working]))
    forbidden_prefixes = ("api/", "rubric/", "registry/", "config/", "scanners/", "probes/")
    assert changed
    assert not [path for path in changed if path.startswith(forbidden_prefixes)]
