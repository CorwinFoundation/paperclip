"""A conditional family may not be executed against a surface it does not apply to.

BEAAA-24612 F6 / directive BEAAA-24008 ruling D.3. On master ``c76d2b92`` a real
``mcp_tool_integrity`` row dispatched at a target whose capability manifest never
declared an MCP or A2A surface: the production path sent the probe, graded the
answer and returned ``verdict=PASS``. That is a fabricated result — the scanner
reported a tool-integrity finding about an integration the target does not have.

``mcp_tool_integrity`` is the only lane-C family that declares an applicability
gate (``FamilySpec.applicability_flag``), and ``evals.aiuc_path_b.base.run_family``
has always honored it. The production executor did not, so the eval library and
the production path disagreed about the same contract.

Ruling E: coverage that survives deletion of the guard is inert. The gate-removal
mutant at the bottom of this module is what makes the rest of these tests
load-bearing — it asserts the observable actually flips when the gate is gone.
"""
from __future__ import annotations

import json

import pytest

from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.registered_case_executor import (
    EvidenceStore,
    RegisteredCaseExecutor,
    ScanBudget,
)
import tools.registered_case_executor as rce

GATED_FAMILY = "mcp_tool_integrity"
# BEAAA-29743 gated these two on a structured-response surface. Every lane-C
# family now carries an applicability flag, so there is no ungated family left
# to contrast against; the contrast is now declared-vs-undeclared surface.
STRUCTURED_LANE_C = ("human_escalation_flagging", "customer_defined_risk_pack")
STRUCTURED_FLAG = "has_structured_output"
DECLARED = {"has_mcp_tool_integration": True}
STRUCTURED_DECLARED = {STRUCTURED_FLAG: True}


class _Calls:
    def __init__(self) -> None:
        self.target: list[str] = []
        self.judge: list[str] = []


def _executor(tmp_path, calls: _Calls, family: str) -> RegisteredCaseExecutor:
    from harness.lane_c_adapter import FIELDS

    def target(prompt: str) -> str:
        calls.target.append(prompt)
        # A cooperative target that answers this family's decision contract.
        return json.dumps({FIELDS[family]: True, "rationale": "test decision"})

    def judge(prompt: str) -> str:
        calls.judge.append(prompt)
        return json.dumps({"verdict": "pass", "score": 1.0})

    return RegisteredCaseExecutor(
        subject=MockRunner("target", target),
        judge=MockRunner("judge", judge),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )


def _rows(family: str) -> list[dict]:
    return [row for row in build_registry() if row.get("test_id") == family]


def _run(tmp_path, row: dict, capability_manifest):
    calls = _Calls()
    executor = _executor(tmp_path, calls, row["test_id"])
    outcome = executor._execute_case(
        row["test_id"], row["case_id"], {**row, "capability_manifest": capability_manifest}
    )
    return outcome, calls


# --- the defect, in every shape a manifest can fail to declare the surface ---

@pytest.mark.parametrize(
    "capability_manifest",
    [
        pytest.param({}, id="empty_manifest"),
        pytest.param(None, id="absent_manifest"),
        pytest.param({"has_mcp_tool_integration": False}, id="explicitly_false"),
        pytest.param({"has_tool_calling": True}, id="unrelated_flag_only"),
        pytest.param("has_mcp_tool_integration", id="malformed_manifest"),
        pytest.param({"has_mcp_tool_integration": "true"}, id="truthy_string_is_not_true"),
    ],
)
def test_undeclared_mcp_surface_is_not_applicable_and_costs_nothing(
    tmp_path, capability_manifest
) -> None:
    row = _rows(GATED_FAMILY)[0]

    outcome, calls = _run(tmp_path, row, capability_manifest)

    assert outcome["verdict"] == "not_applicable"
    assert outcome["score"] is None
    assert calls.target == []
    assert calls.judge == []
    assert outcome["judge"]["applicable"] is False


@pytest.mark.parametrize("dev_mode", ["on", "off"])
def test_gate_holds_under_both_founder_dev_mode_settings(
    tmp_path, monkeypatch, dev_mode
) -> None:
    """Dev mode changes whether these rows score, never whether they apply."""

    if dev_mode == "on":
        monkeypatch.setenv("BACKBOND_FOUNDER_DEV_MODE", "on")
    else:
        monkeypatch.delenv("BACKBOND_FOUNDER_DEV_MODE", raising=False)
    row = _rows(GATED_FAMILY)[0]

    outcome, calls = _run(tmp_path, row, {})

    assert outcome["verdict"] == "not_applicable"
    assert outcome["score"] is None
    assert (len(calls.target), len(calls.judge)) == (0, 0)


def test_every_gated_case_goes_not_applicable_not_just_the_first(tmp_path) -> None:
    rows = _rows(GATED_FAMILY)
    assert len(rows) == 12

    results = [_run(tmp_path, row, {}) for row in rows]

    assert {outcome["verdict"] for outcome, _ in results} == {"not_applicable"}
    assert {outcome["score"] for outcome, _ in results} == {None}
    assert sum(len(c.target) + len(c.judge) for _, c in results) == 0


# --- positive control: the gate must not be a blanket refusal ---

def test_declared_mcp_surface_still_executes(tmp_path) -> None:
    """Without this, a gate that N/A'd everything would pass every test above."""

    row = _rows(GATED_FAMILY)[0]

    outcome, calls = _run(tmp_path, row, DECLARED)

    assert outcome["verdict"] != "not_applicable"
    assert len(calls.target) > 0


@pytest.mark.parametrize("family", STRUCTURED_LANE_C)
def test_structured_lane_c_families_run_when_the_surface_is_declared(
    tmp_path, family
) -> None:
    """BEAAA-29743: declaring the surface must leave hef/cdr measured as before."""

    row = _rows(family)[0]

    outcome, calls = _run(tmp_path, row, STRUCTURED_DECLARED)

    assert outcome["verdict"] != "not_applicable"
    assert len(calls.target) > 0


@pytest.mark.parametrize("family", STRUCTURED_LANE_C)
def test_structured_lane_c_families_are_na_without_the_surface(
    tmp_path, family
) -> None:
    """The BEAAA-29743 defect: a prose-only target must be N/A, not failed."""

    row = _rows(family)[0]

    outcome, calls = _run(tmp_path, row, {})

    assert outcome["verdict"] == "not_applicable"
    assert outcome["score"] is None
    assert calls.target == []


# --- the declaration and the enforcement may not drift apart ---

def test_enforced_flag_matches_the_family_spec_and_the_rubric_mapping() -> None:
    from pathlib import Path

    import yaml

    from evals.aiuc_path_b.manifest import FAMILY_MODULES

    spec_flag = FAMILY_MODULES[GATED_FAMILY].SPEC.applicability_flag
    mapping = yaml.safe_load(
        (Path(rce.REPO_ROOT) / "rubric" / "probe_mappings" / f"{GATED_FAMILY}.yaml")
        .read_text(encoding="utf-8")
    )

    assert spec_flag == "has_mcp_tool_integration"
    assert mapping["applicability"]["gate_flag"] == spec_flag
    assert mapping["applicability"]["na_when_absent"] is True
    # what the production path actually enforces
    assert rce._declared_applicability_flag(GATED_FAMILY) == spec_flag
    for family in STRUCTURED_LANE_C:
        assert FAMILY_MODULES[family].SPEC.applicability_flag == STRUCTURED_FLAG
        family_mapping = yaml.safe_load(
            (Path(rce.REPO_ROOT) / "rubric" / "probe_mappings" / f"{family}.yaml")
            .read_text(encoding="utf-8")
        )
        assert family_mapping["applicability"]["gate_flag"] == STRUCTURED_FLAG
        assert family_mapping["applicability"]["na_when_absent"] is True
        assert rce._declared_applicability_flag(family) == STRUCTURED_FLAG


# --- ruling E: prove the coverage above is not inert ---

def test_removing_the_gate_makes_the_undeclared_surface_execute_again(
    tmp_path, monkeypatch
) -> None:
    """Gate-removal mutant. If this test ever fails, the tests above prove nothing.

    Restores the pre-fix behaviour by neutralising _resolve_applicability and
    asserts the observable flips back: the undeclared surface is probed and
    graded. A test suite that still passes with the guard deleted is coverage
    theatre; this is the check that says it isn't.
    """

    row = _rows(GATED_FAMILY)[0]

    # baseline: gate present -> no execution
    guarded_outcome, guarded_calls = _run(tmp_path, row, {})
    assert guarded_outcome["verdict"] == "not_applicable"
    assert guarded_calls.target == []

    monkeypatch.setattr(rce, "_resolve_applicability", lambda *_a, **_k: (True, "always"))
    mutant_outcome, mutant_calls = _run(tmp_path, row, {})

    assert mutant_outcome["verdict"] != "not_applicable"
    assert len(mutant_calls.target) > 0, (
        "gate removal did not change behaviour - the applicability tests in this "
        "module are inert and prove nothing"
    )
