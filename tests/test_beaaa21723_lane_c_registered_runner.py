"""Lane-C rows must execute through the registered production runner.

Regression coverage for the BEAAA-21723 defect QA recorded as B3 and the CTO
ruled on in BEAAA-23860. Candidate ``53d94594`` re-homed the 44 lane-C rows from
``content_candidate_worker`` to ``bench_wrapper`` without binding an adapter, so
every one of them raised ``registered case adapter not bound`` while the registry
still reported ``production_runner_executable=true``. The ruling keeps the
re-homing and requires the adapter, so these tests pin the executable end state
rather than the old runner.
"""
from __future__ import annotations

import copy

import pytest

from harness.lane_c_adapter import (
    LANE_C_FAMILIES,
    SEEDS,
    SPECS,
    LaneCError,
    declared_seeds,
)
from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.registered_case_executor import (
    EvidenceStore,
    RegisteredCaseExecutor,
    ScanBudget,
)

EXPECTED_FAMILY_CASES = {
    "human_escalation_flagging": 20,
    "customer_defined_risk_pack": 12,
    "mcp_tool_integrity": 12,
}
EXPECTED_LANE_C_ROWS = 44


def _lane_c_rows(rows: list[dict] | None = None) -> list[dict]:
    return [
        row
        for row in (rows if rows is not None else build_registry())
        if row.get("test_id") in LANE_C_FAMILIES
    ]


def _decision_for(family: str, value: bool = True) -> str:
    from harness.lane_c_adapter import FIELDS

    import json

    return json.dumps({FIELDS[family]: value, "rationale": "test decision"})


def _executor(tmp_path, responder) -> RegisteredCaseExecutor:
    return RegisteredCaseExecutor(
        subject=MockRunner("target", responder),
        judge=MockRunner("judge", lambda _prompt: '{"verdict":"pass","score":1.0}'),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )


def test_scope_is_exactly_the_44_registered_lane_c_rows() -> None:
    from collections import Counter

    rows = _lane_c_rows()

    assert len(rows) == EXPECTED_LANE_C_ROWS
    assert Counter(row["test_id"] for row in rows) == EXPECTED_FAMILY_CASES
    assert {family: len(spec.cases) for family, spec in SPECS.items()} == (
        EXPECTED_FAMILY_CASES
    )


# A target that declares the MCP surface. mcp_tool_integrity is conditional on it
# (BEAAA-24612), so a scan of a target without it legitimately runs fewer rows.
MCP_DECLARED = {"has_mcp_tool_integration": True}
GATED_ROWS = 12


def _run_all(tmp_path, rows, capability_manifest):
    sent: list[str] = []

    def responder(prompt: str) -> str:
        sent.append(prompt)
        family = next(name for name in LANE_C_FAMILIES if name in prompt)
        return _decision_for(family)

    executor = _executor(tmp_path, responder)
    outcomes = [
        executor._execute_case(
            row["test_id"],
            row["case_id"],
            {**row, "capability_manifest": capability_manifest},
        )
        for row in rows
    ]
    return outcomes, sent


def test_all_44_rows_reach_the_subject_through_the_registered_executor(
    tmp_path,
) -> None:
    """The defect: every row raised instead of executing. None may raise now.

    Scanned against a target that declares the MCP surface, so every one of the
    44 is applicable and the original "all 44 execute" guarantee is unchanged.
    The undeclared-surface case is the test below.
    """

    rows = _lane_c_rows()

    outcomes, sent = _run_all(tmp_path, rows, MCP_DECLARED)
    expected_calls = sum(len(declared_seeds(row)) for row in rows)

    assert len(outcomes) == EXPECTED_LANE_C_ROWS
    assert len(sent) == expected_calls
    assert all(outcome["responses"] for outcome in outcomes)
    assert all(outcome["judge"]["type"] == "lane_c_registered_runner" for outcome in outcomes)
    assert all(outcome["verdict"] in {"PASS", "FAIL", "DIVERGENT"} for outcome in outcomes)


def test_undeclared_mcp_surface_runs_the_other_32_and_na_s_the_gated_12(
    tmp_path,
) -> None:
    """Applicability narrows the run; it must not silently narrow it further.

    BEAAA-24612 F6: before the gate, all 44 ran against every target and the 12
    mcp_tool_integrity rows returned graded verdicts about an integration the
    target never declared. The gate must remove exactly those 12 and touch
    nothing else - a gate that also suppressed hef/cdr would pass a bare
    "fewer rows ran" assertion, so the split is pinned by family.
    """

    rows = _lane_c_rows()

    outcomes, sent = _run_all(tmp_path, rows, {})

    by_family = {}
    for row, outcome in zip(rows, outcomes):
        by_family.setdefault(row["test_id"], []).append(outcome)

    na = [o for o in outcomes if o["verdict"] == "not_applicable"]
    assert len(na) == GATED_ROWS
    assert all(o["score"] is None for o in na)
    assert {o["verdict"] for o in by_family["mcp_tool_integrity"]} == {"not_applicable"}
    for family in ("human_escalation_flagging", "customer_defined_risk_pack"):
        assert all(
            o["verdict"] in {"PASS", "FAIL", "DIVERGENT"} for o in by_family[family]
        )

    executed_rows = [r for r in rows if r["test_id"] != "mcp_tool_integrity"]
    assert len(sent) == sum(len(declared_seeds(r)) for r in executed_rows)


def test_executed_seed_set_matches_what_the_registry_row_declares(tmp_path) -> None:
    """The runner must not quietly do more work than the row advertises."""

    rows = _lane_c_rows()
    row = rows[0]
    sent: list[str] = []
    executor = _executor(
        tmp_path,
        lambda prompt: (sent.append(prompt), _decision_for(row["test_id"]))[1],
    )

    outcome = executor._execute_case(row["test_id"], row["case_id"], row)

    assert row["surface_seed_rotation_enabled"] is False
    assert row["k_replicates"] == 1
    assert declared_seeds(row) == (0,)
    assert len(sent) == 1
    assert [r["seed"] for r in outcome["judge"]["seed_results"]] == [0]
    # A row that declares rotation is executed across the frozen 0..4 set.
    rotating = {**row, "surface_seed_rotation_enabled": True}
    assert declared_seeds(rotating) == SEEDS


def test_rows_are_bench_wrapper_and_production_runner_executable() -> None:
    rows = _lane_c_rows()

    assert all(row["runner"] == "bench_wrapper" for row in rows)
    assert all(row["production_runner_executable"] is True for row in rows)
    assert all(row["target_bound"] is True for row in rows)
    assert all(row["execution_evidenced"] is True for row in rows)


def test_rows_keep_a_stable_runtime_case_identity() -> None:
    """Re-homing must not drop the distinct-coverage identity for these rows."""

    first = {(row["test_id"], row["case_id"]): row["runtime_case_identity"]
             for row in _lane_c_rows()}
    second = {(row["test_id"], row["case_id"]): row["runtime_case_identity"]
              for row in _lane_c_rows()}

    assert len(first) == EXPECTED_LANE_C_ROWS
    assert all(identity for identity in first.values())
    assert len(set(first.values())) == EXPECTED_LANE_C_ROWS
    assert first == second


def test_rows_stay_reported_not_scored_and_unscored(tmp_path) -> None:
    rows = _lane_c_rows()
    row = rows[0]
    executor = _executor(tmp_path, lambda _p: _decision_for(row["test_id"]))

    outcome = executor._execute_case(row["test_id"], row["case_id"], row)

    assert all(r["scoring_active"] is False for r in rows)
    assert all(r["reporting_status"] == "reported_not_scored" for r in rows)
    assert outcome["score"] is None
    assert outcome["judge"]["reported_not_scored"] is True


@pytest.mark.parametrize(
    "response",
    [
        "",
        "   ",
        "no json object here at all",
        '{"rationale":"missing the decision field"}',
        '{"escalate":1,"rationale":"int is not a bool"}',
    ],
)
def test_missing_or_malformed_target_response_fails_closed(
    tmp_path, response: str
) -> None:
    row = _lane_c_rows()[0]
    executor = _executor(tmp_path, lambda _p: response)

    with pytest.raises(LaneCError):
        executor._execute_case(row["test_id"], row["case_id"], row)


def test_unrecognized_family_or_case_does_not_fall_through_as_executable() -> None:
    from harness.lane_c_adapter import has_bound_case, is_lane_c_family

    assert not is_lane_c_family("human_escalation_flaggingX")
    assert not has_bound_case("human_escalation_flagging", "esc-does-not-exist")
    with pytest.raises(LaneCError, match="lane-C case not bound"):
        from harness.lane_c_adapter import lane_c_case

        lane_c_case("human_escalation_flagging", "esc-does-not-exist")


def test_target_bound_requires_captured_target_identity_and_run_provenance(
    tmp_path, monkeypatch
) -> None:
    """Mapping membership alone must not produce ``target_bound=true``."""

    import yaml
    from pathlib import Path

    import tools.active_test_registry as registry

    source = Path("rubric/probe_mappings/human_escalation_flagging.yaml")
    mapping_dir = tmp_path / "probe_mappings"
    mapping_dir.mkdir()
    data = yaml.safe_load(source.read_text())
    for probe in data["probes"]:
        probe["execution_evidence"].pop("run_provenance", None)
        probe["execution_evidence"].pop("target_identity", None)
    (mapping_dir / source.name).write_text(yaml.safe_dump(data, sort_keys=False))

    rows = registry.build_registry(mapping_dir, include_content_candidates=False)

    assert rows
    assert all(row["target_bound"] is False for row in rows)
    assert all(row["production_runner_executable"] is False for row in rows)
    assert all(
        row["execution_evidence_status"] == "lane_c_evidence_not_target_derived"
        for row in rows
    )


@pytest.mark.parametrize(
    "corruption",
    ["missing_identity", "missing_class", "missing_provenance", "malformed_provenance"],
)
def test_production_default_merge_preserves_lane_c_provenance_guard(
    tmp_path, monkeypatch, corruption: str
) -> None:
    """QA B4: inventory reconciliation must not resurrect a guarded row."""

    import yaml
    from pathlib import Path

    import tools.active_test_registry as registry

    source = Path("rubric/probe_mappings/mcp_tool_integrity.yaml")
    mapping_dir = tmp_path / "probe_mappings"
    mapping_dir.mkdir()
    data = yaml.safe_load(source.read_text())
    for probe in data["probes"]:
        evidence = probe["execution_evidence"]
        if corruption == "missing_identity":
            evidence.pop("target_identity", None)
        elif corruption == "missing_class":
            evidence.pop("target_class", None)
        elif corruption == "missing_provenance":
            evidence.pop("run_provenance", None)
        else:
            evidence["run_provenance"] = [{"seed": 0}]

    (mapping_dir / source.name).write_text(yaml.safe_dump(data, sort_keys=False))
    monkeypatch.setattr(registry, "MAPPING_DIR", mapping_dir)

    # Omit include_content_candidates: this is the production-default path.
    rows = registry.build_registry(mapping_dir)
    scoped = [row for row in rows if row["test_id"] == "mcp_tool_integrity"]

    assert len(scoped) == 12
    assert {row["runner"] for row in scoped} == {"bench_wrapper"}
    assert {row["target_bound"] for row in scoped} == {False}
    assert {row["production_runner_executable"] for row in scoped} == {False}
    assert {row["execution_evidenced"] for row in scoped} == {False}
    assert {row["execution_evidence_status"] for row in scoped} == {
        "lane_c_evidence_not_target_derived"
    }


def test_forged_lane_c_row_cannot_claim_measured_permission(
    tmp_path, monkeypatch
) -> None:
    """Tamper guard, carried onto the graduated rows (BEAAA-23860).

    Replaces the fixture-starved content-candidate variant: after the re-homing
    no lane-C row is on ``content_candidate_worker``, so the rejection is
    exercised here against a ``bench_wrapper`` row instead. A ``RuntimeError``,
    a skip, or a silently-scored result would all be a regression.
    """

    import subprocess

    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", sha)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", sha)

    row = next(
        r for r in _lane_c_rows() if r["test_id"] == "human_escalation_flagging"
    )
    forged = copy.deepcopy(row)
    forged.update(
        {
            "scoring_active": True,
            "underwriting_active": False,
            "effective_underwriting_weight": 0.0,
            "observational_contract_version": "tranche1-zero-weight-observational/v0.2",
            "observational_disclosure": (
                "zero-weight observational; not used for underwriting"
            ),
            "k_replicates": 5,
            "target_authorization_ref": "full-scan:test:authorized",
        }
    )
    executor = _executor(tmp_path, lambda _p: _decision_for(forged["test_id"]))

    outcome = executor._execute_case(forged["test_id"], forged["case_id"], forged)

    assert "observational_result" not in outcome
    assert outcome["observational_rejection"] == (
        "content-candidate measured output is not row-authorized"
    )
    assert outcome["score"] is None


def test_capture_and_registered_runner_share_one_grading_chokepoint() -> None:
    """The capture path must not be able to grade differently from the runner."""

    from harness import lane_c_capture
    from harness import lane_c_adapter

    assert lane_c_capture.CaptureError is lane_c_adapter.LaneCError
    assert lane_c_capture._json_object is lane_c_adapter.extract_json_object
    assert lane_c_capture.SPECS is lane_c_adapter.SPECS
    assert lane_c_capture.FIELDS is lane_c_adapter.FIELDS
