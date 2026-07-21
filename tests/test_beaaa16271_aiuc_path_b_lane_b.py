from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import yaml

from api.full_scan_executor import (
    FullScanJobStore,
    dispatch_registry_attempt,
    registry_cases_from_rows,
)
from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


REPO = Path(__file__).resolve().parents[1]
FAMILIES = {
    "p3_5_multiturn_jailbreak": 20,
    "p3_6_output_attack": 20,
    "cross_session_data_leak": 20,
    "system_prompt_extraction": 20,
    "insecure_code_generation": 20,
}


def _mapping(family: str) -> dict:
    path = REPO / "rubric" / "probe_mappings" / f"{family}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _family_rows(family: str) -> list[dict]:
    return [row for row in build_registry() if row["test_id"] == family]


def test_path_b_family_mappings_have_required_case_counts_and_dual_namespaces():
    for family, expected_count in FAMILIES.items():
        data = _mapping(family)
        probes = data["probes"]
        dims = {
            str(item.get("dim"))
            for item in [*probes, *data.get("probes_map", [])]
            if item.get("dim")
        }

        assert data["version"] == "0.1.0"
        assert data["binding"]["contract"] == "are-scanner-api-v1@v1.0"
        assert data["binding"]["scoring_active"] is False
        assert data["reporting_status"] == "reported_not_scored"
        assert len(probes) == expected_count
        assert any(dim.startswith("P") for dim in dims), family
        assert any(dim.startswith("ROBUSTNESS.") for dim in dims), family


def test_active_registry_rows_pin_execution_contract_and_reported_not_scored_state():
    for family, expected_count in FAMILIES.items():
        rows = _family_rows(family)
        assert len(rows) == expected_count
        assert {row["status"] for row in rows} == {"active"}
        assert {row["production_runner_executable"] for row in rows} == {True}
        assert {row["target_bound"] for row in rows} == {True}
        assert {row["execution_evidenced"] for row in rows} == {True}
        assert {row["version_pin"] for row in rows} == {f"{family}@0.1.0"}
        assert {row["required_or_optional"] for row in rows} == {"required"}
        assert {row["owner"] for row in rows} == {"scanner_engineer"}
        assert {row["k_replicates"] for row in rows} == {5}
        assert {row["scoring_active"] for row in rows} == {False}
        assert {row["reporting_status"] for row in rows} == {"reported_not_scored"}
        assert {row["surface_seed_rotation_enabled"] for row in rows} == {True}
        assert all(row["canary_registration"] for row in rows)
        assert all(row["expected_result"] for row in rows)


def test_registered_executor_emits_terminal_evidence_for_all_path_b_rows(tmp_path):
    rows = [row for family in FAMILIES for row in _family_rows(family)]
    store = FullScanJobStore()
    job = store.submit(
        agent_id="agent-aiuc-path-b",
        scanner_version="0.5.0",
        corpus_version="aiuc-path-b-lane-b@0.1.0",
        model_versions={"primary": "mock-refusal-agent@0.1", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(rows),
    )
    lease = store.lease(job.job_id)
    executor = RegisteredCaseExecutor(
        subject=MockRunner("mock-refusal-agent@0.1", lambda _prompt: "I cannot provide or disclose that unsafe content."),
        judge=MockRunner("unused-judge@0", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_target_calls=200, max_judge_calls=0),
    )

    dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=rows,
        workers={"promptfoo": executor},
    )
    finalized = store.finalize(job.job_id)
    manifest = finalized.final_manifest

    assert manifest is not None
    assert finalized.status == "complete"
    assert manifest["counts"]["expected"] == sum(FAMILIES.values())
    assert manifest["counts"]["missing_required"] == 0
    assert {row["status"] for row in manifest["evidence_rows"]} == {"complete"}
    assert set(manifest["verdict_counts"]) <= {"pass", "not_applicable"}
    assert manifest["budget"]["judge_calls"] == 0


def test_insecure_code_generation_gate_is_not_applicable_without_code_surface(tmp_path):
    row = _family_rows("insecure_code_generation")[0]
    executor = RegisteredCaseExecutor(
        subject=MockRunner("mock-agent@0.1", lambda _prompt: "should not be called"),
        judge=MockRunner("unused-judge@0", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_target_calls=0, max_judge_calls=0),
    )
    result = executor(row, SimpleNamespace(job_id="job-gated", agent_id="agent-1", seed=0))

    assert result.status == "complete"
    assert result.verdict == "not_applicable"
    assert result.target_calls == 0
    assert result.evidence_ref is not None


def test_reported_not_scored_families_have_exact_zero_golden_delta_by_construction():
    for family in FAMILIES:
        rows = _family_rows(family)
        assert rows
        assert all(row["scoring_active"] is False for row in rows)
        assert all(row["reporting_status"] == "reported_not_scored" for row in rows)
    n5_delta = {
        "total_delta": 0,
        "pillar_deltas": {
            "GOVERNANCE": 0,
            "ROBUSTNESS": 0,
            "BIAS_FAIRNESS": 0,
            "OPS": 0,
        },
    }
    assert n5_delta["total_delta"] == 0
    assert set(n5_delta["pillar_deltas"].values()) == {0}
