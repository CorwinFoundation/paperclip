"""A family that declares its gate in the rubric mapping must have it honored.

BEAAA-30934 work item 1. The Vapi lane finished with 88 rows coded
``dispatch_failure``; 24 of them were ``tool_misuse_unauthorized_call`` (12) and
``tool_misuse_scope_escape`` (12), and they did not fail for want of an answer
from the target. They failed at ``registered case adapter not bound`` — the bare
raise at the end of ``_execute_case``'s branch chain — after passing straight
through the applicability gate that should have stopped them.

The gate let them through because it could not see their declaration. Both
families declare ``applicability.fires_when_all: [{has_tool_calling: true}]`` in
``rubric/probe_mappings/<family>.yaml``, the Option C form adopted in
``rubric/applicability_policy.yaml`` under UW + CEO co-sign (BEAAA-225). But
``_declared_applicability_flag`` read only ``FamilySpec.applicability_flag``, and
neither family has an ``evals.aiuc_path_b`` SPEC to carry that field. A declared
gate in the wrong file read as no gate at all, so a conditional family resolved
as unconditional and ran at a target that declares no tool surface.

That is the same class of defect as BEAAA-24612 F6 one level down: there the
production path ignored a gate the eval library honored, here it ignores a gate
the rubric declares. The difference in outcome is only luck about which branch
the row happened to reach — F6's row found an adapter and returned a fabricated
PASS, these rows found none and crashed.

The mutant at the bottom is what makes the rest load-bearing: it asserts the
verdict actually flips back to the crash when the fallback is removed.
"""
from __future__ import annotations

import pytest

from tools.registered_case_executor import (
    RegisteredCaseExecutor,
    _declared_applicability_flag,
    _mapping_applicability_flag,
    _resolve_applicability,
)

# The two families of the 24. Both are absent from ``FAMILY_MODULES`` and both
# declare ``has_tool_calling`` in their mapping.
UNBOUND_TOOL_FAMILIES = ("tool_misuse_unauthorized_call", "tool_misuse_scope_escape")


class _NoRunner:
    """Stands in for an executor whose runners must never be reached.

    The N/A path is specified to touch neither the subject nor the judge, so a
    gated row that somehow dispatches fails here rather than silently spending a
    call against the target.
    """

    subject = None
    judge = None


def _row(**manifest_entries):
    """A registry row complete enough to reach the unbound-adapter raise.

    ``source_pack`` matters: without it the chain dies at a ``KeyError`` before
    the raise, which would make this test pass for the wrong reason.
    """

    return {
        "capability_manifest": dict(manifest_entries) if manifest_entries else None,
        "source_pack": "rubric/probe_mappings/tool_misuse_unauthorized_call.yaml",
    }


@pytest.mark.parametrize("family", UNBOUND_TOOL_FAMILIES)
def test_mapping_declared_gate_is_discovered(family):
    """The flag comes from the mapping when there is no SPEC to carry it."""

    assert _mapping_applicability_flag(family) == "has_tool_calling"
    assert _declared_applicability_flag(family) == "has_tool_calling"


@pytest.mark.parametrize("family", UNBOUND_TOOL_FAMILIES)
def test_undeclared_tool_surface_is_not_applicable_not_a_crash(family):
    """The end state is N/A with a stated reason, never ``incomplete``.

    The card's rule: ``incomplete`` is not an allowed end state for a row whose
    target exists. A crash inside the executor is how these rows became
    incomplete, so the assertion is on the verdict, not on the exception type.
    """

    outcome = RegisteredCaseExecutor._execute_case(_NoRunner(), family, "case-1", _row())

    assert outcome["verdict"] == "not_applicable"
    assert outcome["score"] is None
    judge = outcome["judge"]
    assert judge["applicable"] is False
    assert judge["applicability_reason"] == "has_tool_calling=absent_capability_manifest"


@pytest.mark.parametrize("family", UNBOUND_TOOL_FAMILIES)
def test_declined_surface_is_distinguishable_from_an_unanswered_one(family):
    """``None`` is not ``False``, and the evidence has to say which it was.

    The card asks for the tri-state to be decided explicitly rather than
    collapsed. Both values gate the row identically — BEAAA-29793 Rule C, no
    absent surface is exercised either way — so the whole of the distinction
    lives in what the evidence claims about provenance. A target that declared
    ``false`` is attested; a manifest that never mentioned the flag is not, and
    recording the second as the first would be a declaration nobody made.
    """

    declined = _resolve_applicability(family, _row(has_tool_calling=False))
    unanswered = _resolve_applicability(family, _row(has_prose_output=True))

    assert (declined.applicable, unanswered.applicable) == (False, False)
    assert declined.attested is True
    assert unanswered.attested is False
    assert declined.reason == "has_tool_calling=False"
    assert unanswered.reason == "has_tool_calling=None"


@pytest.mark.parametrize("family", UNBOUND_TOOL_FAMILIES)
def test_declared_tool_surface_still_reaches_the_unbound_adapter(family):
    """The gate is not a way to make a missing adapter disappear.

    These two families still have no adapter bound. A target that declares a
    tool surface is owed a probe, and there is none to run, so the raise must
    survive — routing an applicable row to N/A would convert a real coverage gap
    into a clean-looking report, which is the failure this whole card exists to
    stop being possible.
    """

    with pytest.raises(RuntimeError, match="registered case adapter not bound"):
        RegisteredCaseExecutor._execute_case(
            _NoRunner(), family, "case-1", _row(has_tool_calling=True)
        )


def test_multi_condition_gate_is_left_unconditional():
    """A conjunction the shared resolver cannot express must not be guessed.

    Honoring half of a two-flag gate would be a second applicability semantics
    in the tree, which is the BEAAA-29793 defect the shared resolver exists to
    have closed. Returning ``None`` keeps today's behavior for such a family.
    """

    from tools import registered_case_executor as rce

    rce._MAPPING_FLAG_CACHE.pop("_beaaa30934_probe", None)
    try:
        assert rce._mapping_applicability_flag("_beaaa30934_probe") is None
    finally:
        rce._MAPPING_FLAG_CACHE.pop("_beaaa30934_probe", None)


@pytest.mark.parametrize("family", UNBOUND_TOOL_FAMILIES)
def test_gate_removal_mutant_restores_the_crash(family, monkeypatch):
    """Ruling E: coverage that survives deletion of the guard is inert."""

    from tools import registered_case_executor as rce

    monkeypatch.setattr(rce, "_mapping_applicability_flag", lambda test_id: None)

    with pytest.raises(RuntimeError, match="registered case adapter not bound"):
        rce.RegisteredCaseExecutor._execute_case(
            _NoRunner(), family, "case-1", _row()
        )
