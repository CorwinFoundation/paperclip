"""BEAAA-31973: interrupt -> restart -> resume on the controlled full-scan runner.

Drives a real sqlite-backed ``FullScanJobStore`` with real registry rows, so the
identity match between checkpointed evidence rows (``probe_id``) and pending
registry rows (``runtime_case_identity``/``case_id``) is exercised rather than
stubbed on both sides.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.full_scan_executor import (  # noqa: E402
    WorkerAttemptResult,
    build_attempt_manifest,
    case_id_from_registry_row,
    dispatch_registry_attempt,
    registry_cases_from_rows,
)
from tools.framework_worker_registry import worker_id_for_registry_row  # noqa: E402


def _load_runner():
    path = REPO_ROOT / "scripts" / "beaaa29082_controlled_full_scan.py"
    spec = importlib.util.spec_from_file_location("beaaa29082_controlled_full_scan", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses need the module resolvable
    spec.loader.exec_module(module)
    return module


RUNNER = _load_runner()
CASE_LIMIT = 8
INTERRUPT_AFTER = 3


def test_live_runner_requests_one_row_checkpoint_flush():
    """The tested kill/resume behavior must be enabled by the real runner."""
    source = inspect.getsource(RUNNER.run_scan)
    assert "checkpoint_every=1" in source


@pytest.fixture(scope="module")
def registry_rows():
    rows = RUNNER.executable_rows(RUNNER.load_registry_rows())[:CASE_LIMIT]
    if len(rows) <= INTERRUPT_AFTER:
        pytest.skip("registry carries too few executable rows for this test")
    return rows


class _RecordingWorker:
    """Stands in for a framework worker and records the cases it was asked to run."""

    def __init__(self, calls: list[str], *, interrupt_after: int | None = None) -> None:
        self.calls = calls
        self.interrupt_after = interrupt_after

    def __call__(self, registry_row, job):
        if self.interrupt_after is not None and len(self.calls) >= self.interrupt_after:
            # BaseException, so it bypasses the dispatcher's `except Exception`
            # the way a signal-driven teardown does.
            raise KeyboardInterrupt("simulated kill mid-dispatch")
        case_id = case_id_from_registry_row(registry_row)
        self.calls.append(case_id)
        return WorkerAttemptResult(
            status="complete",
            evidence_ref=f"evidence://{job.job_id}/{case_id}.json",
            model_version="test-model",
            verdict="pass",
            target_calls=1,
            judge_calls=1,
        )


def _workers(rows, calls, *, interrupt_after=None):
    worker = _RecordingWorker(calls, interrupt_after=interrupt_after)
    return {worker_id_for_registry_row(dict(row)): worker for row in rows}


def _submit(store, rows):
    return store.submit(
        agent_id=RUNNER.VAPI_ASSISTANT_ID,
        scanner_version="1.3.0",
        corpus_version="1.3.0",
        model_versions={
            "primary": "test-model",
            "runner_candidate_sha": "a" * 40,
        },
        seed=42,
        registry_cases=registry_cases_from_rows(rows, execution_flag=RUNNER.EXECUTION_FLAG),
    )


def _fingerprint(manifest):
    """Manifest content that must not differ between resumed and straight runs."""
    rows = sorted(
        (
            row.get("probe_id"),
            row.get("status"),
            row.get("rubric_point_id"),
            row.get("acquisition_basis"),
            row.get("seed"),
        )
        for row in manifest.get("evidence_rows", [])
    )
    return {
        "rows": rows,
        "counts": manifest.get("counts"),
        "incomplete": manifest.get("incomplete"),
        "passed": manifest.get("acceptance_gate", {}).get("passed"),
    }


def test_interrupted_run_resumes_without_repeating_paid_calls(tmp_path, registry_rows):
    # --- control: one uninterrupted dispatch --------------------------------
    control_calls: list[str] = []
    control_store = RUNNER.open_job_store(tmp_path / "control.sqlite3")
    control_job = _submit(control_store, registry_rows)
    control_attempt = control_store.lease(control_job.job_id, ttl_seconds=3600)["attempt_id"]
    control_job = dispatch_registry_attempt(
        control_store,
        control_job.job_id,
        attempt_id=control_attempt,
        registry_rows=registry_rows,
        workers=_workers(registry_rows, control_calls),
        execution_flag=RUNNER.EXECUTION_FLAG,
        checkpoint_every=1,
    )
    assert len(control_calls) == len(registry_rows)

    # --- first pass, killed mid-dispatch ------------------------------------
    db = tmp_path / "resume.sqlite3"
    first_calls: list[str] = []
    store = RUNNER.open_job_store(db)
    job = _submit(store, registry_rows)
    attempt_id = store.lease(job.job_id, ttl_seconds=6 * 3600)["attempt_id"]
    with pytest.raises(KeyboardInterrupt):
        dispatch_registry_attempt(
            store,
            job.job_id,
            attempt_id=attempt_id,
            registry_rows=registry_rows,
            workers=_workers(registry_rows, first_calls, interrupt_after=INTERRUPT_AFTER),
            execution_flag=RUNNER.EXECUTION_FLAG,
            checkpoint_every=1,
        )
    assert len(first_calls) == INTERRUPT_AFTER

    # --- restart: a fresh store over the same sqlite file -------------------
    resumed_store = RUNNER.open_job_store(db)
    assert resumed_store.status(job.job_id).status == "queued"  # dead lease cleared

    # The identity assertion this test exists for: evidence rows written by the
    # dispatcher resolve back to the same case ids the registry rows carry.
    completed = RUNNER.completed_case_ids(
        resumed_store, job.job_id, expected_candidate_sha="a" * 40
    )
    assert completed == set(first_calls)

    # Neither a different candidate nor a row whose explicit binding was
    # tampered with may suppress a paid call on restart.
    assert RUNNER.completed_case_ids(
        resumed_store, job.job_id, expected_candidate_sha="b" * 40
    ) == set()

    pending = [row for row in registry_rows if case_id_from_registry_row(row) not in completed]
    assert len(pending) == len(registry_rows) - INTERRUPT_AFTER

    second_calls: list[str] = []
    lease = resumed_store.lease(job.job_id, ttl_seconds=6 * 3600)
    assert lease["leased"] is True
    resumed_job = dispatch_registry_attempt(
        resumed_store,
        job.job_id,
        attempt_id=lease["attempt_id"],
        registry_rows=pending,
        workers=_workers(registry_rows, second_calls),
        execution_flag=RUNNER.EXECUTION_FLAG,
        checkpoint_every=1,
    )

    # No repeat target/judge call for a case already paid for.
    assert set(second_calls).isdisjoint(set(first_calls))
    assert sorted(first_calls + second_calls) == sorted(control_calls)

    # And the resumed manifest matches the uninterrupted one.
    assert _fingerprint(build_attempt_manifest(resumed_job)) == _fingerprint(
        build_attempt_manifest(control_job)
    )


def test_checkpoint_flush_survives_a_kill_before_dispatch_returns(tmp_path, registry_rows):
    """Without an in-loop flush, a killed dispatch checkpoints nothing."""
    db = tmp_path / "flush.sqlite3"
    calls: list[str] = []
    store = RUNNER.open_job_store(db)
    job = _submit(store, registry_rows)
    attempt_id = store.lease(job.job_id, ttl_seconds=3600)["attempt_id"]
    with pytest.raises(KeyboardInterrupt):
        dispatch_registry_attempt(
            store,
            job.job_id,
            attempt_id=attempt_id,
            registry_rows=registry_rows,
            workers=_workers(registry_rows, calls, interrupt_after=INTERRUPT_AFTER),
            execution_flag=RUNNER.EXECUTION_FLAG,
            checkpoint_every=1,
        )
    reopened = RUNNER.open_job_store(db)
    assert len(reopened.status(job.job_id).evidence_rows) == INTERRUPT_AFTER
