from __future__ import annotations

import json
import copy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from api.handlers import quote_handler
from pricing.quote import Rates
from rubric.bands import assign_band, band_to_dict
from underwriting import admittance
from underwriting.automatic_decision import canonical_decision_bytes, decide
from harness.runner import MockRunner
from tools.registered_case_executor import (
    EvidenceStore,
    RegisteredCaseExecutor,
    ScanBudget,
)
from tools.active_test_registry import build_registry
import tools.tranche1_observational as observational
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
    activate_registry_rows,
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
    sha = "e06cb09df3d0a490816eace227b083b97665949d"
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", sha)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", sha)
    monkeypatch.delenv("BACKBOND_TRANCHE1_OBSERVATIONAL", raising=False)
    rows = build_registry()
    active_before = [
        row for row in rows
        if row.get("runner") == "content_candidate_worker"
        and row.get("scoring_active") is True
    ]
    assert len(active_before) == 36
    monkeypatch.setenv("BACKBOND_TRANCHE1_OBSERVATIONAL", "off")
    activate_registry_rows(rows)
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


def test_evidence_store_read_is_tenant_and_audience_gated(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", "e06cb09df3d0a490816eace227b083b97665949d")
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", "e06cb09df3d0a490816eace227b083b97665949d")
    record = _record(sorted(ALLOWLIST)[0], "case", 4)
    store = EvidenceStore(tmp_path)
    uri, _ = store.write(job_id="job", test_id="family", case_id="case", payload={"ok": True})
    assert store.read_observational_evidence(
        uri, record=record, tenant_id="tenant-a", audience="internal_observational"
    )["ok"] is True
    with pytest.raises(ObservationalContractError):
        store.read_observational_evidence(
            uri, record=record, tenant_id="tenant-b", audience="internal_observational"
        )


def test_runtime_executor_evidence_access_denies_cross_tenant_and_customer_audience(
    tmp_path, monkeypatch
):
    sha = "e06cb09df3d0a490816eace227b083b97665949d"
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", sha)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", sha)
    row = next(
        row for row in build_registry()
        if row.get("test_id") == "tool_misuse_param_tamper"
        and row.get("case_id") == "TOOL_MISUSE_PT-01"
    )
    executor = RegisteredCaseExecutor(
        subject=MockRunner(
            "target", lambda _prompt: '{"actual_response_label":"resisted_no_disclosure"}'
        ),
        judge=MockRunner("judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
        tenant_id="tenant-a",
        audience="internal_observational",
    )
    job = type("Job", (), {
        "job_id": "job-authz", "agent_id": "agent-a", "seed": 42,
        "capability_manifest": {"has_tool_calling": True},
    })()
    result = executor(row, job)
    assert result.status == "complete"
    assert executor.read_observational_evidence(
        family_id=row["test_id"], case_id=row["case_id"],
        evidence_uri=result.evidence_ref, tenant_id="tenant-a",
        audience="internal_observational",
    )["test_id"] == row["test_id"]
    for tenant, audience in (
        ("tenant-b", "internal_observational"),
        ("tenant-a", "customer"),
    ):
        with pytest.raises(ObservationalContractError):
            executor.read_observational_evidence(
                family_id=row["test_id"], case_id=row["case_id"],
                evidence_uri=result.evidence_ref, tenant_id=tenant, audience=audience,
            )


def test_ranking_is_minimized_and_rejects_sensitive_alias_partial_collision(monkeypatch):
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", "e06cb09df3d0a490816eace227b083b97665949d")
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", "e06cb09df3d0a490816eace227b083b97665949d")
    record = _record(sorted(ALLOWLIST)[0], "case", 4)
    record["replicates"][0].update({
        "prompt": "SECRET PROMPT",
        "tool_arguments": {"password": "hunter2"},
        "response": "customer@example.test",
        "confidential_artifact": "PRIVATE",
    })
    encoded = json.dumps(rank_families([record]))
    for forbidden in (
        "evidence_uri", "target_authorization_ref", "tenant-a", "replicates", "pins",
        "SECRET PROMPT", "hunter2", "customer@example.test", "PRIVATE",
    ):
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


def test_actual_rollback_refuses_before_mutation_and_retains_files(tmp_path, monkeypatch):
    sha = "e06cb09df3d0a490816eace227b083b97665949d"
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", sha)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", sha)
    monkeypatch.delenv("BACKBOND_TRANCHE1_OBSERVATIONAL", raising=False)
    active = build_registry()
    evidence = tmp_path / "retained.json"
    evidence.write_bytes(b"retained-evidence")
    original_evidence = evidence.read_bytes()
    original_rows = copy.deepcopy(active)
    monkeypatch.setenv("BACKBOND_TRANCHE1_OBSERVATIONAL", "off")

    for runtime, authorized in (("0" * 40, sha), (sha, "1" * 40)):
        monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", runtime)
        monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", authorized)
        candidate = copy.deepcopy(original_rows)
        with pytest.raises(ObservationalContractError):
            activate_registry_rows(candidate)
        assert candidate == original_rows
        assert evidence.read_bytes() == original_evidence

    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", sha)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", sha)
    for mutation in ("missing", "collision"):
        candidate = copy.deepcopy(original_rows)
        selected = [
            row for row in candidate
            if row.get("runner") == "content_candidate_worker"
            and row.get("test_id") in ALLOWLIST
        ]
        if mutation == "missing":
            selected[0]["test_id"] = "unknown_family"
        else:
            selected[0]["case_id"] = selected[1]["case_id"]
        before = copy.deepcopy(candidate)
        with pytest.raises(ObservationalContractError):
            activate_registry_rows(candidate)
        assert candidate == before
        assert evidence.read_bytes() == original_evidence

    rolled_back = copy.deepcopy(original_rows)
    activate_registry_rows(rolled_back)
    disabled = [
        row for row in rolled_back
        if row.get("runner") == "content_candidate_worker"
        and row.get("test_id") in ALLOWLIST
        and row.get("scoring_active") is False
    ]
    assert len(disabled) == 36
    assert evidence.read_bytes() == original_evidence


@pytest.mark.parametrize(
    "pin_name",
    ("CONFIG_SHA256", "INVENTORY_SHA256", "MAPPING_SHA256", "EVALUATOR_SHA256"),
)
def test_actual_rollback_refuses_each_material_file_pin_before_mutation(
    pin_name, tmp_path, monkeypatch
):
    sha = "e06cb09df3d0a490816eace227b083b97665949d"
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", sha)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", sha)
    monkeypatch.delenv("BACKBOND_TRANCHE1_OBSERVATIONAL", raising=False)
    rows = build_registry()
    evidence = tmp_path / "retained.json"
    evidence.write_bytes(b"retained-evidence")
    original = copy.deepcopy(rows)
    monkeypatch.setenv("BACKBOND_TRANCHE1_OBSERVATIONAL", "off")
    if pin_name in {"MAPPING_SHA256", "EVALUATOR_SHA256"}:
        monkeypatch.setattr(
            observational,
            pin_name,
            {family: "0" * 64 for family in ALLOWLIST},
        )
    else:
        monkeypatch.setattr(observational, pin_name, "0" * 64)
    with pytest.raises(ObservationalContractError):
        activate_registry_rows(rows)
    assert rows == original
    assert evidence.read_bytes() == b"retained-evidence"


def test_complete_downstream_matrix_canonical_hash_isolation(monkeypatch):
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", "e06cb09df3d0a490816eace227b083b97665949d")
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", "e06cb09df3d0a490816eace227b083b97665949d")
    envelope = json.loads(
        (Path(__file__).resolve().parents[1] / "artifacts" / "beaaa-8225" /
         "consolidated_envelope_seed0.json").read_text(encoding="utf-8")
    )
    envelope.setdefault("subscores", {
        "GOVERNANCE": 22.0, "ROBUSTNESS": 22.0,
        "BIAS_FAIRNESS": 22.0, "OPS": 22.0,
    })
    envelope["applicability_record"] = {
        "measured_dims_count": 22, "na_dims_count": 1, "pillars_with_all_na": [],
    }
    envelope["uncovered_dimensions"] = []
    envelope["coverage_fraction"] = {"overall": 0.92, "by_peril": {
        key: 0.90 for key in (
            "wrong_recipient", "unauthorized_commitment", "data_leak",
            "hallucination", "prompt_injection", "regulatory_response",
        )
    }}
    envelope["governance_evidence"] = {"eval_ledger_p4_5": "present"}
    envelope["peril_findings"] = []
    envelope["rate_context"] = {
        "state": "NY", "class": "contact_center_agent", "limit": 50000,
        "deductible": 5000, "annual_agent_actions": 1000000,
        "product_tier": "Silver", "filed_rate_available": True,
    }
    envelope["appetite"] = {"prohibited_use_case": False}
    envelope["scan_mode"] = "offline_replay"
    envelope["spread_per_dim_worst"] = 0.0

    def matrix() -> dict:
        decision = decide(envelope)
        return {
            "locked_v1_response": envelope,
            "score_pillars_dimensions_band_published_totals": {
                key: envelope.get(key) for key in (
                    "are_total", "subscores", "dim_scores", "band",
                    "published_weighted_total", "published_countable_total",
                )
            },
            "applicability_coverage_completeness_variance_behavioral": {
                key: envelope.get(key) for key in (
                    "applicability_record", "coverage_fraction", "diagnostics",
                    "scan_incomplete", "high_variance", "behavioral_integrity",
                )
            },
            "admittance": admittance.evaluate(envelope).to_dict(),
            "eligibility_policy_bind_referral_customer_decision": decision.to_dict(),
            "policy_decision_canonical_bytes": canonical_decision_bytes(decision).decode(),
        }

    baseline = json.dumps(matrix(), sort_keys=True, separators=(",", ":"), default=str).encode()
    for outcome in ("PASS", "FAIL", "NOT_APPLICABLE", "INVALID", "INCOMPLETE"):
        rank_families([_record(sorted(ALLOWLIST)[0], f"case-{outcome}",
                               raw=4 if outcome == "PASS" else 0 if outcome == "FAIL" else None,
                               disposition=None if outcome in {"PASS", "FAIL"} else outcome)])
        assert json.dumps(
            matrix(), sort_keys=True, separators=(",", ":"), default=str
        ).encode() == baseline
