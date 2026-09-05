"""BEAAA-29743: lane C is inapplicable to a target with no structured-response surface.

The live BEAAA-29082 scan (`d3c0b756`) drove the real VAPI assistant, which
answers in prose because that is what a customer-facing voice assistant does.
Lane C grades a JSON decision object, so all 32 required rows raised
``target response did not contain a JSON object``, landed ``status="failed"``,
and forced ``counts.missing_required > 0``.

The ruling is option 3: lane C measures a structured-decision surface, so a
target without one is *out of scope* for it rather than failing it. That is
expressed where BEAAA-24612 / directive BEAAA-24008 ruling D.3 already put this
decision — ``FamilySpec.applicability_flag``, resolved per target off the job's
capability manifest — not as a registry-global executability flip, which would
also delete lane C for the JSON-capable targets where it works today.

The parser is deliberately NOT widened: a target that *declares* the surface and
then speaks prose anyway still fails closed.
"""
from __future__ import annotations

import functools
import json

import pytest

from harness.lane_c_adapter import LaneCError
from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.registered_case_executor import (
    EvidenceStore,
    RegisteredCaseExecutor,
    ScanBudget,
)
import tools.registered_case_executor as rce

PROSE_FAMILIES = ("human_escalation_flagging", "customer_defined_risk_pack")
FLAG = "has_structured_output"
DECLARED = {FLAG: True}

#: A real customer-facing voice-assistant answer: it addresses the scenario in
#: natural language and never serializes a decision.
PROSE_RESPONSE = (
    "I understand this is a difficult situation, and I'm sorry you're dealing "
    "with it. I'm not able to resolve something of that size myself, so let me "
    "get one of our specialists on the line with you right away."
)


class _Calls:
    def __init__(self) -> None:
        self.target: list[str] = []
        self.judge: list[str] = []


def _executor(tmp_path, calls: _Calls, *, prose: bool):
    from harness.lane_c_adapter import FIELDS

    def target(prompt: str) -> str:
        calls.target.append(prompt)
        if prose:
            return PROSE_RESPONSE
        family = next(f for f in FIELDS if f in prompt)
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


@functools.lru_cache(maxsize=None)
def _row(family: str) -> dict:
    # build_registry() is expensive; these tests only need one stable row each.
    return [row for row in build_registry() if row.get("test_id") == family][0]


def _run(tmp_path, family: str, manifest, *, prose: bool = True):
    calls = _Calls()
    executor = _executor(tmp_path, calls, prose=prose)
    row = _row(family)
    outcome = executor._execute_case(
        row["test_id"], row["case_id"], {**row, "capability_manifest": manifest}
    )
    return outcome, calls


# --- the live defect, against a prose fixture rather than a JSON one ---

@pytest.mark.parametrize("family", PROSE_FAMILIES)
@pytest.mark.parametrize(
    "manifest",
    [
        pytest.param({}, id="empty_manifest"),
        pytest.param(None, id="absent_manifest"),
        pytest.param({FLAG: False}, id="explicitly_false"),
        pytest.param({"has_tool_calling": True}, id="unrelated_flag_only"),
        pytest.param({FLAG: "true"}, id="truthy_string_is_not_true"),
    ],
)
def test_prose_only_target_is_not_applicable_and_never_dispatches(
    tmp_path, family, manifest
) -> None:
    outcome, calls = _run(tmp_path, family, manifest)

    assert outcome["verdict"] == "not_applicable"
    assert outcome["score"] is None
    # No target call at all: an N/A row costs nothing and cannot be graded.
    assert (calls.target, calls.judge) == ([], [])
    assert outcome["judge"]["applicable"] is False
    assert outcome["judge"]["applicability_reason"].startswith(FLAG)


@pytest.mark.parametrize("family", PROSE_FAMILIES)
def test_declaring_the_surface_still_fails_closed_on_prose(tmp_path, family) -> None:
    """The flag is a declaration, not a guarantee. The parser is not widened."""

    with pytest.raises(LaneCError) as excinfo:
        _run(tmp_path, family, DECLARED, prose=True)

    assert "did not contain a JSON object" in str(excinfo.value)


@pytest.mark.parametrize("family", PROSE_FAMILIES)
def test_a_json_speaking_target_still_executes_and_grades(tmp_path, family) -> None:
    """The gate must not delete lane C for the targets where it works today."""

    outcome, calls = _run(tmp_path, family, DECLARED, prose=False)

    assert outcome["verdict"] != "not_applicable"
    assert calls.target, "a declared structured surface must still be probed"


# --- ruling E: coverage that survives deleting the guard is inert ---

@pytest.mark.parametrize("family", PROSE_FAMILIES)
def test_removing_the_gate_reintroduces_the_live_defect(
    tmp_path, family, monkeypatch
) -> None:
    """Mutant: with the applicability gate gone, the prose target crashes again.

    This is what makes the assertions above load-bearing — it pins the exact
    observable from the d3c0b756 scan, LaneCError on a prose answer.
    """

    monkeypatch.setattr(rce, "_declared_applicability_flag", lambda test_id: None)

    with pytest.raises(LaneCError) as excinfo:
        _run(tmp_path, family, {}, prose=True)

    assert "did not contain a JSON object" in str(excinfo.value)
