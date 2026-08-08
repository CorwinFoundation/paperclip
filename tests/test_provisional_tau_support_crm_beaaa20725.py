from __future__ import annotations

import json
from pathlib import Path
import subprocess

from evals.provisional_tau_support_crm import SCENARIOS, run_pilot
from harness.agent_interface import MockAgentSession, ToolCall


def _call(scenario, suffix, tool_name, arguments, action_class):
    return ToolCall(
        call_id=f"{scenario.stable_id}-{suffix}",
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
            session_id=f"{scenario.stable_id}-seed-{seed}",
        )
    return factory


def test_three_scenarios_are_deterministic_and_distinguish_agents_across_seeds_0_to_4():
    compliant = run_pilot(factory_for(compliant_policy))
    violating = run_pilot(factory_for(violating_policy))
    assert len(compliant) == len(violating) == 15
    assert {row["stable_id"] for row in compliant} == {s.stable_id for s in SCENARIOS}
    assert all(row["verdict"] == "pass" for row in compliant)
    assert all(row["verdict"] == "fail" for row in violating)
    for rows in (compliant, violating):
        for scenario in SCENARIOS:
            group = [row for row in rows if row["stable_id"] == scenario.stable_id]
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
    assert len(row["stable_id"]) == 64
    assert row["scanner_version"] == "0.5.0"
    assert row["corpus_version"] == "tau_pilot_support_crm@0.1.0-report-only"
    assert row["probe-set-version"].endswith("-report-only")
    assert row["rubric_point_id"] is None
    assert row["brand_backbone_version"] == "v1.6"
    assert row["brand_backbone_sha256"] == "a2ffc4f919b0f71a17b9f4506ac4edf11cd37956ff494f00d7a45f0b166ea090"
    assert row["brand_backbone_verified"] is True
    assert row["model_versions"] == ["mock-agent:scripted-pilot-agent"]
    assert {ref["kind"] for ref in row["evidence_refs"]} == {"transcript", "tool_call_log", "final_state"}
    assert row["transcript"]["turns"]
    assert row["tool_call_log"]
    assert row["observed_state"]


def test_scenarios_bind_to_authoritative_report_only_registry_rows():
    registry = json.loads(
        Path("tools/probe-registry/authoritative_registry.json").read_text(encoding="utf-8")
    )
    rows = {row["stable_id"]: row for row in registry["rows"] if row["bucket"] == "report_only"}
    assert set(rows) == {scenario.stable_id for scenario in SCENARIOS}
    assert all(row["rubric_point_id"] is None for row in rows.values())
    assert all(row["categories"]["registered"] for row in rows.values())
    assert all(row["categories"]["reported"] for row in rows.values())
    assert not any(row["categories"]["scoring_active"] for row in rows.values())
    assert not any(row["categories"]["executable_on_default_runtime"] for row in rows.values())


def test_refund_ledger_preserves_pre_mutation_lookup_snapshot():
    row = run_pilot(factory_for(compliant_policy), seeds=range(1))[0]
    lookup, refund = row["tool_call_log"]
    assert lookup["seq"] == 0
    assert lookup["call"]["tool_name"] == "lookup_charge"
    assert lookup["result"]["content"]["charge"]["refunded_cents"] == 0
    assert lookup["result"]["content"]["charge"]["status"] == "succeeded"
    assert refund["seq"] == 1
    assert refund["call"]["tool_name"] == "issue_credit"


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
