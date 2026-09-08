"""BEAAA-29793 Rule A: `undeclared` is not `false`.

CTO ruling on BEAAA-29793 (ADR, four rules). This file pins Rule A only.

Rule A separates three epistemic states that `run_family` previously collapsed
into one boolean: the target declared the capability, the target declared it
absent, and the target never answered. Rules B (attestation record) and C
(`not_applicable_attested` / `not_applicable_unattested`) are held pending a CTO
ruling on transport: `governance_attestations` has zero readers under `evals/`,
so with no channel every outcome would be `unattested` by construction and the
distinction would not be testable.

Rule C's execution half is already binding and is pinned here: the tri-state must
not change what runs. An undeclared family is gated exactly as a declared-false
family is, so BEAAA-29788 stands and no absent surface is exercised.
"""

import pytest

from evals.aiuc_path_b import applicability, base, manifest
from evals.aiuc_path_b import mcp_tool_integrity as mcp

FLAG = "has_mcp_tool_integration"


def _gated_families():
    """Every family that consults the capability manifest, discovered not listed.

    BEAAA-29793: this is deliberately dynamic. At the BEAAA-29788 base only
    mcp_tool_integrity is gated, but PR #308 (landed on master as aeaed3b2) gates
    human_escalation_flagging and customer_defined_risk_pack too, and BEAAA-29742
    gates further families. A hardcoded list would silently stop covering Rule A
    for every family added after this file was written, which is the same
    under-coverage failure the ticket is about.
    """
    out = []
    for name, module in sorted(manifest.FAMILY_MODULES.items()):
        if module.SPEC.applicability_flag is not None:
            out.append(pytest.param(module, id=name))
    assert out, "no gated family found; Rule A has nothing to pin"
    return out


GATED = _gated_families()

_EXECUTION_KEYS = (
    "applicable",
    "fired_probe_ledger",
    "canary_registration",
    "skipped_probe_ledger",
    "skipped_canary_registration",
    "case_results",
)


def _run(flags):
    return base.run_family(mcp.SPEC, mcp.compliant_policy, seed=0, architecture_flags=flags)


def test_undeclared_flag_is_not_reported_as_a_declined_capability():
    """The defect, stated directly: an empty manifest used to emit `FLAG=False`."""
    ev = _run({})
    assert ev["applicability"]["declaration"] == base.UNDECLARED
    assert ev["applicability_reason"] == f"{FLAG}=None"
    # The whole point of Rule A. Evidence must not assert a declaration the
    # target never made.
    assert "False" not in ev["applicability_reason"]


def test_absent_manifest_is_distinguishable_from_an_empty_one():
    """`flags or {}` collapsed these two. They are different facts about the scan."""
    no_manifest = _run(None)
    empty_manifest = _run({})
    assert no_manifest["applicability"]["detail"] == applicability.DETAIL_ABSENT_MANIFEST
    assert empty_manifest["applicability"]["detail"] != applicability.DETAIL_ABSENT_MANIFEST
    # Both are undeclared; only the reason for the silence differs.
    assert no_manifest["applicability"]["declaration"] == base.UNDECLARED
    assert empty_manifest["applicability"]["declaration"] == base.UNDECLARED


def test_declared_false_keeps_its_declaration_and_its_evidence_string():
    ev = _run({FLAG: False})
    assert ev["applicability"]["declaration"] == base.DECLARED_FALSE
    assert ev["applicability_reason"] == f"{FLAG}=False"
    assert ev["applicability"]["detail"] != applicability.DETAIL_ABSENT_MANIFEST
    assert ev["applicable"] is False


def test_declared_true_is_unchanged():
    ev = _run({FLAG: True})
    assert ev["applicability"]["declaration"] == base.DECLARED_TRUE
    assert ev["applicability_reason"] == f"{FLAG}=True"
    assert ev["applicable"] is True


def test_ungated_family_is_not_gated_and_reads_always():
    """A family with no applicability_flag never consults the manifest."""
    spec = mcp.SPEC
    assert spec.applicability_flag == FLAG, "fixture drifted; this test needs a gated family"
    resolution = applicability.resolve(_UngatedSpec().applicability_flag, None)
    applicable, reason, declaration = (
        resolution.applicable,
        resolution.reason,
        resolution.declaration,
    )
    assert applicable is True
    assert reason == "always"
    assert declaration == base.NOT_GATED


class _UngatedSpec:
    applicability_flag = None


@pytest.mark.parametrize("undeclared_flags", [None, {}, {"has_tool_calling": True}])
def test_rule_c_execution_is_identical_for_undeclared_and_declared_false(undeclared_flags):
    """Rule C: the tri-state changes reporting, never execution.

    Every undeclared shape must exercise exactly what a declared-false target
    exercises, which under BEAAA-29788 is nothing. Only the two declaration keys
    and the reason string may differ.
    """
    undeclared = _run(undeclared_flags)
    declared_false = _run({FLAG: False})
    for key in _EXECUTION_KEYS:
        assert undeclared[key] == declared_false[key], f"{key} diverged"
    assert undeclared["fired_probe_ledger"] == []
    assert undeclared["canary_registration"] == []
    assert all(r["verdict"] == base.NA for r in undeclared["case_results"])


def test_evidence_fingerprint_covers_the_declaration():
    """A tri-state that does not reach the sha256 is not carried in evidence."""
    assert _run({}) ["sha256"] != _run({FLAG: False})["sha256"]
    assert _run(None)["sha256"] != _run({})["sha256"]


def test_third_party_flags_do_not_satisfy_the_gate():
    """The live producer state: `{"has_tool_calling": True}` and nothing else."""
    ev = _run({"has_tool_calling": True})
    assert ev["applicability"]["declaration"] == base.UNDECLARED
    assert ev["applicable"] is False


# ---- Rule A across every gated family, not just the one that existed at this base ----
@pytest.mark.parametrize("module", GATED)
def test_rule_a_holds_for_every_gated_family(module):
    """Rule A is a property of the resolver, so it must hold for every gated family.

    Guards the case the ticket is actually about: a family gated by a flag that no
    producer emits. Undeclared must never be rendered as a declared decline, no
    matter which family is asking.
    """
    flag = module.SPEC.applicability_flag
    spec, policy = module.SPEC, module.compliant_policy

    undeclared = base.run_family(spec, policy, seed=0, architecture_flags={})
    assert undeclared["applicability"]["declaration"] == base.UNDECLARED
    assert undeclared["applicability_reason"] == f"{flag}=None"
    assert undeclared["applicable"] is False

    declined = base.run_family(spec, policy, seed=0, architecture_flags={flag: False})
    assert declined["applicability"]["declaration"] == base.DECLARED_FALSE
    assert declined["applicability_reason"] == f"{flag}=False"

    declared = base.run_family(spec, policy, seed=0, architecture_flags={flag: True})
    assert declared["applicability"]["declaration"] == base.DECLARED_TRUE
    assert declared["applicable"] is True

    # Rule C: the tri-state must not change what runs, for any gated family.
    for key in _EXECUTION_KEYS:
        assert undeclared[key] == declined[key], f"{module.SPEC.name}: {key} diverged"


@pytest.mark.parametrize("module", GATED)
def test_no_producer_in_tree_satisfies_any_gate(module):
    """The live defect, pinned per family.

    tools/tranche1_canary_runner.py is the only capability-manifest producer and
    emits {"has_tool_calling": True}. No gated family is satisfied by it, so every
    gated family is inert for every target today. This test is expected to change
    when a producer starts declaring these flags; that is the point.
    """
    live_producer_manifest = {"has_tool_calling": True}
    ev = base.run_family(
        module.SPEC, module.compliant_policy, seed=0, architecture_flags=live_producer_manifest
    )
    assert ev["applicability"]["declaration"] == base.UNDECLARED
    assert ev["applicable"] is False
