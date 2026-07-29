from __future__ import annotations

from collections import Counter
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


def test_candidate_worker_is_pinned_and_never_measured_score_allowed() -> None:
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
    assert worker["release_blocker"] is False
