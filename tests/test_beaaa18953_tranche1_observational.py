from __future__ import annotations

import json

import pytest

from tools.active_test_registry import build_registry
from tools.tranche1_observational import (
    ALLOWLIST,
    CONFIG_SHA256,
    DISCLOSURE,
    EVALUATOR_SHA256,
    INVENTORY_SHA256,
    MAPPING_SHA256,
    ObservationalContractError,
    build_case_record,
    normalize_outcome,
    rank_families,
)


def _pins(family):
    return {
        "inventory_sha256": INVENTORY_SHA256,
        "mapping_sha256": MAPPING_SHA256[family],
        "evaluator_sha256": EVALUATOR_SHA256[family],
        "configuration_sha256": CONFIG_SHA256,
        "corpus_sha256": INVENTORY_SHA256,
        "commit_sha256": "e06cb09df3d0a490816eace227b083b97665949d",
    }


def _record(family, case, raw=None, disposition=None):
    runs = [
        {
            "replicate_index": index,
            "seed": f"s{index}",
            "raw_result": raw,
            "disposition": disposition,
            "evidence_uri": f"evidence://x/{case}/{index}",
            "evidence_sha256": f"h{index}",
            "started_at": "2026-07-29T00:00:00Z",
            "completed_at": "2026-07-29T00:00:01Z",
        }
        for index in range(5)
    ]
    return build_case_record(
        family_id=family,
        case_id=case,
        replicates=runs,
        pins=_pins(family),
        target_authorization_ref="auth:tenant:scan",
    )


def test_outcome_normalization_and_fail_closed_semantics():
    assert [normalize_outcome(raw_result=n) for n in range(5)] == [
        "FAIL", "FAIL", "FAIL", "PASS", "PASS"
    ]
    assert normalize_outcome(disposition="not_applicable") == "NOT_APPLICABLE"
    with pytest.raises(ObservationalContractError):
        normalize_outcome(raw_result=5)
    with pytest.raises(ObservationalContractError):
        normalize_outcome(raw_result=4, disposition="INCOMPLETE")


def test_registry_promotes_exactly_36_without_count_or_worker_collateral(monkeypatch):
    monkeypatch.delenv("BACKBOND_TRANCHE1_OBSERVATIONAL", raising=False)
    rows = build_registry()
    selected = [
        row for row in rows
        if row.get("runner") == "content_candidate_worker"
        and row.get("scoring_active") is True
    ]
    assert len(selected) == 36
    assert {row["test_id"] for row in selected} == set(ALLOWLIST)
    assert {row["k_replicates"] for row in selected} == {5}
    assert {row["effective_underwriting_weight"] for row in selected} == {0.0}
    assert {row["underwriting_active"] for row in selected} == {False}
    assert {row["observational_disclosure"] for row in selected} == {DISCLOSURE}
    assert sum(row.get("content_candidate_inventory") is True for row in rows) == 2000


def test_single_config_rollback_restores_only_36(monkeypatch):
    monkeypatch.setenv("BACKBOND_TRANCHE1_OBSERVATIONAL", "off")
    rows = build_registry()
    candidates = [row for row in rows if row.get("content_candidate_inventory")]
    worker_candidates = [
        row for row in candidates if row.get("runner") == "content_candidate_worker"
    ]
    assert len(candidates) == 2000
    assert len(worker_candidates) == 1782
    assert not [row for row in worker_candidates if row.get("scoring_active") is True]
    assert {row["reporting_status"] for row in worker_candidates} == {
        "reported_not_scored"
    }


def test_n5_minimum_aggregation_and_ranking_ties():
    family_a, family_b, family_c = sorted(ALLOWLIST)
    records = [
        _record(family_a, "a-pass", 4),
        _record(family_a, "a-fail", 2),
        _record(family_b, "b-pass", 3),
        _record(family_b, "b-fail", 0),
        _record(family_c, "c-na", disposition="NOT_APPLICABLE"),
    ]
    ranked = rank_families(records)
    assert [row["family_id"] for row in ranked] == [family_a, family_b, family_c]
    assert [row["rank"] for row in ranked] == [1, 2, None]
    assert ranked[2]["success_rate"] is None
    assert ranked[2]["counts"]["NOT_APPLICABLE"] == 1
    with pytest.raises(ObservationalContractError):
        build_case_record(
            family_id=family_a,
            case_id="short",
            replicates=[{"raw_result": 4}] * 4,
            pins=_pins(family_a),
            target_authorization_ref="auth",
        )


def test_observational_mutation_cannot_change_legacy_bytes():
    legacy = {
        "are_total": 88,
        "pillar_scores": {"ROBUSTNESS": 22},
        "band": "A",
        "eligibility": "eligible",
        "premium": 10,
        "published_countable_total": 2201,
    }
    before = json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()
    for raw in range(5):
        rank_families([_record(sorted(ALLOWLIST)[0], f"case-{raw}", raw)])
        assert json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode() == before
    for disposition in ("NOT_APPLICABLE", "INVALID", "INCOMPLETE"):
        rank_families([
            _record(sorted(ALLOWLIST)[0], f"case-{disposition}", disposition=disposition)
        ])
        assert json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode() == before
