"""Provenance emission on tool_grounded_factuality deployment_measured rows (BEAAA-12055).

Follow-up to BEAAA-12028. The ADR-0053 ADM.R0.FACT full-limit gate consumes a
per-submission `provenance` sub-block on the deployment_measured evidence row:

    provenance = {source, task_set, claim_level_value_entity_binding}

These tests assert the Scanner-side emitter stamps that sub-block on EVERY
deployment_measured row (verdict rows + tamper-evident artifact rows) and on the
run result; that the fail-closed default is the reference fixture (not full-limit
eligible); that an explicit submission run earns the submission_agent provenance
the gate requires; that v1 never claims per-claim value<->entity binding; and that
provenance is confined to deployment_measured rows. Deterministic — no seeds/network.
"""
from __future__ import annotations

from evals.perils.tool_grounded_factuality.pilot_live import (
    PROVENANCE_SOURCE_REFERENCE_FIXTURE,
    PROVENANCE_SOURCE_SUBMISSION_AGENT,
    PROVENANCE_TASKSET_REFERENCE,
    PROVENANCE_TASKSET_REPRESENTATIVE,
    REFERENCE_FIXTURE_PROVENANCE,
    SUBMISSION_AGENT_PROVENANCE,
    Provenance,
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

# Exactly the three keys the ADR-0053 gate reads (`_tool_grounded_provenance`).
_PROVENANCE_KEYS = {"source", "task_set", "claim_level_value_entity_binding"}


def _run(provenance=None):
    kwargs = {} if provenance is None else {"provenance": provenance}
    return run_tool_grounded_factuality_task(
        TASK, session_factory_for(faithful_agent_policy), k=K, **kwargs
    )


# --- Default is the reference fixture, fail-closed (NOT full-limit eligible) ---
def test_default_run_stamps_reference_fixture_provenance():
    d = _run().to_dict()
    prov = d["provenance"]
    assert set(prov) == _PROVENANCE_KEYS
    assert prov["source"] == PROVENANCE_SOURCE_REFERENCE_FIXTURE
    assert prov["task_set"] == PROVENANCE_TASKSET_REFERENCE
    assert prov["claim_level_value_entity_binding"] is False
    # Reference fixture must NOT satisfy the gate's full-limit predicate shape.
    assert not (
        prov["source"] == PROVENANCE_SOURCE_SUBMISSION_AGENT
        and prov["task_set"] == PROVENANCE_TASKSET_REPRESENTATIVE
    )


def test_default_provenance_rides_every_deployment_measured_row():
    d = _run().to_dict()
    rows = d["evidence_refs"] + d["evidence_artifacts"]
    assert rows, "expected evidence rows"
    for row in rows:
        assert row["evidence_grade"] == "deployment_measured"
        assert set(row["provenance"]) == _PROVENANCE_KEYS
        assert row["provenance"]["source"] == PROVENANCE_SOURCE_REFERENCE_FIXTURE
        assert row["provenance"]["task_set"] == PROVENANCE_TASKSET_REFERENCE


# --- Explicit submission run earns the full-limit-eligible provenance ---------
def test_submission_run_earns_full_limit_provenance():
    d = _run(SUBMISSION_AGENT_PROVENANCE).to_dict()
    for row in d["evidence_refs"] + d["evidence_artifacts"] + [d]:
        prov = row["provenance"]
        assert prov["source"] == PROVENANCE_SOURCE_SUBMISSION_AGENT
        assert prov["task_set"] == PROVENANCE_TASKSET_REPRESENTATIVE
        # Gate Condition 1 shape (ADR-0053 §2.1) — submission agent + representative set.
        assert (
            prov["source"] == PROVENANCE_SOURCE_SUBMISSION_AGENT
            and prov["task_set"] == PROVENANCE_TASKSET_REPRESENTATIVE
        )


# --- v1 never claims per-claim value<->entity binding (Condition 2 exclusion) --
def test_v1_never_claims_claim_level_binding():
    for prov in (REFERENCE_FIXTURE_PROVENANCE, SUBMISSION_AGENT_PROVENANCE):
        assert prov.claim_level_value_entity_binding is False
    d = _run(SUBMISSION_AGENT_PROVENANCE).to_dict()
    for row in d["evidence_refs"] + d["evidence_artifacts"]:
        assert row["provenance"]["claim_level_value_entity_binding"] is False


# --- Provenance is confined to deployment_measured rows -----------------------
def test_provenance_only_on_deployment_measured_rows():
    run_result = {"scenario_id": "s1", "verdicts": {"rp.a": True, "rp.b": False}}
    prov = SUBMISSION_AGENT_PROVENANCE.to_dict()
    measured = evidence_refs_for_run(
        run_result, artifact_prefix="p", evidence_grade="deployment_measured", provenance=prov
    )
    assert all("provenance" in r for r in measured)
    # An indicative row must NOT carry provenance even if one is passed.
    indicative = evidence_refs_for_run(
        run_result, artifact_prefix="p", evidence_grade="indicative", provenance=prov
    )
    assert all("provenance" not in r for r in indicative)


# --- Adding provenance does not perturb tamper-evidence reproducibility -------
def test_provenance_addition_preserves_determinism():
    a = _run(SUBMISSION_AGENT_PROVENANCE)
    b = _run(SUBMISSION_AGENT_PROVENANCE)
    assert a.ledger_fingerprints == b.ledger_fingerprints
    assert [x["sha256"] for x in a.evidence_artifacts] == [
        x["sha256"] for x in b.evidence_artifacts
    ]


def test_provenance_to_dict_roundtrips_expected_keys():
    p = Provenance(source="submission_agent", task_set="representative_business_factual")
    assert set(p.to_dict()) == _PROVENANCE_KEYS
    assert p.to_dict()["claim_level_value_entity_binding"] is False
