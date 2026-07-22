"""DB-backed full-scan queue worker.

BEAAA-14843 rule-execution scope: this worker consumes jobs already durably
accepted by ``FullScanJobStore``. It does not expose a public async contract and
does not alter rubric, judge, scoring, or response-envelope rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from api.full_scan_executor import (
    DEFAULT_MAX_RECOVERY_ATTEMPTS,
    FrameworkWorker,
    FullScanJobStore,
    dispatch_registry_attempt,
)


@dataclass(frozen=True)
class FullScanWorkerResult:
    status: str
    job_id: str | None = None
    attempt_id: str | None = None
    processed_cases: int = 0
    final_status: str | None = None
    final_manifest_hash: str | None = None


class FullScanQueueWorker:
    """Lease, resume, and finalize durable full-scan jobs one attempt at a time."""

    def __init__(
        self,
        store: FullScanJobStore,
        *,
        registry_rows: Sequence[Mapping[str, Any]],
        workers: Mapping[str, FrameworkWorker],
        lease_ttl_seconds: int = 300,
        max_recovery_attempts: int = DEFAULT_MAX_RECOVERY_ATTEMPTS,
    ) -> None:
        if lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be positive")
        self._store = store
        self._registry_rows = tuple(dict(row) for row in registry_rows)
        self._workers = dict(workers)
        self._lease_ttl_seconds = lease_ttl_seconds
        self._max_recovery_attempts = max_recovery_attempts

    def recover(self) -> dict[str, Any]:
        return self._store.recover(max_recovery_attempts=self._max_recovery_attempts)

    def run_once(self, *, max_cases: int | None = None) -> FullScanWorkerResult:
        """Run one durable job.

        ``max_cases`` is a test/ops escape hatch for simulating worker death
        after a bounded number of case checkpoints. When it stops early the job
        remains running with an active lease; a later process calls
        :meth:`recover`, clears that dead lease, and resumes only pending cases.
        """
        if max_cases is not None and max_cases <= 0:
            raise ValueError("max_cases must be positive")

        lease = self._store.lease_next(ttl_seconds=self._lease_ttl_seconds)
        if lease is None:
            return FullScanWorkerResult(status="idle")

        job_id = str(lease["job_id"])
        attempt_id = str(lease["attempt_id"])
        processed = 0

        while True:
            pending = self._pending_registry_rows(job_id)
            if not pending:
                finalized = self._store.finalize(job_id, attempt_id=attempt_id)
                return FullScanWorkerResult(
                    status="finalized",
                    job_id=job_id,
                    attempt_id=attempt_id,
                    processed_cases=processed,
                    final_status=finalized.status,
                    final_manifest_hash=finalized.final_manifest_hash,
                )

            row = pending[0]
            dispatch_registry_attempt(
                self._store,
                job_id,
                attempt_id=attempt_id,
                registry_rows=(row,),
                workers=self._workers,
            )
            processed += 1

            if max_cases is not None and processed >= max_cases:
                return FullScanWorkerResult(
                    status="interrupted",
                    job_id=job_id,
                    attempt_id=attempt_id,
                    processed_cases=processed,
                )

    def _pending_registry_rows(self, job_id: str) -> list[Mapping[str, Any]]:
        resume = self._store.resume(job_id)
        terminal_case_ids = {
            str(row["probe_id"])
            for row in resume["evidence_rows"]
            if isinstance(row, Mapping)
            and row.get("status") in {"complete", "incomplete", "failed", "skipped"}
        }
        return [
            row
            for row in self._registry_rows
            if row.get("status") == "active"
            and row.get("production_runner_executable") is True
            and str(row.get("case_id") or "") not in terminal_case_ids
        ]


__all__ = ["FullScanQueueWorker", "FullScanWorkerResult"]
