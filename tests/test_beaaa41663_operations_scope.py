import json
from pathlib import Path

import pytest

from api.operations_scope import (
    ScopeValidationError,
    project_targeted_scope_registry,
    reconcile_scope_execution,
    validate_scope_selection,
)


REGISTRY = (
    {"case_id": "a", "scope_id": "scope-a"},
    {"case_id": "b", "scope_id": "scope-b"},
)


def validate(selection, rows=REGISTRY):
    return validate_scope_selection(
        selection, registry_revision="registry@1", registry_rows=rows
    )


def test_full_requires_exact_manifest_and_is_the_only_fresh_full_score_path():
    scope = validate({
        "registry_revision": "registry@1", "mode": "full",
        "scope_ids": [], "case_manifest": ["b", "a"],
    })
    assert scope["complete"] is True
    assert scope["may_claim_full_score"] is True
    assert scope["unknown_impact_rule"] == "published_full"
    with pytest.raises(ScopeValidationError, match="case_manifest_mismatch"):
        validate({
            "registry_revision": "registry@1", "mode": "full",
            "scope_ids": [], "case_manifest": ["a"],
        })


def test_targeted_is_held_until_every_selected_case_is_approved():
    submitted = {
        "registry_revision": "registry@1", "mode": "targeted",
        "scope_ids": ["scope-a"], "case_manifest": ["a"],
    }
    held = validate(submitted)
    assert held["held"] is True
    assert held["held_reason"] == "targeted_content_not_approved"
    assert held["may_claim_full_score"] is False
    approved = validate(submitted, rows=(
        {"case_id": "a", "scope_id": "scope-a", "targeted_content_approved": True,
         "targeted_supported": True},
    ))
    assert approved["held"] is False
    assert approved["complete"] is False


def test_unknown_scope_registry_mismatch_and_smoke_claims_fail_closed():
    with pytest.raises(ScopeValidationError, match="registry_revision_mismatch"):
        validate({"registry_revision": "old", "mode": "full", "case_manifest": ["a", "b"]})
    with pytest.raises(ScopeValidationError, match="scope_id_unknown"):
        validate({
            "registry_revision": "registry@1", "mode": "targeted",
            "scope_ids": ["missing"], "case_manifest": [],
        })
    smoke = validate({
        "registry_revision": "registry@1", "mode": "smoke",
        "scope_ids": [], "case_manifest": ["a", "b"],
    })
    assert smoke["advisory"] is True
    assert smoke["may_claim_full_score"] is False
    assert smoke["unknown_impact_rule"] is None


def test_targeted_unsupported_case_requires_full_fallback():
    submitted = {
        "registry_revision": "registry@1", "mode": "targeted",
        "scope_ids": ["scope-a"], "case_manifest": ["a"],
    }
    scope = validate(submitted, rows=(
        {"case_id": "a", "scope_id": "scope-a", "targeted_content_approved": True,
         "targeted_supported": False},
    ))
    assert scope["requires_full_fallback"] is True
    assert scope["held_reason"] == "targeted_scope_requires_full_fallback"


def test_targeted_reconciliation_is_not_full_and_fails_closed_on_missing_evidence():
    scope = validate({
        "registry_revision": "registry@1", "mode": "targeted",
        "scope_ids": ["scope-a"], "case_manifest": ["a"],
    }, rows=(
        {"case_id": "a", "scope_id": "scope-a", "targeted_content_approved": True,
         "targeted_supported": True},
    ))
    incomplete = reconcile_scope_execution(scope, [{"case_id": "a", "status": "complete"}])
    assert incomplete["missing_evidence"] == ["a"]
    assert incomplete["complete"] is False
    complete = reconcile_scope_execution(scope, [
        {"case_id": "a", "status": "complete", "evidence_ref": "evidence://a"}
    ])
    assert complete["complete"] is True
    assert complete["may_claim_full_score"] is False


def test_reported_not_scored_pass_fail_is_gradeable_without_numeric_score():
    scope = validate({
        "registry_revision": "registry@1", "mode": "targeted",
        "scope_ids": ["scope-a"], "case_manifest": ["a"],
    }, rows=({"case_id": "a", "scope_id": "scope-a", "targeted_content_approved": True, "targeted_supported": True},))
    result = reconcile_scope_execution(scope, [{
        "case_id": "a", "verdict": "PASS", "reporting_status": "reported_not_scored",
        "score": None, "evidence_ref": "evidence://a",
    }])
    assert result["complete"] is True
    assert result["gradeable"] == ["a"]
    assert result["may_claim_full_score"] is False


def test_released_catalog_policy_projection_is_pinned_and_held_without_founder_decision():
    root = Path(__file__).parents[1]
    catalog = json.loads((root / "tools/probe-registry/active_registry.json").read_text())
    tenant = [row for row in catalog["rows"] if str(row.get("case_id_or_probe_id", "")).startswith("TENANT-")]
    pack = {
        "registry_key": "pack@2", "pack_artifact": {"sha256": "abc123"},
        "catalog": {"registry_revision": "authoritative-probe-registry@1.1.0+source-closure-655dcc6f2ff74f9cb5f32d46b9e5cf8a129c16b5", "family": "cross_customer_data_exposure", "family_revision": "cross_customer_data_exposure@0.2.0-candidate"},
        "cases": {
            "target_specific": [{"case_id": row["case_id_or_probe_id"], "case_revision": "sha256:" + row["stable_id"]} for row in tenant[:6]],
            "mandatory_cross_cutting": [{"case_id": row["case_id_or_probe_id"], "case_revision": "sha256:" + row["stable_id"]} for row in tenant[6:]],
        },
        "supported_target_constraints": {"supported_adapters": ["bench_wrapper"]},
    }
    rows = project_targeted_scope_registry(catalog=catalog, policy={"targeted_packs": {"pack@2": pack}}, policy_revision="policy-rev-2", registry_key="pack@2")
    assert [row["case_id"] for row in rows] == [f"TENANT-{i:02d}" for i in range(1, 9)]
    assert all(row["targeted_supported"] for row in rows)
    assert not any(row["targeted_content_approved"] for row in rows)
    assert all(row["numeric_score_eligible"] is False for row in rows)
    approved = project_targeted_scope_registry(
        catalog=catalog, policy={"targeted_packs": {"pack@2": pack}}, policy_revision="policy-rev-2", registry_key="pack@2",
        founder_decision={"decision": "approved", "approver_role": "Founder", "policy_revision": "policy-rev-2", "registry_key": "pack@2", "pack_artifact_sha256": "abc123"},
    )
    assert all(row["targeted_content_approved"] for row in approved)


def test_projection_rejects_revision_drift_and_synthetic_policy_approval():
    catalog = {"metadata": {"probe_set_version": "registry@1", "source_closure_sha": "closure"}, "rows": [{
        "case_id_or_probe_id": "TENANT-01", "stable_id": "right", "corpus_or_family": "family", "version_pin": "family@1", "worker_runner": "bench_wrapper",
        "categories": {"executable": True, "executable_on_default_runtime": True},
    }]}
    pack = {
        "registry_key": "pack@1", "content_approved": True, "pack_artifact": {"sha256": "artifact"},
        "catalog": {"registry_revision": "registry@1+source-closure-closure", "family": "family", "family_revision": "family@1"},
        "cases": {"target_specific": [{"case_id": "TENANT-01", "case_revision": "sha256:wrong"}], "mandatory_cross_cutting": []},
        "supported_target_constraints": {"supported_adapters": ["bench_wrapper"]},
    }
    with pytest.raises(ScopeValidationError, match="case_revision_mismatch"):
        project_targeted_scope_registry(catalog=catalog, policy={"targeted_packs": {"pack@1": pack}}, policy_revision="p1", registry_key="pack@1")


def test_beaaa41701_v2_artifact_projects_exact_released_rows_but_stays_held():
    root = Path(__file__).parents[1]
    catalog = json.loads((root / "tools/probe-registry/active_registry.json").read_text())
    pack = json.loads((root / "artifacts/BEAAA-41701/targeted-data-boundary-pack-v2.json").read_text())
    policy_pack = dict(pack)
    policy_pack["pack_artifact"] = {"sha256": "4f85eb38626c19f08a8a51ad9a6d939ed3d0db42b18114c3c1e4a9f6764fe8a3"}
    rows = project_targeted_scope_registry(
        catalog=catalog,
        policy={"targeted_packs": {pack["registry_key"]: policy_pack}},
        policy_revision="replacement-policy-revision-pending",
        registry_key=pack["registry_key"],
    )
    assert len(rows) == 8
    assert all(row["targeted_supported"] for row in rows)
    assert not any(row["targeted_content_approved"] for row in rows)
    assert {tuple(row["completion_verdicts"]) for row in rows} == {("PASS", "FAIL")}


@pytest.mark.parametrize("status", ["error", "skipped", "unsupported"])
def test_full_reconciliation_rejects_terminal_but_unscorable_cases(status):
    scope = validate({
        "registry_revision": "registry@1", "mode": "full",
        "scope_ids": [], "case_manifest": ["a", "b"],
    })
    result = reconcile_scope_execution(scope, [
        {"case_id": case_id, "status": status, "evidence_ref": f"evidence://{case_id}"}
        for case_id in ("a", "b")
    ])
    assert result["terminal"] == ["a", "b"]
    assert result["unscorable"] == ["a", "b"]
    assert result["complete"] is False
    assert result["may_claim_full_score"] is False
