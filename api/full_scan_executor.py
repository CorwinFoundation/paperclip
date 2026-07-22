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
from typing import Any, Callable, Iterable, Mapping, Sequence

from api.bundle import sign_manifest
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


ASYNC_FULL_SCAN_API_VERSION = "scanner-full-scan-async/v0.1"
ATTEMPT_MANIFEST_VERSION = "full-scan-attempt-manifest/v0.1"
TERMINAL_JOB_STATUSES = {"complete", "incomplete", "failed", "cancelled"}
TERMINAL_PROBE_STATUSES = {"complete", "skipped", "failed"}
RECONCILED_TERMINAL_PROBE_STATUSES = TERMINAL_PROBE_STATUSES | {"incomplete"}


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


FrameworkWorker = Callable[[Mapping[str, Any], FullScanJob], WorkerAttemptResult]


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
        "contract_version": "are-scanner-api-v1@v1.0",
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
        "contract_version": "are-scanner-api-v1@v1.0",
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


def registry_cases_from_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[FullSuiteCase, ...]:
    """Convert canonical active-test registry rows into unique executable cases."""
    cases: list[FullSuiteCase] = []
    seen: set[str] = set()
    for row in rows:
        if row.get("status") != "active":
            continue
        if row.get("production_runner_executable") is not True:
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
        if registry_row.get("production_runner_executable") is not True:
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
    rows = tuple(evidence_rows)
    exact = {row.probe_id for row in rows if row.status in TERMINAL_PROBE_STATUSES}
    rubric_counts: dict[str, int] = {}
    for case in cases:
        rubric_counts[case.rubric_point_id] = rubric_counts.get(case.rubric_point_id, 0) + 1
    unique_terminal_rubrics = {
        row.rubric_point_id
        for row in rows
        if row.status in TERMINAL_PROBE_STATUSES
        and rubric_counts.get(row.rubric_point_id) == 1
    }
    return {
        case.case_id
        for case in cases
        if case.case_id in exact or case.rubric_point_id in unique_terminal_rubrics
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
