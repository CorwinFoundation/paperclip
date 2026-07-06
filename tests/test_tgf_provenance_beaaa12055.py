"""Provenance emission on tool_grounded_factuality deployment_measured rows (BEAAA-12055).

Follow-up to BEAAA-12028; conforms to the CO-SIGNED ADR-0053 §4 consumption gate
(`underwriting/admittance.py` `_tgf_full_limit_eligible`), NOT the stale issue-description
draft. The gate grants a full-limit business-factual bind ONLY when, fail-closed, the
`behavioral_integrity … tool_grounded_factuality` dimension block carries provenance with:

    source == "deployment_representative"
    task_set_kind == "representative_deployment"
    agent_ref present AND == the submission's insured_agent_ref

and the Condition-2 misattribution exclusion sunsets only at verifier_version >= 2.

These tests assert the emitter stamps that vocabulary on every deployment_measured row,
surfaces the load-bearing copy (+ verifier_version) on the dimension block the gate reads,
pins agent_ref for submission runs, fail-closes to the reference fixture by default, and
stays deterministic. A local reimplementation of the gate predicate (`_gate_full_limit_ok`,
byte-for-byte the ADR-0053 §4 checks) proves the emitted block actually clears the gate —
the gate itself lives on a separate branch and cannot be imported here. Deterministic; no seeds/network.
"""
from __future__ import annotations

from typing import Any, Mapping

import pytest

from evals.perils.tool_grounded_factuality.pilot_live import (
    PROVENANCE_SOURCE_DEPLOYMENT_REPRESENTATIVE,
    PROVENANCE_SOURCE_REFERENCE_FIXTURE,
    PROVENANCE_TASKSET_KIND_REFERENCE,
    PROVENANCE_TASKSET_KIND_REPRESENTATIVE,
    REFERENCE_FIXTURE_PROVENANCE,
    VERIFIER_VERSION,
    Provenance,
    deployment_representative_provenance,
    run_tool_grounded_factuality_task,
)
from evals.perils.tool_grounded_factuality.tasks import (
    TOOL_GROUNDED_FACTUALITY_TASKS,
    faithful_agent_policy,
    session_factory_for,
)
from evals.perils.tool_grounded_factuality.verifier import evidence_refs_for_run

TASK = TOOL_GROUNDED_FACTUALITY_TASKS[0]
K = 5
INSURED_AGENT_REF = "insured-agent-sha256:abc123"


def _run(provenance=None):
    kwargs = {} if provenance is None else {"provenance": provenance}
    return run_tool_grounded_factuality_task(
        TASK, session_factory_for(faithful_agent_policy), k=K, **kwargs
    )


def _submission_provenance():
    return deployment_representative_provenance(INSURED_AGENT_REF)


# --- Local mirror of the live gate predicate (ADR-0053 §4 / admittance.py) -----
_GATE_SOURCE = "deployment_representative"
_GATE_TASKSET_KIND = "representative_deployment"


def _gate_full_limit_ok(prov: Mapping[str, Any], insured_agent_ref: str | None) -> bool:
    """Byte-for-byte the `_tgf_full_limit_eligible` provenance clause — fail-closed ALL."""
    if prov.get("source") != _GATE_SOURCE:
        return False
    if prov.get("task_set_kind") != _GATE_TASKSET_KIND:
        return False
    agent_ref = prov.get("agent_ref")
    if not isinstance(agent_ref, str) or not agent_ref:
        return False
    if not isinstance(insured_agent_ref, str) or not insured_agent_ref:
        return False
    return agent_ref == insured_agent_ref


def _dim_block(result_dict: Mapping[str, Any]) -> Mapping[str, Any]:
    return result_dict["behavioral_integrity"]["dimensions"]["tool_grounded_factuality"]


# --- Default is the reference fixture, fail-closed (NOT full-limit eligible) ---
def test_default_run_stamps_reference_fixture_provenance():
    d = _run().to_dict()
    prov = d["provenance"]
    assert prov["source"] == PROVENANCE_SOURCE_REFERENCE_FIXTURE
    assert prov["task_set_kind"] == PROVENANCE_TASKSET_KIND_REFERENCE
    assert "agent_ref" not in prov  # the fixture characterizes no customer
    # The gate must NOT grant full-limit on the reference fixture, even given an insured ref.
    assert not _gate_full_limit_ok(prov, INSURED_AGENT_REF)
    assert not _gate_full_limit_ok(_dim_block(d)["provenance"], INSURED_AGENT_REF)


def test_default_provenance_rides_every_deployment_measured_row():
    d = _run().to_dict()
    rows = d["evidence_refs"] + d["evidence_artifacts"]
    assert rows, "expected evidence rows"
    for row in rows:
        assert row["evidence_grade"] == "deployment_measured"
        assert row["provenance"]["source"] == PROVENANCE_SOURCE_REFERENCE_FIXTURE
        assert row["provenance"]["task_set_kind"] == PROVENANCE_TASKSET_KIND_REFERENCE


# --- Submission run earns full-limit; the emitted block CLEARS the live gate ----
def test_submission_run_provenance_clears_the_gate():
    d = _run(_submission_provenance()).to_dict()
    prov = d["provenance"]
    assert prov["source"] == PROVENANCE_SOURCE_DEPLOYMENT_REPRESENTATIVE
    assert prov["task_set_kind"] == PROVENANCE_TASKSET_KIND_REPRESENTATIVE
    assert prov["agent_ref"] == INSURED_AGENT_REF
    # The load-bearing check: the dimension block the gate reads clears full-limit.
    assert _gate_full_limit_ok(_dim_block(d)["provenance"], INSURED_AGENT_REF)
    # And every evidence row carries the same eligible vocabulary.
    for row in d["evidence_refs"] + d["evidence_artifacts"]:
        assert _gate_full_limit_ok(row["provenance"], INSURED_AGENT_REF)


def test_submission_run_fails_gate_on_agent_ref_mismatch():
    # agent_ref is the identity pin: a different insured agent must NOT clear the gate.
    d = _run(_submission_provenance()).to_dict()
    assert not _gate_full_limit_ok(_dim_block(d)["provenance"], "some-other-insured-agent")


def test_agent_ref_is_required_for_submission_provenance():
    # A submission run cannot silently degrade to an identity-less (fail-closed) block.
    with pytest.raises(ValueError):
        deployment_representative_provenance("")
    with pytest.raises(ValueError):
        deployment_representative_provenance("   ")


# --- verifier_version drives Condition 2 (misattribution exclusion), not a flag -
def test_dimension_block_carries_verifier_version_v1():
    d = _run(_submission_provenance()).to_dict()
    block = _dim_block(d)
    assert block["verifier_version"] == VERIFIER_VERSION == 1
    assert block["evidence_grade"] == "deployment_measured"
    # v1 < 2 => the gate's misattribution exclusion still applies (documented contract).
    assert block["verifier_version"] < 2


# --- behavioral_integrity stays reported-not-scored (ADR-0045 invariant) --------
def test_behavioral_integrity_block_is_reported_not_scored():
    d = _run(_submission_provenance()).to_dict()
    bi = d["behavioral_integrity"]
    assert bi["scored"] is False
    assert bi["family"] == "behavioral_integrity"
    assert "tool_grounded_factuality" in bi["dimensions"]


# --- Provenance is confined to deployment_measured rows -----------------------
def test_provenance_only_on_deployment_measured_rows():
    run_result = {"scenario_id": "s1", "verdicts": {"rp.a": True, "rp.b": False}}
    prov = _submission_provenance().to_dict()
    measured = evidence_refs_for_run(
        run_result, artifact_prefix="p", evidence_grade="deployment_measured", provenance=prov
    )
    assert all("provenance" in r for r in measured)
    indicative = evidence_refs_for_run(
        run_result, artifact_prefix="p", evidence_grade="indicative", provenance=prov
    )
    assert all("provenance" not in r for r in indicative)


# --- Adding provenance does not perturb tamper-evidence reproducibility -------
def test_provenance_addition_preserves_determinism():
    a = _run(_submission_provenance())
    b = _run(_submission_provenance())
    assert a.ledger_fingerprints == b.ledger_fingerprints
    assert [x["sha256"] for x in a.evidence_artifacts] == [
        x["sha256"] for x in b.evidence_artifacts
    ]


# --- to_dict omits absent optional fields (clean fail-closed reference block) ---
def test_reference_provenance_to_dict_omits_agent_ref():
    assert set(REFERENCE_FIXTURE_PROVENANCE.to_dict()) == {"source", "task_set_kind"}
    p = Provenance(source="deployment_representative", task_set_kind="representative_deployment",
                   agent_ref="a1", task_set_ref="catalog/v1")
    assert p.to_dict() == {
        "source": "deployment_representative",
        "task_set_kind": "representative_deployment",
        "agent_ref": "a1",
        "task_set_ref": "catalog/v1",
    }
