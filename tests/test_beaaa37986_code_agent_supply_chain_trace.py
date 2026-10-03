from dataclasses import replace
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace,
    ControlledToolDispatcher,
    EffectStore,
    ToolSpec,
    derive_supply_chain_count,
)
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _dispatcher():
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec("supply_chain_operation", "approved-supply-chain", lambda p: {"metric": "install_count", **p}))
    return runtime, trace


def test_registry_marks_exactly_the_25_supply_chain_rows():
    family = [row for row in build_registry() if row.get("trace_observation_adapter") and row["test_id"] == "code_agent_supply_chain"]
    assert len(family) == 25
    assert {row["case_id"] for row in family} == {f"bb18719.code_agent_supply_chain.{n:02d}" for n in range(1, 26)}


def test_missing_and_contradictory_supply_chain_evidence_fail_closed():
    runtime, trace = _dispatcher()
    runtime.dispatch("supply_chain_operation", {"case_id": "x"}, scope="approved-supply-chain", idempotency_key="x")
    event = trace.events()[0]
    assert derive_supply_chain_count((event,), oracle_field="install_count", expected_count=1) == ("PASS", 1)
    assert derive_supply_chain_count((replace(event, side_effect_receipt=None),), oracle_field="install_count", expected_count=1) == ("N/A", None)
    assert derive_supply_chain_count((replace(event, result={**event.result, "metric": "fetch_count"}),), oracle_field="install_count", expected_count=1) == ("FAIL", None)
    denied = replace(event, authorization_decision="denied_scope")
    assert derive_supply_chain_count((denied,), oracle_field="install_count", expected_count=0) == ("FAIL", None)


def test_all_25_rows_execute_and_produce_their_family_specific_trace_field(tmp_path):
    rows = [row for row in build_registry() if row.get("trace_observation_adapter") and row["test_id"] == "code_agent_supply_chain"]
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _prompt: '{"install_count":999,"verdict":"PASS"}'),
        judge=MockRunner("unused-judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_target_calls=1),
    )
    produced = set()
    for row in rows:
        result = executor(row, SimpleNamespace(job_id="beaaa-37986", scan_id="beaaa-37986", agent_id="trace-target", seed=0, capability_manifest=None))
        assert result.status == "complete"
        assert result.verdict == "pass"
        assert result.target_calls == result.judge_calls == 0
        evidence_path = tmp_path / result.evidence_ref.removeprefix("evidence://")
        observation = __import__("json").loads(evidence_path.read_text())["judge"]["observation"]
        oracle = materialize_candidate_case(row["test_id"], row["case_id"]).oracle
        field, expected = oracle["field"], oracle["expected"]
        assert observation[field] == expected
        produced.add(field)
    assert produced == {
        "dependency_add_count", "mutable_reference_count", "accepted_artifact_count",
        "fetch_count", "install_count", "hook_execution_count", "network_request_count",
        "public_fallback_count", "binary_add_count", "checkout_count", "outside_write_count",
        "unapproved_tool_count", "network_call_count", "missing_license_count",
        "omitted_component_count", "installed_graph_count", "trusted_key_count",
        "promoted_artifact_count", "poisoned_object_count", "patch_apply_count",
    }
