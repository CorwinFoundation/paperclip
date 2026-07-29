from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from api.handlers import quote_handler
from pricing.quote import Rates
from rubric.bands import assign_band, band_to_dict
from underwriting import admittance
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import load_candidate_inventory
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
    authorize_evidence_uri,
    validate_rollback,
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
        tenant_id="tenant-a",
        audience="internal_observational",
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


def test_n5_minimum_aggregation_and_complete_roster_ranking(monkeypatch):
    monkeypatch.setenv(
        "BACKBOND_RUNTIME_COMMIT_SHA",
        "e06cb09df3d0a490816eace227b083b97665949d",
    )
    monkeypatch.setenv(
        "BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA",
        "e06cb09df3d0a490816eace227b083b97665949d",
    )
    family_a, family_b, family_c = sorted(ALLOWLIST)
    rosters = {
        family: sorted(
            str(row["stable_case_id"])
            for row in load_candidate_inventory()
            if row["family_id"] == family
        )
        for family in ALLOWLIST
    }
    records = []
    for case_id in rosters[family_a]:
        records.append(_record(family_a, case_id, 4))
    for case_id in rosters[family_b]:
        records.append(_record(family_b, case_id, 3))
    for case_id in rosters[family_c]:
        records.append(_record(family_c, case_id, disposition="NOT_APPLICABLE"))
    ranked = rank_families(records)
    assert [row["family_id"] for row in ranked] == [family_a, family_b, family_c]
    assert [row["rank"] for row in ranked] == [1, 2, None]
    assert ranked[2]["success_rate"] is None
    assert ranked[2]["counts"]["NOT_APPLICABLE"] == 12
    with pytest.raises(ObservationalContractError):
        build_case_record(
            family_id=family_a,
            case_id="short",
            replicates=[{"raw_result": 4}] * 4,
            pins=_pins(family_a),
            target_authorization_ref="auth",
            tenant_id="tenant-a",
            audience="internal_observational",
        )


def test_partial_or_duplicate_family_is_insufficient_evidence(monkeypatch):
    monkeypatch.setenv(
        "BACKBOND_RUNTIME_COMMIT_SHA",
        "e06cb09df3d0a490816eace227b083b97665949d",
    )
    monkeypatch.setenv(
        "BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA",
        "e06cb09df3d0a490816eace227b083b97665949d",
    )
    family = sorted(ALLOWLIST)[0]
    result = rank_families([_record(family, "partial", 4)])
    assert len(result) == 3
    assert {row["ranking_status"] for row in result} == {"INSUFFICIENT_EVIDENCE"}
    assert {row["rank"] for row in result} == {None}


def test_observational_mutation_is_byte_isolated_from_production_consumers(monkeypatch):
    monkeypatch.setenv(
        "BACKBOND_RUNTIME_COMMIT_SHA",
        "e06cb09df3d0a490816eace227b083b97665949d",
    )
    monkeypatch.setenv(
        "BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA",
        "e06cb09df3d0a490816eace227b083b97665949d",
    )
    score = {
        "bmi": 78,
        "are_total": 78,
        "band": band_to_dict(assign_band(78)),
        "applicability_record": {
            "measured_dims": ["P2.1", "P2.3", "P2.4", "P2.5", "P2.6", "P3.1"],
            "na_dims": [],
        },
        "system_prompt_kind": "hardened",
    }
    customer = {
        "sector": "ENT_SAAS",
        "annual_revenue_usd": 500_000_000,
        "limit": {"per_claim_usd": 2_000_000, "aggregate_usd": 5_000_000},
    }
    rates = Rates.load(Path(__file__).resolve().parents[1] / "pricing" / "rates.yaml")
    quoted_at = datetime(2026, 7, 29, tzinfo=timezone.utc)
    score_bytes = json.dumps(score, sort_keys=True, separators=(",", ":")).encode()
    admittance_bytes = json.dumps(
        admittance.evaluate(score).to_dict(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    quote_bytes = json.dumps(
        quote_handler(
            {"score": score, "customer": customer},
            rates=rates,
            quoted_at=quoted_at,
        )[1],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    for raw in range(5):
        rank_families([_record(sorted(ALLOWLIST)[0], f"case-{raw}", raw)])
        assert json.dumps(score, sort_keys=True, separators=(",", ":")).encode() == score_bytes
        assert json.dumps(
            admittance.evaluate(score).to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode() == admittance_bytes
        assert json.dumps(
            quote_handler(
                {"score": score, "customer": customer},
                rates=rates,
                quoted_at=quoted_at,
            )[1],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode() == quote_bytes
    for disposition in ("NOT_APPLICABLE", "INVALID", "INCOMPLETE"):
        rank_families([
            _record(sorted(ALLOWLIST)[0], f"case-{disposition}", disposition=disposition)
        ])
        assert json.dumps(score, sort_keys=True, separators=(",", ":")).encode() == score_bytes
        assert json.dumps(
            admittance.evaluate(score).to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode() == admittance_bytes
        assert json.dumps(
            quote_handler(
                {"score": score, "customer": customer},
                rates=rates,
                quoted_at=quoted_at,
            )[1],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode() == quote_bytes


def test_inherited_authorization_and_evidence_uri_access(monkeypatch):
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", "e06cb09df3d0a490816eace227b083b97665949d")
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", "e06cb09df3d0a490816eace227b083b97665949d")
    record = _record(sorted(ALLOWLIST)[0], "case", 4)
    uri = "evidence://job/family/case.json"
    assert authorize_evidence_uri(
        record, uri, tenant_id="tenant-a", audience="internal_observational"
    ) == uri
    for tenant, audience in (
        ("tenant-b", "internal_observational"),
        ("tenant-a", "customer"),
        ("", "internal_observational"),
    ):
        with pytest.raises(ObservationalContractError):
            authorize_evidence_uri(record, uri, tenant_id=tenant, audience=audience)


def test_ranking_is_minimized_and_rejects_sensitive_alias_partial_collision(monkeypatch):
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", "e06cb09df3d0a490816eace227b083b97665949d")
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", "e06cb09df3d0a490816eace227b083b97665949d")
    record = _record(sorted(ALLOWLIST)[0], "case", 4)
    encoded = json.dumps(rank_families([record]))
    for forbidden in ("evidence_uri", "target_authorization_ref", "tenant-a", "replicates", "pins"):
        assert forbidden not in encoded
    for mutation in (
        {**record, "family": record["family_id"]},
        {key: value for key, value in record.items() if key != "outcome"},
        {**record, "evidence_uri": "evidence://secret"},
        {**record, "family_id": "tool_misuse_param_tamper_alias"},
    ):
        with pytest.raises(ObservationalContractError):
            rank_families([mutation])


def test_rollback_refuses_all_material_drift_and_retains_evidence(monkeypatch):
    sha = "e06cb09df3d0a490816eace227b083b97665949d"
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", sha)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", sha)
    records = []
    for family in sorted(ALLOWLIST):
        roster = sorted(
            str(row["stable_case_id"])
            for row in load_candidate_inventory()
            if row["family_id"] == family
        )
        records.extend(_record(family, case, 4) for case in roster)
    assert validate_rollback(records) == {
        "rollback_version": "reported_not_scored/v1",
        "retained_evidence_records": 36,
        "evidence_deleted": False,
    }
    for pin in ("mapping_sha256", "evaluator_sha256", "corpus_sha256", "configuration_sha256", "commit_sha256"):
        drifted = [dict(row) for row in records]
        drifted[0] = {**drifted[0], "pins": {**drifted[0]["pins"], pin: "0" * 64}}
        with pytest.raises(ObservationalContractError):
            validate_rollback(drifted)
    for bad_records in (records[:-1], records + [records[0]]):
        with pytest.raises(ObservationalContractError):
            validate_rollback(bad_records)
