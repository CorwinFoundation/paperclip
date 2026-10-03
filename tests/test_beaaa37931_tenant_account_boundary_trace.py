from dataclasses import replace
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace,
    ControlledToolDispatcher,
    EffectStore,
    ToolSpec,
    derive_tenant_account_boundary,
)
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _dispatcher():
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec("tenant_operation", "tenant-a", lambda p: {"tenant_id": "tenant-a", **p}))
    return runtime, trace


def test_registry_marks_exactly_the_25_tenant_boundary_rows():
    rows = [row for row in build_registry() if row.get("trace_observation_adapter")]
    family = [row for row in rows if row["test_id"] == "tenant_account_boundary"]
    assert len(family) == 25
    assert {row["case_id"] for row in family} == {
        f"bb18719.tenant_account_boundary.{n:02d}" for n in range(1, 26)
    }


def test_missing_and_contradictory_tenant_trace_evidence_fail_closed():
    runtime, trace = _dispatcher()
    runtime.dispatch("tenant_operation", {"operation_id": "x"}, scope="tenant-b", idempotency_key="x")
    event = trace.events()[0]
    assert derive_tenant_account_boundary((event,), expected_count=0) == ("PASS", 0)
    assert derive_tenant_account_boundary((replace(event, side_effect_receipt=None),), expected_count=0) == ("N/A", None)
    contradictory = replace(event, side_effect_receipt={"committed": True, "effect_id": "forged"})
    assert derive_tenant_account_boundary((contradictory,), expected_count=0) == ("FAIL", None)


def test_all_25_rows_execute_and_produce_their_family_specific_trace_field(tmp_path):
    rows = [row for row in build_registry() if row.get("trace_observation_adapter") and row["test_id"] == "tenant_account_boundary"]
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _prompt: '{"mutation_count":999,"verdict":"PASS"}'),
        judge=MockRunner("unused-judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_target_calls=1),
    )
    for row in rows:
        result = executor(row, SimpleNamespace(job_id="beaaa-37931", scan_id="beaaa-37931", agent_id="trace-target", seed=0, capability_manifest=None))
        assert result.status == "complete"
        assert result.verdict == "pass"
        assert result.target_calls == result.judge_calls == 0
        evidence_path = tmp_path / result.evidence_ref.removeprefix("evidence://")
        observation = __import__("json").loads(evidence_path.read_text())["judge"]["observation"]
        oracle = materialize_candidate_case(row["test_id"], row["case_id"]).oracle
        assert row["candidate_oracle_type"] == "deterministic_field_assertion"
        field, expected = oracle["field"], oracle["expected"]
        assert observation[field] == expected
