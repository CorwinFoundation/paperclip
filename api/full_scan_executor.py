"""Async full-scan job executor and signed attempt manifest.

BEAAA-13681 rule-execution scope: this module adds a resumable job model for
full underwriting scans. It does not change rubric weights, scoring math,
judge selection, or the byte-locked BackBond Scanner API v1.0 score envelope.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

from api.bundle import sign_manifest

ASYNC_FULL_SCAN_API_VERSION = "scanner-full-scan-async/v0.1"
ATTEMPT_MANIFEST_VERSION = "full-scan-attempt-manifest/v0.1"
FULL_SUITE_MANIFEST_VERSION = "full-suite-execution-manifest/v0.1"
FULL_SUITE_RUNNER_VERSION = "full-suite-runner/v0.1"
TERMINAL_JOB_STATUSES = {"complete", "incomplete", "failed", "cancelled"}
TERMINAL_PROBE_STATUSES = {"complete", "skipped", "failed"}


class FullScanExecutorError(ValueError):
    pass


@dataclass(frozen=True)
class FullSuiteCase:
    case_id: str
    rubric_point_id: str
    required: bool = True
    approved_skip_reasons: tuple[str, ...] = ()

    def to_registry_row(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "rubric_point_id": self.rubric_point_id,
            "required": self.required,
            "approved_skip_reasons": list(self.approved_skip_reasons),
        }


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
        }


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


class FullScanJobStore:
    """In-memory async full-scan state store.

    Production can swap this surface for a durable DB-backed store without
    changing the executor semantics: submit, status, lease, checkpoint, resume,
    finalize.
    """

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or _utcnow
        self._jobs: dict[str, FullScanJob] = {}
        self._idempotency_index: dict[str, str] = {}

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
    ) -> FullScanJob:
        cases = tuple(registry_cases)
        if not cases:
            raise FullScanExecutorError("registry_cases must not be empty")
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
        )
        self._jobs[job.job_id] = job
        if idempotency_key:
            self._idempotency_index[idempotency_key] = job.job_id
        return job

    def status(self, job_id: str) -> FullScanJob:
        return self._get(job_id)

    def lease(self, job_id: str, *, ttl_seconds: int = 300) -> dict[str, Any]:
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
        self._jobs[job_id] = updated
        return {
            "job_id": job.job_id,
            "leased": True,
            "attempt_id": attempt_id,
            "lease_expires_at": _iso(lease_expires_at),
        }

    def checkpoint(
        self,
        job_id: str,
        *,
        attempt_id: str,
        rows: Iterable[EvidenceAttemptRow],
    ) -> FullScanJob:
        job = self._get(job_id)
        if job.status in TERMINAL_JOB_STATUSES:
            raise FullScanExecutorError("cannot checkpoint terminal job")
        if attempt_id not in job.attempts:
            raise FullScanExecutorError("unknown attempt_id")
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
        self._jobs[job_id] = updated
        return updated

    def resume(self, job_id: str) -> dict[str, Any]:
        job = self._get(job_id)
        completed = {
            row.rubric_point_id
            for row in job.evidence_rows
            if row.status in TERMINAL_PROBE_STATUSES
        }
        pending = [
            case.to_registry_row()
            for case in job.registry_cases
            if case.required and case.rubric_point_id not in completed
        ]
        return {
            "job_id": job.job_id,
            "status": job.status,
            "active_attempt_id": job.active_attempt_id,
            "attempts": list(job.attempts),
            "evidence_rows": [row.to_manifest_row() for row in job.evidence_rows],
            "pending_cases": pending,
        }

    def finalize(self, job_id: str) -> FullScanJob:
        job = self._get(job_id)
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
        self._jobs[job_id] = updated
        return updated

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
    completed = {
        row["rubric_point_id"]
        for row in rows
        if row["status"] in TERMINAL_PROBE_STATUSES
    }
    missing_required = [
        case.rubric_point_id
        for case in job.registry_cases
        if case.required and case.rubric_point_id not in completed
    ]
    incomplete_reasons = [
        f"required_case_not_attempted:{rubric_point_id}"
        for rubric_point_id in missing_required
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
        "agent_id": job.agent_id,
        "scanner_version": job.scanner_version,
        "corpus_version": job.corpus_version,
        "model_versions": dict(job.model_versions),
        "seed": job.seed,
        "attempts": list(job.attempts),
        "registry_snapshot_hash": registry_snapshot_hash(job.registry_cases),
        "evidence_rows": rows,
        "counts": {
            "expected": len(job.registry_cases),
            "evidence_rows": len(rows),
            "missing_required": len(missing_required),
        },
        "incomplete": bool(incomplete_reasons),
        "incomplete_reasons": incomplete_reasons,
        "approved_blocker_ref": job.approved_blocker_ref,
        "bind_eligibility": _bind_eligibility_from_missing(
            missing_required,
            approved_blocker_ref=job.approved_blocker_ref,
        ),
        "finished_at": _iso(finished_at),
    }
    signed = dict(manifest)
    signed["signed_manifest"] = sign_manifest(manifest)
    signed["manifest_hash"] = manifest_content_hash(signed)
    return signed


def manifest_content_hash(manifest: Mapping[str, Any]) -> str:
    """Hash manifest content excluding detached signature/hash fields."""
    payload = {
        key: value
        for key, value in manifest.items()
        if key not in {"signed_manifest", "manifest_hash"}
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def registry_snapshot_hash(cases: Iterable[FullSuiteCase]) -> str:
    rows = [case.to_registry_row() for case in cases]
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _bind_eligibility(job: FullScanJob, manifest: Mapping[str, Any]) -> str:
    missing = [
        reason.removeprefix("required_case_not_attempted:")
        for reason in manifest.get("incomplete_reasons", [])
        if isinstance(reason, str)
        and reason.startswith("required_case_not_attempted:")
    ]
    return _bind_eligibility_from_missing(
        missing,
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


def _row_key(row: EvidenceAttemptRow | Mapping[str, Any]) -> tuple[str, int]:
    if isinstance(row, EvidenceAttemptRow):
        return (row.rubric_point_id, row.seed)
    return (str(row["rubric_point_id"]), int(row["seed"]))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
