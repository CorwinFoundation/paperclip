"""Async full-scan job executor and signed attempt manifest.

BEAAA-13681 rule-execution scope: this module adds a resumable job model for
full underwriting scans. It does not change rubric weights, scoring math,
judge selection, or the byte-locked BackBond Scanner API v1.0 score envelope.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable, Iterable, Mapping, Sequence

from api.bundle import sign_manifest
from api.full_scan_job_persistence import FullScanJobPersistence
from api.full_suite_manifest import (
    FULL_SUITE_MANIFEST_VERSION,
    FULL_SUITE_RUNNER_VERSION,
    FullSuiteCase,
    registry_snapshot_hash,
)
from tools.framework_worker_registry import WORKERS, worker_id_for_registry_row
from tools.framework_workers import build_default_framework_workers
from tools.active_test_registry import _is_countable_active_executable
from rubric.bands import assign_band
from scanners import CONTRACT_VERSION


ASYNC_FULL_SCAN_API_VERSION = "scanner-full-scan-async/v0.1"
ATTEMPT_MANIFEST_VERSION = "full-scan-attempt-manifest/v0.1"
TERMINAL_JOB_STATUSES = {"complete", "incomplete", "failed", "cancelled"}
TERMINAL_PROBE_STATUSES = {"complete", "skipped", "failed"}
RECONCILED_TERMINAL_PROBE_STATUSES = TERMINAL_PROBE_STATUSES | {"incomplete"}

# BEAAA-14401 — a recovered job whose stale attempt is cleared is re-queued for
# resume. A job that cannot make progress across this many restart recoveries is
# finalized to an explicit terminal failure so a poison job never resumes
# forever (acceptance: "resume OR reach an explicit terminal failure").
DEFAULT_MAX_RECOVERY_ATTEMPTS = 5
RESTART_RECOVERY_INCOMPLETE_REASON = "process_restart_recovery_exhausted"


class FullScanExecutorError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceAttemptRow:
    """Per-probe evidence row required by BEAAA-13681 acceptance."""

    probe_id: str
    rubric_point_id: str
    status: str
    framework: str
    corpus_version: str
    model_version: str
    seed: int
    evidence_ref: str | None
    incomplete_reason: str | None = None
    test_id: str = ""
    verdict: str | None = None
    target_calls: int = 0
    judge_calls: int = 0
    estimated_judge_input_tokens: int = 0
    estimated_judge_output_tokens: int = 0
    estimated_judge_cost_usd: float = 0.0
    duration_ms: int = 0

    def to_manifest_row(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "rubric_point_id": self.rubric_point_id,
            "status": self.status,
            "framework": self.framework,
            "corpus_version": self.corpus_version,
            "model_version": self.model_version,
            "seed": self.seed,
            "evidence_ref": self.evidence_ref,
            "incomplete_reason": self.incomplete_reason,
            "test_id": self.test_id,
            "verdict": self.verdict,
            "target_calls": self.target_calls,
            "judge_calls": self.judge_calls,
            "estimated_judge_input_tokens": self.estimated_judge_input_tokens,
            "estimated_judge_output_tokens": self.estimated_judge_output_tokens,
            "estimated_judge_cost_usd": self.estimated_judge_cost_usd,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class WorkerAttemptResult:
    """Normalized result returned by a framework worker boundary."""

    status: str
    evidence_ref: str | None
    incomplete_reason: str | None = None
    model_version: str | None = None
    verdict: str | None = None
    target_calls: int = 0
    judge_calls: int = 0
    estimated_judge_input_tokens: int = 0
    estimated_judge_output_tokens: int = 0
    estimated_judge_cost_usd: float = 0.0
    duration_ms: int = 0


@dataclass(frozen=True)
class ControlledFamilyRun:
    """N=5 controlled-agent family result for release-band validation."""

    agent_id: str
    family: str
    scores: tuple[int, int, int, int, int]
    expected_bands: tuple[str, ...]
    disposition: str = "required"
    expected_failure: bool = False


@dataclass(frozen=True)
class FullScanValidationError:
    code: str
    detail: str


@dataclass(frozen=True)
class FullScanJob:
    job_id: str
    agent_id: str
    scanner_version: str
    corpus_version: str
    model_versions: Mapping[str, Any]
    seed: int
    registry_cases: tuple[FullSuiteCase, ...]
    status: str = "queued"
    attempts: tuple[str, ...] = ()
    active_attempt_id: str | None = None
    lease_expires_at: datetime | None = None
    evidence_rows: tuple[EvidenceAttemptRow, ...] = ()
    approved_blocker_ref: str | None = None
    final_manifest: Mapping[str, Any] | None = None
    final_manifest_hash: str | None = None
    bind_eligibility: str = "blocked_base_suite_incomplete"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    wall_clock_budget_seconds: int | None = None
    rate_limit_per_minute: int | None = None
    terminal_webhook: Mapping[str, Any] | None = None
    terminal_webhook_delivered_at: datetime | None = None
    # BEAAA-14401 — number of times a restart recovery sweep has re-queued this
    # job after clearing a stale (dead-worker) attempt. Bounded by
    # ``DEFAULT_MAX_RECOVERY_ATTEMPTS`` so an unresumable job reaches a terminal
    # failure instead of looping forever.
    recovery_count: int = 0


FrameworkWorker = Callable[[Mapping[str, Any], FullScanJob], WorkerAttemptResult]

class FullScanJobStore:
    """Async full-scan state store with optional durable persistence.

    The in-memory dict remains the hot read/write path (fast, lock-guarded).
    When a ``persistence`` backend is supplied (BEAAA-14401), every job state
    transition is also written through to durable storage, and existing jobs +
    the idempotency index are loaded back on construction. This is how an
    accepted job survives a process restart: ``submit``, ``lease``,
    ``checkpoint``, ``finalize`` all persist, and :meth:`recover` sweeps
    non-terminal jobs at startup so each either resumes or reaches an explicit
    terminal failure — it never disappears.

    ``persistence=None`` (the default) is byte-for-byte the legacy in-memory
    behavior: no I/O, identical semantics.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        persistence: "FullScanJobPersistence | None" = None,
    ) -> None:
        self._clock = clock or _utcnow
        self._jobs: dict[str, FullScanJob] = {}
        self._idempotency_index: dict[str, str] = {}
        self._lock = RLock()
        self._persistence = persistence
        if persistence is not None:
            for record in persistence.load_jobs():
                job = _job_from_record(record)
                self._jobs[job.job_id] = job
            self._idempotency_index.update(persistence.load_idempotency())

    def _persist(self, job: FullScanJob) -> None:
        """Update the in-memory job and write it through to durable storage."""
        self._jobs[job.job_id] = job
        if self._persistence is not None:
            self._persistence.save(
                job_id=job.job_id,
                status=job.status,
                updated_at=_iso(job.updated_at) if job.updated_at else None,
                record=_job_to_record(job),
            )

    def submit(
        self,
        *,
        agent_id: str,
        scanner_version: str,
        corpus_version: str,
        model_versions: Mapping[str, Any],
        seed: int,
        registry_cases: Iterable[FullSuiteCase],
        idempotency_key: str | None = None,
        approved_blocker_ref: str | None = None,
        wall_clock_budget_seconds: int | None = None,
        rate_limit_per_minute: int | None = None,
        terminal_webhook: Mapping[str, Any] | None = None,
    ) -> FullScanJob:
        with self._lock:
            cases = tuple(registry_cases)
            if not cases:
                raise FullScanExecutorError("registry_cases must not be empty")
            if wall_clock_budget_seconds is not None and wall_clock_budget_seconds <= 0:
                raise FullScanExecutorError("wall_clock_budget_seconds must be positive")
            if rate_limit_per_minute is not None and rate_limit_per_minute <= 0:
                raise FullScanExecutorError("rate_limit_per_minute must be positive")
            if idempotency_key and idempotency_key in self._idempotency_index:
                return self._jobs[self._idempotency_index[idempotency_key]]
            now = self._clock()
            job = FullScanJob(
                job_id=str(uuid.uuid4()),
                agent_id=agent_id,
                scanner_version=scanner_version,
                corpus_version=corpus_version,
                model_versions=dict(model_versions),
                seed=seed,
                registry_cases=cases,
                approved_blocker_ref=approved_blocker_ref,
                created_at=now,
                updated_at=now,
                wall_clock_budget_seconds=wall_clock_budget_seconds,
                rate_limit_per_minute=rate_limit_per_minute,
                terminal_webhook=(
                    dict(terminal_webhook) if terminal_webhook is not None else None
                ),
            )
            self._jobs[job.job_id] = job
            if self._persistence is not None:
                self._persistence.save_accepted_job(
                    job_id=job.job_id,
                    status=job.status,
                    updated_at=_iso(job.updated_at) if job.updated_at else None,
                    record=_job_to_record(job),
                    idempotency_key=idempotency_key,
                )
            if idempotency_key:
                self._idempotency_index[idempotency_key] = job.job_id
            return job

    def status(self, job_id: str) -> FullScanJob:
        with self._lock:
            return self._get(job_id)

    def lease(self, job_id: str, *, ttl_seconds: int = 300) -> dict[str, Any]:
        with self._lock:
            if ttl_seconds <= 0:
                raise FullScanExecutorError("ttl_seconds must be positive")
            job = self._get(job_id)
            now = self._clock()
            if job.status in TERMINAL_JOB_STATUSES:
                raise FullScanExecutorError("cannot lease terminal job")
            if job.active_attempt_id and job.lease_expires_at and job.lease_expires_at > now:
                return {
                    "job_id": job.job_id,
                    "leased": False,
                    "active_attempt_id": job.active_attempt_id,
                    "lease_expires_at": _iso(job.lease_expires_at),
                }
            attempt_id = str(uuid.uuid4())
            lease_expires_at = now + timedelta(seconds=ttl_seconds)
            updated = replace(
                job,
                status="running",
                attempts=job.attempts + (attempt_id,),
                active_attempt_id=attempt_id,
                lease_expires_at=lease_expires_at,
                updated_at=now,
            )
            self._persist(updated)
            return {
                "job_id": job.job_id,
                "leased": True,
                "attempt_id": attempt_id,
                "lease_expires_at": _iso(lease_expires_at),
            }

    def queued_job_ids(self) -> list[str]:
        """Return resumable, non-terminal job ids in deterministic FIFO order."""
        with self._lock:
            now = self._clock()
            candidates = [
                job
                for job in self._jobs.values()
                if job.status not in TERMINAL_JOB_STATUSES
                and (
                    job.active_attempt_id is None
                    or job.lease_expires_at is None
                    or job.lease_expires_at <= now
                )
            ]
            return [
                job.job_id
                for job in sorted(
                    candidates,
                    key=lambda job: (
                        job.created_at or datetime.min.replace(tzinfo=timezone.utc),
                        job.job_id,
                    ),
                )
            ]

    def lease_next(self, *, ttl_seconds: int = 300) -> dict[str, Any] | None:
        """Lease the next resumable queued job, or ``None`` if no job is ready."""
        with self._lock:
            for job_id in self.queued_job_ids():
                try:
                    lease = self.lease(job_id, ttl_seconds=ttl_seconds)
                except FullScanExecutorError:
                    continue
                if lease.get("leased") is True:
                    return lease
            return None

    def terminal_webhook_pending_jobs(self) -> list[FullScanJob]:
        """Return terminal jobs whose configured webhook is not yet delivered."""
        with self._lock:
            pending = [
                job
                for job in self._jobs.values()
                if job.status in TERMINAL_JOB_STATUSES
                and job.terminal_webhook is not None
                and job.terminal_webhook_delivered_at is None
            ]
            return sorted(
                pending,
                key=lambda job: (
                    job.updated_at or datetime.min.replace(tzinfo=timezone.utc),
                    job.job_id,
                ),
            )

    def checkpoint(
        self,
        job_id: str,
        *,
        attempt_id: str,
        rows: Iterable[EvidenceAttemptRow],
    ) -> FullScanJob:
        with self._lock:
            job = self._get(job_id)
            if job.status in TERMINAL_JOB_STATUSES:
                raise FullScanExecutorError("cannot checkpoint terminal job")
            self._require_active_lease(job, attempt_id=attempt_id)
            merged = {_row_key(row): row for row in job.evidence_rows}
            for row in rows:
                _validate_evidence_row(row)
                merged[_row_key(row)] = row
            updated = replace(
                job,
                status="running",
                evidence_rows=tuple(merged[key] for key in sorted(merged)),
                updated_at=self._clock(),
            )
            self._persist(updated)
            return updated

    def resume(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._get(job_id)
            completed = _satisfied_case_ids(job.registry_cases, job.evidence_rows)
            pending = [
                case.to_registry_row()
                for case in job.registry_cases
                if case.required and case.case_id not in completed
            ]
            return {
                "job_id": job.job_id,
                "status": job.status,
                "active_attempt_id": job.active_attempt_id,
                "attempts": list(job.attempts),
                "evidence_rows": [row.to_manifest_row() for row in job.evidence_rows],
                "pending_cases": pending,
            }

    def finalize(
        self, job_id: str, *, attempt_id: str | None = None
    ) -> FullScanJob:
        with self._lock:
            job = self._get(job_id)
            active_attempt_id = attempt_id or job.active_attempt_id
            if active_attempt_id is None:
                raise FullScanExecutorError("job has no active attempt")
            self._require_active_lease(job, attempt_id=active_attempt_id)
            manifest = build_attempt_manifest(job, finished_at=self._clock())
            manifest_hash = manifest_content_hash(manifest)
            bind_eligibility = _bind_eligibility(job, manifest)
            final_status = "complete" if bind_eligibility == "eligible" else "incomplete"
            updated = replace(
                job,
                status=final_status,
                active_attempt_id=None,
                lease_expires_at=None,
                final_manifest=manifest,
                final_manifest_hash=manifest_hash,
                bind_eligibility=bind_eligibility,
                updated_at=self._clock(),
            )
            self._persist(updated)
            return updated

    def recover(
        self,
        *,
        max_recovery_attempts: int = DEFAULT_MAX_RECOVERY_ATTEMPTS,
    ) -> dict[str, Any]:
        """Restart-recovery sweep — run once at process startup.

        Every persisted job has already been loaded into ``self._jobs`` by the
        constructor, so no accepted job disappears on restart. This sweep then
        makes each non-terminal job actionable again:

        * A job that was ``running`` when the process died holds a stale
          ``active_attempt_id`` whose worker thread/process no longer exists.
          The dead attempt is cleared and the job is re-queued (``status`` ->
          ``queued``), so the next :meth:`lease` can resume it. ``recovery_count``
          is incremented.
        * A job that exhausts ``max_recovery_attempts`` without completing is
          finalized to an explicit terminal ``failed`` record — it never
          resumes forever.
        * A job that was cleanly ``queued`` (never leased) is already
          resumable; it is left untouched (it survived).

        Terminal jobs (already complete/incomplete/failed/cancelled) are
        durable terminal records and are skipped. Returns a summary of the
        job_ids resumed vs. failed for the startup audit log.
        """
        with self._lock:
            resumed: list[str] = []
            failed: list[str] = []
            for job_id, job in list(self._jobs.items()):
                if job.status in TERMINAL_JOB_STATUSES:
                    continue
                # Cleanly queued (never leased) — already resumable, survived.
                if job.active_attempt_id is None and job.status == "queued":
                    continue
                if job.recovery_count + 1 > max_recovery_attempts:
                    self._finalize_stranded(
                        job, reason=RESTART_RECOVERY_INCOMPLETE_REASON
                    )
                    failed.append(job_id)
                    continue
                recovered = replace(
                    job,
                    status="queued",
                    active_attempt_id=None,
                    lease_expires_at=None,
                    recovery_count=job.recovery_count + 1,
                    updated_at=self._clock(),
                )
                self._persist(recovered)
                resumed.append(job_id)
            return {"resumed": resumed, "failed": failed}

    def finalize_stranded(self, job_id: str, *, reason: str) -> FullScanJob:
        """Force a non-terminal job to an explicit terminal ``failed`` record.

        Used when a stranded job cannot resume (dependency permanently gone,
        recovery budget exhausted). Signs the attempt manifest from whatever
        evidence exists so the terminal record is auditable. No-op equivalence:
        a lease is not required — recovery owns the job.
        """
        with self._lock:
            job = self._get(job_id)
            if job.status in TERMINAL_JOB_STATUSES:
                return job
            return self._finalize_stranded(job, reason=reason)

    def mark_terminal_webhook_delivered(self, job_id: str) -> FullScanJob:
        """Persist the terminal webhook delivery marker.

        The marker is intentionally separate from finalization so a failed
        secret resolution or HTTP post leaves the terminal job retryable after
        restart.
        """
        with self._lock:
            job = self._get(job_id)
            if job.terminal_webhook_delivered_at is not None:
                return job
            now = self._clock()
            updated = replace(
                job,
                terminal_webhook_delivered_at=now,
                updated_at=now,
            )
            self._persist(updated)
            return updated

    def _finalize_stranded(self, job: FullScanJob, *, reason: str) -> FullScanJob:
        manifest = build_attempt_manifest(job, finished_at=self._clock())
        updated = replace(
            job,
            status="failed",
            active_attempt_id=None,
            lease_expires_at=None,
            final_manifest=manifest,
            final_manifest_hash=manifest_content_hash(manifest),
            bind_eligibility=_bind_eligibility(job, manifest),
            updated_at=self._clock(),
        )
        self._persist(updated)
        return updated

    def _require_active_lease(self, job: FullScanJob, *, attempt_id: str) -> None:
        if attempt_id not in job.attempts:
            raise FullScanExecutorError("unknown attempt_id")
        if job.active_attempt_id != attempt_id:
            raise FullScanExecutorError("attempt_id is not active")
        if job.lease_expires_at is None or job.lease_expires_at <= self._clock():
            raise FullScanExecutorError("attempt lease expired")

    def _get(self, job_id: str) -> FullScanJob:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise FullScanExecutorError("job_not_found") from exc


def build_attempt_manifest(
    job: FullScanJob,
    *,
    finished_at: datetime | None = None,
) -> dict[str, Any]:
    finished_at = finished_at or _utcnow()
    rows = [row.to_manifest_row() for row in sorted(job.evidence_rows, key=_row_key)]
    expected_cases = [
        case.to_registry_row()
        for case in sorted(job.registry_cases, key=lambda case: case.case_id)
    ]
    completed = _satisfied_case_ids(job.registry_cases, job.evidence_rows)
    missing_required = [
        case.case_id
        for case in job.registry_cases
        if case.required and case.case_id not in completed
    ]
    verdict_counts = Counter(
        str(row.get("verdict") or "unresolved") for row in rows
    )
    status_counts = Counter(str(row.get("status") or "unknown") for row in rows)
    budget = {
        "target_calls": sum(int(row.get("target_calls") or 0) for row in rows),
        "judge_calls": sum(int(row.get("judge_calls") or 0) for row in rows),
        "estimated_judge_input_tokens": sum(
            int(row.get("estimated_judge_input_tokens") or 0) for row in rows
        ),
        "estimated_judge_output_tokens": sum(
            int(row.get("estimated_judge_output_tokens") or 0) for row in rows
        ),
        "estimated_judge_cost_usd": round(
            sum(float(row.get("estimated_judge_cost_usd") or 0.0) for row in rows),
            6,
        ),
        "wall_clock_ms": sum(int(row.get("duration_ms") or 0) for row in rows),
        "limits": dict(job.model_versions.get("execution_budget") or {}),
    }
    incomplete_reasons = [
        f"required_case_not_attempted:{case_id}"
        for case_id in missing_required
    ]
    for row in rows:
        if row["status"] != "complete" and row.get("incomplete_reason"):
            incomplete_reasons.append(
                f"{row['probe_id']}:{row['incomplete_reason']}"
            )
    manifest: dict[str, Any] = {
        "api_version": ASYNC_FULL_SCAN_API_VERSION,
        "manifest_version": ATTEMPT_MANIFEST_VERSION,
        "full_suite_manifest_version": FULL_SUITE_MANIFEST_VERSION,
        "runner_version": FULL_SUITE_RUNNER_VERSION,
        "job_id": job.job_id,
        "manifest_evidence_ref": f"evidence://{job.job_id}/manifest.json",
        "agent_id": job.agent_id,
        "scanner_version": job.scanner_version,
        "corpus_version": job.corpus_version,
        "model_versions": dict(job.model_versions),
        "seed": job.seed,
        "attempts": list(job.attempts),
        "registry_snapshot_hash": registry_snapshot_hash(job.registry_cases),
        "expected_cases": expected_cases,
        "evidence_rows": rows,
        "counts": {
            "expected": len(job.registry_cases),
            "evidence_rows": len(rows),
            "missing_required": len(missing_required),
        },
        "reconciliation": {
            "attempted": len(rows),
            "terminal": sum(
                count
                for status, count in status_counts.items()
                if status in RECONCILED_TERMINAL_PROBE_STATUSES
            ),
            "by_status": dict(sorted(status_counts.items())),
        },
        "verdict_counts": dict(sorted(verdict_counts.items())),
        "budget": budget,
        "analysis_status": (
            "complete" if not incomplete_reasons else "analysis_incomplete"
        ),
        "incomplete": bool(incomplete_reasons),
        "incomplete_reasons": incomplete_reasons,
        "approved_blocker_ref": job.approved_blocker_ref,
        "execution_budget": {
            "wall_clock_budget_seconds": job.wall_clock_budget_seconds,
            "rate_limit_per_minute": job.rate_limit_per_minute,
        },
        "bind_eligibility": _bind_eligibility_from_missing(
            incomplete_reasons,
            approved_blocker_ref=job.approved_blocker_ref,
        ),
        "finished_at": _iso(finished_at),
    }
    signed = dict(manifest)
    signed["signed_manifest"] = sign_manifest(manifest)
    signed["manifest_hash"] = manifest_content_hash(signed)
    return signed


def build_controlled_validation_report(
    runs: Iterable[ControlledFamilyRun],
) -> dict[str, Any]:
    """Validate known-good/known-bad N=5 family bands for CI.

    The inputs are already scored by the controlled fixture runner. This helper
    only checks repeatability and expected band placement, so it does not alter
    score math or rubric behavior.
    """
    rows: list[dict[str, Any]] = []
    errors: list[FullScanValidationError] = []
    quarantined: list[str] = []

    for run in runs:
        if len(run.scores) != 5:
            errors.append(
                FullScanValidationError(
                    "controlled_run_not_n5",
                    f"{run.agent_id}:{run.family} has {len(run.scores)} scores",
                )
            )
            continue
        observed_bands = tuple(assign_band(score).letter for score in run.scores)
        unique_bands = sorted(set(observed_bands))
        expected = set(run.expected_bands)
        band_match = bool(expected) and set(observed_bands).issubset(expected)
        spread = max(run.scores) - min(run.scores)
        disposition = run.disposition

        if disposition == "quarantined":
            quarantined.append(f"{run.agent_id}:{run.family}")
        elif not band_match:
            errors.append(
                FullScanValidationError(
                    "controlled_band_mismatch",
                    (
                        f"{run.agent_id}:{run.family} observed {unique_bands} "
                        f"outside expected {sorted(expected)}"
                    ),
                )
            )
        elif run.expected_failure and not set(observed_bands).issubset({"C", "D"}):
            errors.append(
                FullScanValidationError(
                    "known_bad_did_not_fail",
                    f"{run.agent_id}:{run.family} known-bad bands {unique_bands}",
                )
            )

        rows.append(
            {
                "agent_id": run.agent_id,
                "family": run.family,
                "scores": list(run.scores),
                "n": 5,
                "spread": spread,
                "observed_bands": list(observed_bands),
                "expected_bands": list(run.expected_bands),
                "expected_failure": run.expected_failure,
                "disposition": disposition,
                "gate": "quarantined" if disposition == "quarantined" else "pass" if band_match else "fail",
            }
        )

    return {
        "report_version": "controlled-full-scan-validation/v0.1",
        "contract_version": CONTRACT_VERSION,
        "n_replicates": 5,
        "rows": rows,
        "quarantined": quarantined,
        "errors": [error.__dict__ for error in errors],
        "passed": not errors,
    }


def validate_full_scan_release_gate(
    *,
    active_registry_rows: Iterable[Mapping[str, Any]],
    worker_manifest: Mapping[str, Any],
    attempt_manifest: Mapping[str, Any],
    verified_registry_versions: Mapping[str, str] | None = None,
    verified_worker_versions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Fail if active registry cases vanish from expected/attempt/evidence rows.

    Optional ``verified_*`` pins let CI compare the deployed registry and worker
    metadata against the exact pins used during validation.
    """
    verified_registry_versions = dict(verified_registry_versions or {})
    verified_worker_versions = dict(verified_worker_versions or {})
    active_rows = [
        row
        for row in active_registry_rows
        if _is_countable_active_executable(row)
    ]
    active_case_ids = {_case_id_from_row(row) for row in active_rows}
    active_case_ids.discard("")
    expected_case_ids = {
        str(row.get("case_id") or "")
        for row in attempt_manifest.get("expected_cases", [])
        if isinstance(row, Mapping)
    }
    attempted_case_ids = {
        str(row.get("probe_id") or "")
        for row in attempt_manifest.get("evidence_rows", [])
        if isinstance(row, Mapping)
    }
    terminal_case_ids = {
        str(row.get("probe_id") or "")
        for row in attempt_manifest.get("evidence_rows", [])
        if isinstance(row, Mapping)
        and row.get("status") in RECONCILED_TERMINAL_PROBE_STATUSES
    }
    evidenced_case_ids = {
        str(row.get("probe_id") or "")
        for row in attempt_manifest.get("evidence_rows", [])
        if isinstance(row, Mapping) and _row_has_evidence(row)
    }

    errors: list[FullScanValidationError] = []
    _append_missing(errors, "active_case_missing_from_expected", active_case_ids - expected_case_ids)
    _append_missing(errors, "active_case_missing_from_attempted", active_case_ids - attempted_case_ids)
    _append_missing(errors, "active_case_missing_from_terminal", active_case_ids - terminal_case_ids)
    _append_missing(errors, "active_case_missing_from_evidenced", active_case_ids - evidenced_case_ids)

    workers_by_id = {
        str(worker.get("worker_id") or ""): worker
        for worker in worker_manifest.get("workers", [])
        if isinstance(worker, Mapping)
    }
    for row in active_rows:
        case_id = _case_id_from_row(row)
        registry_pin = str(row.get("version_pin") or "")
        verified_registry_pin = verified_registry_versions.get(case_id)
        if verified_registry_pin is not None and registry_pin != verified_registry_pin:
            errors.append(
                FullScanValidationError(
                    "registry_version_pin_mismatch",
                    f"{case_id} deployed={registry_pin} verified={verified_registry_pin}",
                )
            )

        worker_id = worker_id_for_registry_row(dict(row))
        worker = workers_by_id.get(worker_id)
        if worker is None:
            errors.append(
                FullScanValidationError(
                    "active_worker_missing_from_manifest",
                    f"{case_id} worker={worker_id}",
                )
            )
            continue
        if worker.get("release_blocker"):
            errors.append(
                FullScanValidationError(
                    "active_worker_release_blocked",
                    f"{case_id} worker={worker_id}: {worker.get('blocker_reason') or ''}",
                )
            )
        verified_worker_pin = verified_worker_versions.get(worker_id)
        worker_version = str(worker.get("framework_version") or "")
        if verified_worker_pin is not None and worker_version != verified_worker_pin:
            errors.append(
                FullScanValidationError(
                    "worker_version_pin_mismatch",
                    f"{worker_id} deployed={worker_version} verified={verified_worker_pin}",
                )
            )

    return {
        "report_version": "full-scan-ci-vanish-gate/v0.1",
        "contract_version": CONTRACT_VERSION,
        "active_case_count": len(active_case_ids),
        "expected_case_count": len(expected_case_ids),
        "attempted_case_count": len(attempted_case_ids),
        "terminal_case_count": len(terminal_case_ids),
        "evidenced_case_count": len(evidenced_case_ids),
        "errors": [error.__dict__ for error in errors],
        "passed": not errors,
    }


def manifest_content_hash(manifest: Mapping[str, Any]) -> str:
    """Hash manifest content excluding detached signature/hash fields."""
    payload = {
        key: value
        for key, value in manifest.items()
        if key not in {"signed_manifest", "manifest_hash"}
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def registry_cases_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    execution_flag: str = "production_runner_executable",
) -> tuple[FullSuiteCase, ...]:
    """Convert selected active-test registry rows into unique executable cases.

    Production callers use the evidence-gated default. Evidence-generation
    workflows may explicitly select another boolean capability flag, such as
    target_bound, without changing the published executed-test count.
    """
    cases: list[FullSuiteCase] = []
    seen: set[str] = set()
    for row in rows:
        if row.get("status") != "active":
            continue
        if row.get(execution_flag) is not True:
            continue
        case_id = _case_id_from_row(row)
        rubric_point_id = str(row.get("risk_or_rubric_id") or row.get("rubric_point_id") or "")
        if not case_id or not rubric_point_id:
            raise FullScanExecutorError("registry row missing case_id or rubric id")
        if case_id in seen:
            raise FullScanExecutorError(f"duplicate executable registry case_id: {case_id}")
        seen.add(case_id)
        cases.append(FullSuiteCase(case_id=case_id, rubric_point_id=rubric_point_id, required=True))
    if not cases:
        raise FullScanExecutorError("no executable active registry rows")
    return tuple(cases)


def dispatch_registry_attempt(
    store: FullScanJobStore,
    job_id: str,
    *,
    attempt_id: str,
    registry_rows: Sequence[Mapping[str, Any]],
    workers: Mapping[str, FrameworkWorker] | None = None,
    execution_flag: str = "production_runner_executable",
) -> FullScanJob:
    """Run one registry-driven attempt and checkpoint terminal evidence rows.

    This is the production boundary between the canonical registry and framework
    workers. A missing worker, dependency failure, timeout, or unsupported target
    interface is recorded as a terminal incomplete evidence row; it is never
    converted to a pass or omitted from reconciliation.
    """
    job = store.status(job_id)
    workers = workers if workers is not None else build_default_framework_workers()
    expected_case_ids = {case.case_id for case in job.registry_cases}
    rows_to_checkpoint: list[EvidenceAttemptRow] = []

    for registry_row in registry_rows:
        if registry_row.get("status") != "active":
            continue
        if registry_row.get(execution_flag) is not True:
            continue
        case_id = _case_id_from_row(registry_row)
        if case_id not in expected_case_ids:
            continue

        worker_id = worker_id_for_registry_row(dict(registry_row))
        worker_definition = WORKERS.get(worker_id)
        framework_version = (
            worker_definition.framework_version
            if worker_definition is not None
            else "worker-definition-missing"
        )
        framework = f"{worker_id}@{framework_version}"
        rubric_point_id = str(
            registry_row.get("risk_or_rubric_id")
            or registry_row.get("rubric_point_id")
            or ""
        )
        model_version = _primary_model_version(job.model_versions)

        worker = workers.get(worker_id)
        if worker is None:
            rows_to_checkpoint.append(
                EvidenceAttemptRow(
                    probe_id=case_id,
                    rubric_point_id=rubric_point_id,
                    status="incomplete",
                    framework=framework,
                    corpus_version=job.corpus_version,
                    model_version=model_version,
                    seed=job.seed,
                    evidence_ref=None,
                    incomplete_reason=f"worker_unavailable:{worker_id}",
                )
            )
            continue

        try:
            result = worker(registry_row, job)
        except TimeoutError:
            result = WorkerAttemptResult(
                status="incomplete",
                evidence_ref=None,
                incomplete_reason=f"worker_timeout:{worker_id}",
            )
        except Exception as exc:  # pragma: no cover - exercised by tests by type only
            result = WorkerAttemptResult(
                status="incomplete",
                evidence_ref=None,
                incomplete_reason=f"worker_error:{worker_id}:{exc.__class__.__name__}",
            )

        rows_to_checkpoint.append(
            EvidenceAttemptRow(
                probe_id=case_id,
                rubric_point_id=rubric_point_id,
                status=result.status,
                framework=framework,
                corpus_version=job.corpus_version,
                model_version=result.model_version or model_version,
                seed=job.seed,
                evidence_ref=result.evidence_ref,
                incomplete_reason=result.incomplete_reason,
                test_id=str(registry_row.get("test_id") or ""),
                verdict=result.verdict,
                target_calls=result.target_calls,
                judge_calls=result.judge_calls,
                estimated_judge_input_tokens=result.estimated_judge_input_tokens,
                estimated_judge_output_tokens=result.estimated_judge_output_tokens,
                estimated_judge_cost_usd=result.estimated_judge_cost_usd,
                duration_ms=result.duration_ms,
            )
        )

    return store.checkpoint(job_id, attempt_id=attempt_id, rows=rows_to_checkpoint)


def _bind_eligibility(job: FullScanJob, manifest: Mapping[str, Any]) -> str:
    return _bind_eligibility_from_missing(
        [
            reason
            for reason in manifest.get("incomplete_reasons", [])
            if isinstance(reason, str)
        ],
        approved_blocker_ref=job.approved_blocker_ref,
    )


def _bind_eligibility_from_missing(
    missing_required: Iterable[str],
    *,
    approved_blocker_ref: str | None,
) -> str:
    missing = list(missing_required)
    if not missing:
        return "eligible"
    if approved_blocker_ref:
        return "blocked_approved_exception"
    return "blocked_base_suite_incomplete"


def _validate_evidence_row(row: EvidenceAttemptRow) -> None:
    if row.status not in {"complete", "incomplete", "failed", "skipped"}:
        raise FullScanExecutorError(f"invalid probe status: {row.status!r}")
    required = {
        "probe_id": row.probe_id,
        "rubric_point_id": row.rubric_point_id,
        "framework": row.framework,
        "corpus_version": row.corpus_version,
        "model_version": row.model_version,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise FullScanExecutorError(
            "evidence row missing required metadata: " + ", ".join(sorted(missing))
        )
    if row.status == "complete" and not row.evidence_ref:
        raise FullScanExecutorError("complete evidence row requires evidence_ref")
    if row.status != "complete" and not row.incomplete_reason:
        raise FullScanExecutorError("non-complete evidence row requires incomplete_reason")


def _case_id_from_row(row: Mapping[str, Any]) -> str:
    return str(
        row.get("runtime_case_identity")
        or row.get("case_id")
        or row.get("case_id_or_probe_id")
        or ""
    )


def _row_has_evidence(row: Mapping[str, Any]) -> bool:
    status = row.get("status")
    if status == "complete":
        return bool(row.get("evidence_ref"))
    if status in {"incomplete", "failed", "skipped"}:
        return bool(row.get("incomplete_reason") or row.get("evidence_ref"))
    return False


def _append_missing(
    errors: list[FullScanValidationError],
    code: str,
    missing: Iterable[str],
) -> None:
    for case_id in sorted(case_id for case_id in missing if case_id):
        errors.append(FullScanValidationError(code, case_id))


def _row_key(row: EvidenceAttemptRow | Mapping[str, Any]) -> tuple[str, int]:
    if isinstance(row, EvidenceAttemptRow):
        return (row.probe_id, row.seed)
    return (str(row["probe_id"]), int(row["seed"]))


def _primary_model_version(model_versions: Mapping[str, Any]) -> str:
    primary = model_versions.get("primary")
    if isinstance(primary, str) and primary:
        return primary
    return "model-version-unavailable"


def _satisfied_case_ids(
    cases: Iterable[FullSuiteCase],
    evidence_rows: Iterable[EvidenceAttemptRow],
) -> set[str]:
    required_case_ids = {case.case_id for case in cases if case.required}
    return {
        row.probe_id
        for row in evidence_rows
        if row.probe_id in required_case_ids
        and row.status == "complete"
        and bool(row.evidence_ref)
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _job_to_record(job: FullScanJob) -> dict[str, Any]:
    """Serialize a ``FullScanJob`` to a JSON-safe record for durable storage.

    The record round-trips exactly back to the same ``FullScanJob`` via
    :func:`_job_from_record` — the persisted state is the store's own output,
    not a re-derivation, so no business logic is duplicated in the DB layer.
    """
    return {
        "job_id": job.job_id,
        "agent_id": job.agent_id,
        "scanner_version": job.scanner_version,
        "corpus_version": job.corpus_version,
        "model_versions": dict(job.model_versions),
        "seed": job.seed,
        "registry_cases": [
            {
                "case_id": case.case_id,
                "rubric_point_id": case.rubric_point_id,
                "required": case.required,
            }
            for case in job.registry_cases
        ],
        "status": job.status,
        "attempts": list(job.attempts),
        "active_attempt_id": job.active_attempt_id,
        "lease_expires_at": _iso(job.lease_expires_at) if job.lease_expires_at else None,
        "evidence_rows": [row.to_manifest_row() for row in job.evidence_rows],
        "approved_blocker_ref": job.approved_blocker_ref,
        "final_manifest": job.final_manifest,
        "final_manifest_hash": job.final_manifest_hash,
        "bind_eligibility": job.bind_eligibility,
        "created_at": _iso(job.created_at) if job.created_at else None,
        "updated_at": _iso(job.updated_at) if job.updated_at else None,
        "wall_clock_budget_seconds": job.wall_clock_budget_seconds,
        "rate_limit_per_minute": job.rate_limit_per_minute,
        "terminal_webhook": (
            dict(job.terminal_webhook) if job.terminal_webhook is not None else None
        ),
        "terminal_webhook_delivered_at": (
            _iso(job.terminal_webhook_delivered_at)
            if job.terminal_webhook_delivered_at
            else None
        ),
        "recovery_count": job.recovery_count,
    }


def _job_from_record(record: Mapping[str, Any]) -> FullScanJob:
    """Reconstruct a ``FullScanJob`` from a durable record."""
    return FullScanJob(
        job_id=record["job_id"],
        agent_id=record["agent_id"],
        scanner_version=record["scanner_version"],
        corpus_version=record["corpus_version"],
        model_versions=dict(record["model_versions"]),
        seed=record["seed"],
        registry_cases=tuple(
            FullSuiteCase(
                case_id=case["case_id"],
                rubric_point_id=case["rubric_point_id"],
                required=case.get("required", True),
            )
            for case in record["registry_cases"]
        ),
        status=record["status"],
        attempts=tuple(record.get("attempts", ())),
        active_attempt_id=record.get("active_attempt_id"),
        lease_expires_at=_parse_iso(record.get("lease_expires_at")),
        evidence_rows=tuple(
            EvidenceAttemptRow(**row) for row in record.get("evidence_rows", ())
        ),
        approved_blocker_ref=record.get("approved_blocker_ref"),
        final_manifest=record.get("final_manifest"),
        final_manifest_hash=record.get("final_manifest_hash"),
        bind_eligibility=record.get(
            "bind_eligibility", "blocked_base_suite_incomplete"
        ),
        created_at=_parse_iso(record.get("created_at")),
        updated_at=_parse_iso(record.get("updated_at")),
        wall_clock_budget_seconds=record.get("wall_clock_budget_seconds"),
        rate_limit_per_minute=record.get("rate_limit_per_minute"),
        terminal_webhook=record.get("terminal_webhook"),
        terminal_webhook_delivered_at=_parse_iso(
            record.get("terminal_webhook_delivered_at")
        ),
        recovery_count=record.get("recovery_count", 0),
    )
