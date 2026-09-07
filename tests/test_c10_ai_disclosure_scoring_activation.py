from __future__ import annotations

import json

import pytest

from harness.runner import MockRunner
from tools import c10_family_activation
from tools.active_test_registry import (
    build_registry,
    count_active_executable,
    count_canonical_scored_rows,
    count_target_bound_runtime,
    is_canonical_scored_row,
)
from tools.c10_family_activation import C10FamilyActivationError
from tools.founder_dev_mode import FounderDevModeError, assert_production_attested
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


EXPECTED_IDS = {f"aid-{index:03d}" for index in range(1, 13)}


def test_every_family_manifest_activates_its_exact_saved_verdict_roster() -> None:
    rows = build_registry()
    activated = {
        (row["test_id"], row["case_id"]): row
        for row in rows
        if row.get("c10_family_activation") is True
    }

    for path in c10_family_activation.ACTIVATION_PATHS:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        family = manifest["family_id"]
        stable_ids = set(manifest["stable_case_ids"])
        family_rows = {
            case_id: row
            for (test_id, case_id), row in activated.items()
            if test_id == family
        }

        assert set(family_rows) == stable_ids
        assert len(family_rows) == manifest["case_count"]
        assert {row["saved_verdict"] for row in family_rows.values()} <= {
            "PASS",
            "FAIL",
        }
        assert {row["priced_score_weight"] for row in family_rows.values()} == {
            0.0
        }


def test_exact_c10_ai_disclosure_family_is_scored_zero_weight() -> None:
    rows = build_registry()
    activated = [
        row
        for row in rows
        if row.get("c10_family_activation") is True
        and row["test_id"] == "ai_disclosure"
    ]

    assert len(activated) == 12
    assert {row["test_id"] for row in activated} == {"ai_disclosure"}
    assert {row["case_id"] for row in activated} == EXPECTED_IDS
    assert all(is_canonical_scored_row(row) for row in activated)
    assert {row["scoring_active"] for row in activated} == {True}
    assert {row["reporting_status"] for row in activated} == {"scored"}
    assert {row["score_input"] for row in activated} == {True}
    assert {row["saved_verdict"] for row in activated} == {"FAIL"}
    assert {row["priced_score_weight"] for row in activated} == {0.0}
    assert {row["effective_underwriting_weight"] for row in activated} == {0.0}
    assert {row["underwriting_active"] for row in activated} == {False}
    assert {row["attestation_tier"] for row in activated} == {"founder_dev_mode"}
    assert {row["production_runner_executable"] for row in activated} == {True}
    with pytest.raises(FounderDevModeError):
        assert_production_attested(rows)


def test_every_activated_c10_case_executes_to_a_numeric_saved_evidence_score(tmp_path) -> None:
    rows = [row for row in build_registry() if row.get("c10_family_activation") is True]
    executor = RegisteredCaseExecutor(
        subject=MockRunner("target", lambda _prompt: (_ for _ in ()).throw(
            AssertionError("saved C10 evidence must not call a target")
        )),
        judge=MockRunner("judge", lambda _prompt: (_ for _ in ()).throw(
            AssertionError("saved C10 evidence must not call a judge")
        )),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )
    outcomes = [
        executor._execute_content_candidate(row["test_id"], row["case_id"], row)
        for row in rows
    ]
    assert len(rows) == 122
    assert all(outcome["score"] in {0.0, 1.0} for outcome in outcomes)
    assert all(outcome["judge"]["type"] == "c10_saved_deterministic_evidence" for outcome in outcomes)


def test_activation_roster_is_exactly_the_six_governed_families() -> None:
    expected = {
        "ai_disclosure", "cbrn_refusal", "cyber_misuse_refusal",
        "harmful_output_golden", "p3_5_multiturn_jailbreak", "p3_6_output_attack",
    }
    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in c10_family_activation.ACTIVATION_PATHS]
    assert {manifest["family_id"] for manifest in manifests} == expected
    assert sum(manifest["case_count"] for manifest in manifests) == 122


def test_activations_add_exact_scored_cases_without_count_or_pricing_drift() -> None:
    rows = build_registry()
    activated = c10_family_activation.load_c10_family_activations()

    assert count_canonical_scored_rows(rows) == 309 + len(activated)
    assert count_active_executable(rows) == 2_353
    assert count_target_bound_runtime(rows) == 2_364
    assert sum(
        row.get("priced_score_weight", 0.0)
        for row in rows
        if row.get("c10_family_activation") is True
    ) == 0.0


def test_legacy_lane_a_namesake_remains_reported_not_scored() -> None:
    rows = build_registry()
    legacy = [
        row
        for row in rows
        if row["test_id"] == "ai_disclosure" and row["case_id"].startswith("AD-")
    ]

    assert len(legacy) == 12
    assert {row["scoring_active"] for row in legacy} == {False}
    assert {row["reporting_status"] for row in legacy} == {"reported_not_scored"}
    assert not [row for row in legacy if row.get("c10_family_activation")]


def test_activation_fails_closed_if_pinned_evidence_hash_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    source = c10_family_activation.ACTIVATION_PATHS[0]
    manifest = json.loads(source.read_text(encoding="utf-8"))
    manifest["evidence"]["canonical_backlog_sha256"] = "0" * 64
    changed = tmp_path / "activation.json"
    changed.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(c10_family_activation, "ACTIVATION_PATHS", (changed,))

    with pytest.raises(C10FamilyActivationError, match="canonical backlog hash mismatch"):
        c10_family_activation.load_c10_family_activations()


def test_activation_fails_closed_if_one_case_is_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    source = c10_family_activation.ACTIVATION_PATHS[0]
    manifest = json.loads(source.read_text(encoding="utf-8"))
    manifest["stable_case_ids"].pop()
    changed = tmp_path / "activation.json"
    changed.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(c10_family_activation, "ACTIVATION_PATHS", (changed,))

    with pytest.raises(C10FamilyActivationError, match="invalid C10 family activation roster"):
        c10_family_activation.load_c10_family_activations()


def test_activation_fails_closed_if_roster_substitutes_a_semantic_namesake(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    manifest = json.loads(c10_family_activation.ACTIVATION_PATHS[0].read_text(encoding="utf-8"))
    manifest["stable_case_ids"][-1] = "AD-012"
    changed = tmp_path / "activation.json"
    changed.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(c10_family_activation, "ACTIVATION_PATHS", (changed,))
    with pytest.raises(C10FamilyActivationError, match="does not match source C10 mapping ledger"):
        c10_family_activation.load_c10_family_activations()


def test_executor_fails_closed_if_primary_evidence_refs_are_missing(tmp_path) -> None:
    row = next(row for row in build_registry() if row.get("c10_family_activation") is True)
    row["saved_evidence_ref"] = ""
    executor = RegisteredCaseExecutor(
        subject=MockRunner("target", lambda _: "unused"),
        judge=MockRunner("judge", lambda _: "unused"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )
    outcome = executor._execute_content_candidate(row["test_id"], row["case_id"], row)
    assert outcome["score"] is None
    assert outcome["c10_evidence_rejection"] == "missing_or_invalid_primary_evidence"
