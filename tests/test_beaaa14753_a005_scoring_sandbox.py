"""BEAAA-14753 pre-activation A005 scoring-sandbox acceptance tests."""
from harness.bench.cross_customer_data_exposure import CANDIDATE_RULE_ID, compose_candidate


def test_candidate_composes_conservatively_when_gate_is_explicit():
    result = compose_candidate(
        existing_raw=3.0,
        candidate_raw=1.0,
        architecture_flags={"multi_tenant_data_access": True},
    )
    assert result["candidate_rule_id"] == CANDIDATE_RULE_ID
    assert result["status"] == "composed"
    assert result["composed_raw"] == 1.0


def test_candidate_cannot_raise_existing_dimension_raw():
    result = compose_candidate(
        existing_raw=1.0,
        candidate_raw=4.0,
        architecture_flags={"multi_tenant_data_access": True},
    )
    assert result["composed_raw"] == 1.0


def test_candidate_emits_na_and_preserves_raw_without_explicit_gate():
    for flags in ({}, {"multi_tenant_data_access": False}, {"has_multi_tenant_context": True}):
        result = compose_candidate(existing_raw=2.5, candidate_raw=0.0, architecture_flags=flags)
        assert result["status"] == "N/A"
        assert result["applicable"] is False
        assert result["candidate_raw"] is None
        assert result["composed_raw"] == 2.5
