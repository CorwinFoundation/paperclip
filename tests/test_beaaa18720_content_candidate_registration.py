from __future__ import annotations

from collections import Counter
import copy
import json
from types import SimpleNamespace
from pathlib import Path

from harness.runner import MockRunner
from tools.active_test_registry import (
    build_registry,
    count_active_executable,
    count_target_bound_runtime,
    distinct_case_counts,
    validate_registry,
)
from tools.content_candidate_inventory import (
    EXPECTED_INVENTORY_FAMILIES,
    EXPECTED_INVENTORY_ROWS,
    INVENTORY_SHA256,
    candidate_judge_ensemble_n,
    load_candidate_inventory,
    materialize_candidate_case,
)
from tools.framework_worker_registry import (
    build_worker_manifest,
    worker_id_for_registry_row,
)
from tools.registered_case_executor import (
    EvidenceStore,
    RegisteredCaseExecutor,
    ScanBudget,
)


def test_accepted_inventory_fails_closed_on_count_hash_and_uniqueness() -> None:
    rows = load_candidate_inventory()

    assert len(rows) == EXPECTED_INVENTORY_ROWS
    assert len({row["family_id"] for row in rows}) == EXPECTED_INVENTORY_FAMILIES
    assert len({row["stable_case_id"] for row in rows}) == EXPECTED_INVENTORY_ROWS
    assert len({row["dedupe_sha256"] for row in rows}) == EXPECTED_INVENTORY_ROWS
    assert (
        len({row["semantic_behavior_fingerprint"] for row in rows})
        == EXPECTED_INVENTORY_ROWS
    )
    assert all(row["reporting_status"] == "reported_not_scored" for row in rows)
    assert len(INVENTORY_SHA256) == 64


def test_all_candidates_are_registered_without_duplicate_family_case_ids() -> None:
    rows = build_registry()
    candidates = [row for row in rows if row.get("content_candidate_inventory")]

    assert len(rows) == 2_242
    assert len(candidates) == 2_000
    assert len({(row["test_id"], row["case_id"]) for row in candidates}) == 2_000
    assert Counter(row["runner"] for row in candidates) == {
        "content_candidate_worker": 1_782,
        "bench_wrapper": 218,
    }
    assert all(row["production_runner_executable"] is True for row in candidates)
    registration_statuses = Counter(
        row["candidate_registration_status"] for row in candidates
    )
    assert registration_statuses == {
        "registered_reported_not_scored": 1_964,
        "registered_zero_weight_observational": 36,
    }


def test_registered_count_and_distinct_runtime_count_reconcile() -> None:
    rows = build_registry()

    assert count_active_executable(rows) == 2_202
    assert count_target_bound_runtime(rows) == 2_213
    assert distinct_case_counts(rows)["total"] == 2_201
    assert not validate_registry(rows, published_count=2_202, strict=True)


def test_candidate_materializer_handles_pairs_and_conversation_events() -> None:
    pair = materialize_candidate_case(
        "counterfactual_stability",
        "cs.pair.ne.01",
    )
    conversation = materialize_candidate_case(
        "authorization_revocation_race",
        "bb18716.authorization_revocation_race.01",
    )

    assert pair.interaction_mode == "independent"
    assert len(pair.prompts) == 2
    assert pair.prompts[0] != pair.prompts[1]
    assert conversation.interaction_mode == "conversation"
    assert conversation.prompts
    assert "Prepare the synthetic case" in conversation.prompts[0]


def test_candidate_adapter_calls_target_and_pinned_judge(tmp_path: Path) -> None:
    rows = build_registry()
    row = next(
        row
        for row in rows
        if row["test_id"] == "human_escalation_flagging"
        and row["case_id"] == "esc-01"
    )
    executor = RegisteredCaseExecutor(
        subject=MockRunner("target", lambda _prompt: '{"escalate":true}'),
        judge=MockRunner(
            "judge",
            lambda _prompt: '{"verdict":"pass","score":1.0}',
        ),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )

    outcome = executor._execute_content_candidate(row["test_id"], row["case_id"])

    assert outcome["verdict"] == "pass"
    assert outcome["responses"] == ['{"escalate":true}']
    assert outcome["judge"]["oracle_type"] == "deterministic_field_assertion"
    assert outcome["judge"]["computed_runtime_case_identity"] == row[
        "runtime_case_identity"
    ]
    assert executor.ledger.snapshot()["target_calls"] == 1
    assert executor.ledger.snapshot()["judge_calls"] == 1
    assert worker_id_for_registry_row(row) == "content_candidate"


def test_allowlisted_candidate_executes_n5_measured_observation(
    tmp_path: Path, monkeypatch
) -> None:
    sha = "14625eecf8f4845e99a18feddf02d5280e041fc5"
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", sha)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", sha)
    row = next(
        row
        for row in build_registry()
        if row["test_id"] == "tool_misuse_unauthorized_call"
        and row["case_id"] == "TOOL_MISUSE_UC-01"
        and row["runner"] == "content_candidate_worker"
    )
    row = {
        **row,
        "target_authorization_ref": "full-scan:test:authorized",
        "case_evidence_uri": (
            "evidence://test/tool_misuse_unauthorized_call/TOOL_MISUSE_UC-01.json"
        ),
        "capability_manifest": {"has_tool_calling": True},
    }
    executor = RegisteredCaseExecutor(
        subject=MockRunner(
            "target",
            lambda _prompt: '{"actual_response_label":"resisted_no_disclosure"}',
        ),
        judge=MockRunner("judge", lambda _prompt: '{"verdict":"pass","score":1.0}'),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )

    outcome = executor._execute_content_candidate(
        row["test_id"], row["case_id"], row
    )

    observed = outcome["observational_result"]
    assert observed["outcome"] == "PASS"
    assert observed["raw_result"] == 4
    assert observed["replicate_count"] == 5
    assert observed["pins"]["commit_sha256"] == sha
    assert observed["underwriting_active"] is False
    assert observed["effective_underwriting_weight"] == 0.0
    assert outcome["score"] is None
    assert executor.ledger.snapshot()["target_calls"] == 5
    assert executor.ledger.snapshot()["judge_calls"] == 0


def test_production_worker_call_persists_isolated_observation(
    tmp_path: Path, monkeypatch
) -> None:
    sha = "14625eecf8f4845e99a18feddf02d5280e041fc5"
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", sha)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", sha)
    row = next(
        row
        for row in build_registry()
        if row["test_id"] == "tool_misuse_param_tamper"
        and row["case_id"] == "TOOL_MISUSE_PT-01"
        and row["runner"] == "content_candidate_worker"
    )
    executor = RegisteredCaseExecutor(
        subject=MockRunner(
            "target",
            lambda _prompt: '{"actual_response_label":"resisted_no_disclosure"}',
        ),
        judge=MockRunner("judge", lambda _prompt: '{"verdict":"pass","score":1.0}'),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )

    result = executor(
        row,
        SimpleNamespace(
            job_id="tranche-job",
            agent_id="authorized-agent",
            seed=42,
            capability_manifest={"has_tool_calling": True},
        ),
    )

    assert result.status == "complete"
    assert result.target_calls == 5
    assert result.judge_calls == 0
    evidence = json.loads(
        (
            tmp_path
            / "tranche-job"
            / "tool_misuse_param_tamper"
            / "TOOL_MISUSE_PT-01.json"
        ).read_text(encoding="utf-8")
    )
    observed = evidence["observational_result"]
    assert observed["outcome"] == "PASS"
    assert observed["raw_result"] == 4
    assert observed["target_authorization_ref"] == (
        "full-scan-job:tranche-job:agent:authorized-agent"
    )
    assert all(
        replicate["evidence_uri"]
        == "evidence://tranche-job/tool_misuse_param_tamper/TOOL_MISUSE_PT-01.json"
        for replicate in observed["replicates"]
    )
    assert all(
        "recorded-by-case-envelope" not in replicate["started_at"]
        and "recorded-by-case-envelope" not in replicate["completed_at"]
        for replicate in observed["replicates"]
    )
    assert evidence["score"] is None
    manifest = {"job_id": "tranche-job", "locked_v1_result": {"are_total": 78}}
    before = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    executor.write_manifest(manifest)
    assert json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() == before
    ranking = json.loads(
        (tmp_path / "tranche-job" / "observational-ranking.json").read_text(
            encoding="utf-8"
        )
    )
    assert ranking["families"]
    assert ranking["evidence_sha256"].startswith("sha256:")
    assert {
        row["ranking_status"] for row in ranking["families"]
    } == {"INSUFFICIENT_EVIDENCE"}
    assert (
        tmp_path / "tranche-job" / "manifest.json"
    ).read_text(encoding="utf-8")


def test_nonallowlisted_candidate_cannot_forge_measured_permission(
    tmp_path: Path, monkeypatch
) -> None:
    sha = "14625eecf8f4845e99a18feddf02d5280e041fc5"
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", sha)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", sha)
    row = next(
        row
        for row in build_registry()
        if row["test_id"] == "human_escalation_flagging"
        and row["runner"] == "content_candidate_worker"
    )
    forged = copy.deepcopy(row)
    forged.update(
        {
            "scoring_active": True,
            "underwriting_active": False,
            "effective_underwriting_weight": 0.0,
            "observational_contract_version": "tranche1-zero-weight-observational/v0.2",
            "observational_disclosure": "zero-weight observational; not used for underwriting",
            "k_replicates": 5,
            "target_authorization_ref": "full-scan:test:authorized",
        }
    )
    executor = RegisteredCaseExecutor(
        subject=MockRunner("target", lambda _prompt: "x"),
        judge=MockRunner("judge", lambda _prompt: '{"verdict":"pass","score":1.0}'),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )

    outcome = executor._execute_content_candidate(
        forged["test_id"], forged["case_id"], forged
    )
    assert "observational_result" not in outcome
    assert outcome["observational_rejection"] == (
        "content-candidate measured output is not row-authorized"
    )
    assert outcome["score"] is None


def test_divergent_n5_is_incomplete_not_min_aggregated(
    tmp_path: Path, monkeypatch
) -> None:
    sha = "14625eecf8f4845e99a18feddf02d5280e041fc5"
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", sha)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", sha)
    row = next(
        row
        for row in build_registry()
        if row["test_id"] == "tool_misuse_scope_escape"
        and row["case_id"] == "TOOL_MISUSE_SE-01"
        and row["runner"] == "content_candidate_worker"
    )
    row = {
        **row,
        "target_authorization_ref": "full-scan:test:authorized",
        "case_evidence_uri": (
            "evidence://test/tool_misuse_scope_escape/TOOL_MISUSE_SE-01.json"
        ),
        "capability_manifest": {"has_tool_calling": True},
    }
    calls = {"count": 0}

    def alternating_target(_prompt):
        calls["count"] += 1
        label = "resisted_no_disclosure" if calls["count"] % 2 else "unsafe"
        return f'{{"actual_response_label":"{label}"}}'

    executor = RegisteredCaseExecutor(
        subject=MockRunner("target", alternating_target),
        judge=MockRunner("judge", lambda _prompt: "must-not-run"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )

    outcome = executor._execute_content_candidate(
        row["test_id"], row["case_id"], row
    )
    assert outcome["observational_result"]["outcome"] == "INCOMPLETE"
    assert outcome["observational_result"]["raw_result"] is None
    assert executor.ledger.snapshot()["judge_calls"] == 0


def test_missing_capability_manifest_is_na_before_any_model_call(
    tmp_path: Path, monkeypatch
) -> None:
    sha = "5541d9b34e7efa7e570b45c80b7fbc0098b45517"
    monkeypatch.setenv("BACKBOND_RUNTIME_COMMIT_SHA", sha)
    monkeypatch.setenv("BACKBOND_TRANCHE1_AUTHORIZED_COMMIT_SHA", sha)
    row = next(
        row
        for row in build_registry()
        if row["test_id"] == "tool_misuse_param_tamper"
        and row["case_id"] == "TOOL_MISUSE_PT-01"
        and row["runner"] == "content_candidate_worker"
    )
    row = {
        **row,
        "target_authorization_ref": "full-scan:test:authorized",
        "case_evidence_uri": (
            "evidence://test/tool_misuse_param_tamper/TOOL_MISUSE_PT-01.json"
        ),
        "capability_manifest": None,
    }
    executor = RegisteredCaseExecutor(
        subject=MockRunner("target", lambda _prompt: (_ for _ in ()).throw(
            AssertionError("target must not be called")
        )),
        judge=MockRunner("judge", lambda _prompt: (_ for _ in ()).throw(
            AssertionError("judge must not be called")
        )),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )

    outcome = executor._execute_content_candidate(
        row["test_id"], row["case_id"], row
    )

    assert outcome["observational_result"]["outcome"] == "NOT_APPLICABLE"
    assert outcome["observational_result"]["raw_result"] is None
    assert executor.ledger.snapshot()["target_calls"] == 0
    assert executor.ledger.snapshot()["judge_calls"] == 0


def test_default_budget_covers_the_registered_candidate_adapter_calls() -> None:
    rows = [
        row
        for row in build_registry()
        if row.get("runner") == "content_candidate_worker"
    ]
    specs = [
        materialize_candidate_case(row["test_id"], row["case_id"])
        for row in rows
    ]
    candidate_target_calls = sum(len(spec.prompts) for spec in specs)
    candidate_judge_calls = sum(candidate_judge_ensemble_n(spec) for spec in specs)
    budget = ScanBudget()

    assert candidate_target_calls == 2_805
    assert candidate_judge_calls == 2_468
    assert candidate_target_calls < budget.max_target_calls
    assert candidate_judge_calls < budget.max_judge_calls


def test_candidate_worker_global_default_false_with_exact_row_allowlist() -> None:
    manifest = build_worker_manifest(build_registry())
    worker = next(
        worker
        for worker in manifest["workers"]
        if worker["worker_id"] == "content_candidate"
    )

    assert worker["active_registry_rows"] == 1_782
    assert worker["dependency_pins"]["inventory_sha256"] == INVENTORY_SHA256
    assert worker["measured_score_allowed"] is False
    assert worker["reported_not_scored_only"] is True
    assert worker["measured_score_family_allowlist"] == {
        "tool_misuse_param_tamper": 12,
        "tool_misuse_scope_escape": 12,
        "tool_misuse_unauthorized_call": 12,
    }
    assert (
        worker["row_bound_measured_contract"]
        == "tranche1-zero-weight-observational/v0.2"
    )
    assert worker["release_blocker"] is False
