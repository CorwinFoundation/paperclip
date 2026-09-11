"""BEAAA-31567 — fail-fast on consecutive upstream auth/quota errors.

DoD tests:
- N consecutive upstream_http_403 rows abort the run and raise UpstreamQuotaAbort.
- A mixed run with scattered 403s (never N consecutive) runs to completion.
"""
from __future__ import annotations

import pytest

from api.full_scan_executor import (
    FullScanJobStore,
    UpstreamQuotaAbort,
    WorkerAttemptResult,
    _UPSTREAM_QUOTA_ABORT_THRESHOLD,
    dispatch_registry_attempt,
    registry_cases_from_rows,
)


def _row(case_id: str) -> dict:
    return {
        "test_id": "promptinject",
        "case_id": case_id,
        "status": "active",
        "production_runner_executable": True,
        "risk_or_rubric_id": "P3.1",
        "runner": "promptfoo_wrapper",
    }


def _submit_job(rows: list[dict]) -> tuple[FullScanJobStore, str, str]:
    store = FullScanJobStore()
    job = store.submit(
        agent_id="agent-quota-test",
        scanner_version="0.0.0",
        corpus_version="registry-baseline-test",
        model_versions={"primary": "fixture-v0", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(rows),
    )
    lease = store.lease(job.job_id)
    return store, job.job_id, str(lease["attempt_id"])


def _quota_worker(_row, _job):
    return WorkerAttemptResult(
        status="incomplete",
        evidence_ref=None,
        incomplete_reason="upstream_http_403",
    )


def _ok_worker(row, _job):
    return WorkerAttemptResult(
        status="complete",
        evidence_ref=f"evidence://scan/test/{row['case_id']}.json",
    )


def test_consecutive_403s_abort_run():
    """N consecutive upstream_http_403 rows must abort with UpstreamQuotaAbort."""
    n = _UPSTREAM_QUOTA_ABORT_THRESHOLD
    rows = [_row(f"case_{i:03d}") for i in range(n + 5)]
    store, job_id, attempt_id = _submit_job(rows)

    with pytest.raises(UpstreamQuotaAbort) as exc_info:
        dispatch_registry_attempt(
            store,
            job_id,
            attempt_id=attempt_id,
            registry_rows=rows,
            workers={"promptfoo": _quota_worker},
        )

    abort = exc_info.value
    assert "upstream_http_403" in abort.abort_reason
    assert abort.consecutive >= n
    assert abort.rows_processed >= n

    # Rows must be checkpointed — job can be finalised without error.
    finalized = store.finalize(job_id, attempt_id=attempt_id)
    assert finalized is not None
    assert finalized.final_manifest is not None
    # Evidence rows were preserved even though dispatch was aborted.
    assert len(finalized.final_manifest["evidence_rows"]) >= n


def test_consecutive_403s_abort_uses_quota_codes():
    """Auth codes 401, 402, 429 also trigger the abort; 500 does not."""
    n = _UPSTREAM_QUOTA_ABORT_THRESHOLD

    for code in ("401", "402", "429"):
        rows = [_row(f"c{i}") for i in range(n + 2)]
        store, job_id, attempt_id = _submit_job(rows)

        def _code_worker(_row, _job, code=code):
            return WorkerAttemptResult(
                status="incomplete",
                evidence_ref=None,
                incomplete_reason=f"upstream_http_{code}",
            )

        with pytest.raises(UpstreamQuotaAbort, match=f"upstream_http_{code}"):
            dispatch_registry_attempt(
                store,
                job_id,
                attempt_id=attempt_id,
                registry_rows=rows,
                workers={"promptfoo": _code_worker},
            )

    # 500 must NOT abort even if repeated N times.
    rows = [_row(f"c5xx_{i}") for i in range(n + 2)]
    store, job_id, attempt_id = _submit_job(rows)

    def _server_error_worker(_row, _job):
        return WorkerAttemptResult(
            status="incomplete",
            evidence_ref=None,
            incomplete_reason="upstream_http_500",
        )

    # Must not raise:
    dispatch_registry_attempt(
        store,
        job_id,
        attempt_id=attempt_id,
        registry_rows=rows,
        workers={"promptfoo": _server_error_worker},
    )


def test_scattered_403s_do_not_abort():
    """Scattered 403s (never N consecutive) must not abort the run."""
    n = _UPSTREAM_QUOTA_ABORT_THRESHOLD
    # Pattern: (n-1) 403s, then 1 success, repeat — never hits the threshold.
    rows = []
    for block in range(3):
        for i in range(n - 1):
            rows.append(_row(f"quota_{block}_{i}"))
        rows.append(_row(f"ok_{block}"))

    fail_ids = {r["case_id"] for r in rows if r["case_id"].startswith("quota")}

    def _mixed_worker(row, _job):
        if row["case_id"] in fail_ids:
            return WorkerAttemptResult(
                status="incomplete",
                evidence_ref=None,
                incomplete_reason="upstream_http_403",
            )
        return WorkerAttemptResult(
            status="complete",
            evidence_ref=f"evidence://scan/test/{row['case_id']}.json",
        )

    store, job_id, attempt_id = _submit_job(rows)
    # Must complete without raising.
    dispatch_registry_attempt(
        store,
        job_id,
        attempt_id=attempt_id,
        registry_rows=rows,
        workers={"promptfoo": _mixed_worker},
    )
    finalized = store.finalize(job_id, attempt_id=attempt_id)
    assert finalized is not None
    assert len(finalized.final_manifest["evidence_rows"]) == len(rows)


def test_abort_resets_counter_on_success():
    """Exactly N-1 quota errors, one success, then N more quota errors — only the
    second unbroken run of N should abort."""
    n = _UPSTREAM_QUOTA_ABORT_THRESHOLD
    rows = (
        [_row(f"pre_{i}") for i in range(n - 1)]  # n-1 quota rows
        + [_row("break")]                            # one success
        + [_row(f"post_{i}") for i in range(n)]     # n quota rows → abort
    )
    fail_ids = {r["case_id"] for r in rows if r["case_id"] != "break"}

    def _mixed_worker(row, _job):
        if row["case_id"] in fail_ids:
            return WorkerAttemptResult(
                status="incomplete",
                evidence_ref=None,
                incomplete_reason="upstream_http_403",
            )
        return WorkerAttemptResult(
            status="complete",
            evidence_ref=f"evidence://scan/test/{row['case_id']}.json",
        )

    store, job_id, attempt_id = _submit_job(rows)
    with pytest.raises(UpstreamQuotaAbort):
        dispatch_registry_attempt(
            store,
            job_id,
            attempt_id=attempt_id,
            registry_rows=rows,
            workers={"promptfoo": _mixed_worker},
        )
    finalized = store.finalize(job_id, attempt_id=attempt_id)
    # Pre-abort rows (n-1 + 1 break + n abort threshold) should all be checkpointed.
    assert len(finalized.final_manifest["evidence_rows"]) >= n
