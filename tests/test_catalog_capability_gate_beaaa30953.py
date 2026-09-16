from __future__ import annotations

import json

import pytest

from api.full_scan_executor import (
    FullScanJobStore,
    WorkerAttemptResult,
    dispatch_registry_attempt,
    registry_cases_from_rows,
    worker_id_for_registry_row,
)
from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.registered_case_executor import (
    _judge_materialized_text_case,
    _structured_unmet_criteria,
)


def _row() -> dict:
    return {
        "status": "active",
        "production_runner_executable": True,
        "test_id": "bb18716.authorization_revocation_race",
        "case_id": "bb18716.authorization_revocation_race.007",
        "risk_or_rubric_id": "runtime.authorization",
        "runner_adapter": "registered_case_executor",
        "applicability_gate": {
            "status": "applicable_when_capability_declared",
            "required_capability": "scoped_authorization",
            "capability_gate": "manifest.scoped_authorization=true",
            "na_evidence_required": ["profile_capability_manifest"],
            "na_is_pass": False,
            "capability_dependent_criteria": ["ambiguity_recorded"],
        },
    }


def _dispatch(capability_manifest, worker):
    row = _row()
    store = FullScanJobStore()
    job = store.submit(
        agent_id="target",
        scanner_version="test",
        corpus_version="test",
        model_versions={"primary": "fixture", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows([row]),
        idempotency_key=f"beaaa-30953:{capability_manifest!r}",
        capability_manifest=capability_manifest,
    )
    lease = store.lease(job.job_id, ttl_seconds=60)
    return dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=[row],
        workers={worker_id_for_registry_row(row): worker},
    )


@pytest.mark.parametrize("manifest", [None, {}])
def test_capability_only_failure_is_incomplete_after_worker(manifest) -> None:
    def worker(_row, _job):
        return WorkerAttemptResult(
            status="complete", evidence_ref="fixture.json", verdict="fail",
            target_calls=1, judge_calls=1,
            unmet_criteria=("ambiguity_recorded",),
        )

    job = _dispatch(manifest, worker)
    [evidence] = job.evidence_rows
    assert evidence.status == "incomplete"
    assert evidence.incomplete_reason.startswith(
        "unavailable_capability:scoped_authorization="
    )
    assert evidence.target_calls == evidence.judge_calls == 1
    assert evidence.evidence_ref is not None


def test_capability_manifest_true_false_and_absent_are_distinct() -> None:
    def worker(_row, _job):
        return WorkerAttemptResult(
            status="complete", evidence_ref="fixture.json", verdict="fail",
            target_calls=1, judge_calls=1,
            unmet_criteria=("ambiguity_recorded",),
        )

    declared_true = _dispatch({"scoped_authorization": True}, worker).evidence_rows[0]
    declared_false = _dispatch({"scoped_authorization": False}, worker).evidence_rows[0]
    absent = _dispatch({}, worker).evidence_rows[0]

    assert (declared_true.status, declared_true.verdict, declared_true.target_calls) == (
        "complete", "fail", 1,
    )
    assert (declared_false.status, declared_false.verdict) == (
        "complete", "not_applicable",
    )
    assert declared_false.target_calls == declared_false.judge_calls == 0
    assert declared_false.acquisition_basis == "not-applicable"
    assert declared_false.disposition == "report-only"
    assert absent.status == "incomplete"
    assert absent.verdict is None


def test_not_applicable_row_carries_na_evidence() -> None:
    """na_evidence must carry both fields named by na_evidence_required."""
    manifest = {"scoped_authorization": False}

    def worker(_row, _job):
        return WorkerAttemptResult(
            status="complete", evidence_ref="fixture.json", verdict="fail",
            target_calls=1, judge_calls=1,
            unmet_criteria=("ambiguity_recorded",),
        )

    ev = _dispatch(manifest, worker).evidence_rows[0]
    assert ev.verdict == "not_applicable"
    assert ev.na_evidence is not None
    assert ev.na_evidence["profile_capability_manifest"] == manifest
    assert ev.na_evidence["missing_capability_name"] == "scoped_authorization"
    assert ev.na_evidence["declared_value"] is False
    # Both contract fields must also appear in the serialised manifest row.
    row = ev.to_manifest_row()
    assert row["na_evidence"]["profile_capability_manifest"] == manifest
    assert row["na_evidence"]["missing_capability_name"] == "scoped_authorization"


def test_non_capability_failure_stays_fail() -> None:
    def worker(_row, _job):
        return WorkerAttemptResult(
            status="complete", evidence_ref="fixture.json", verdict="fail",
            target_calls=1, judge_calls=1,
            unmet_criteria=("ambiguity_recorded", "harmful_disclosure"),
        )

    job = _dispatch({"scoped_authorization": False}, worker)
    [evidence] = job.evidence_rows
    assert evidence.status == "complete"
    assert evidence.verdict == "fail"


def test_met_catalog_capability_preserves_worker_verdict() -> None:
    def worker(_row, _job):
        return WorkerAttemptResult(
            status="complete",
            evidence_ref="fixture.json",
            verdict="fail",
            target_calls=1,
            judge_calls=1,
        )

    job = _dispatch({"scoped_authorization": True}, worker)
    [evidence] = job.evidence_rows
    assert evidence.status == "complete"
    assert evidence.verdict == "fail"
    assert evidence.target_calls == evidence.judge_calls == 1


def test_unclassified_judge_failure_fails_closed_as_fail() -> None:
    row = _row()
    row["applicability"] = row.pop("applicability_gate")
    store = FullScanJobStore()
    job = store.submit(
        agent_id="target",
        scanner_version="test",
        corpus_version="test",
        model_versions={"primary": "fixture", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows([row]),
    )
    lease = store.lease(job.job_id, ttl_seconds=60)
    finished = dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=[row],
        workers={worker_id_for_registry_row(row): lambda _row, _job: WorkerAttemptResult(
            status="complete", evidence_ref="fixture.json", verdict="fail",
            target_calls=1, judge_calls=1,
        )},
    )
    [evidence] = finished.evidence_rows
    assert evidence.status == "complete"
    assert evidence.verdict == "fail"


def test_registry_declares_structured_capability_criterion() -> None:
    row = next(
        row for row in build_registry()
        if row["test_id"] == "authorization_revocation_race"
        and (row.get("applicability_gate") or {}).get("na_evidence_required")
    )
    assert row["applicability_gate"]["capability_dependent_criteria"] == [
        "capability_manifest"
    ]


def test_judge_returns_structured_capability_only_failure() -> None:
    judge = MockRunner(
        "judge",
        lambda _prompt: json.dumps({
            "verdict": "fail", "score": 0.0,
            "unmet_criteria": ["capability_manifest"],
        }),
    )
    result = _judge_materialized_text_case(
        judge,
        prompts=["do the synthetic action"], responses=["unable"],
        expected={}, judge_criteria="fixture",
        applicability={"manifest": None},
    )
    assert _structured_unmet_criteria({"judge": result}) == (
        "capability_manifest",
    )


def test_malformed_structured_judge_result_fails_closed() -> None:
    assert _structured_unmet_criteria({
        "judge": {"verdict": "fail", "unmet_criteria": "capability_manifest"}
    }) == ()
