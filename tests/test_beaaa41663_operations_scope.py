import pytest

from api.operations_scope import ScopeValidationError, validate_scope_selection


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
        {"case_id": "a", "scope_id": "scope-a", "targeted_content_approved": True},
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
