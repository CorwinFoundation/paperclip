"""Async full-scan job executor and signed attempt manifest.

BEAAA-13681 rule-execution scope: this module adds a resumable job model for
full underwriting scans. It does not change rubric weights, scoring math,
judge selection, or the byte-locked BackBond Scanner API v1.0 score envelope.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import RLock, Semaphore
from typing import Any, Callable, Iterable, Mapping, Sequence

from api.attacker_pins import attacker_pins_manifest_block
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
from tools.assessment_test_catalog import FAMILIES as EVIDENCE_FAMILIES


ASYNC_FULL_SCAN_API_VERSION = "scanner-full-scan-async/v0.1"
ATTEMPT_MANIFEST_VERSION = "full-scan-attempt-manifest/v0.1"
TERMINAL_JOB_STATUSES = {"complete", "incomplete", "failed", "cancelled", "incomplete_cost_cap"}
COULD_NOT_RUN_PROBE_STATUS = "could_not_run"
COST_CAP_REACHED_REASON = "cost_cap_reached"
DEFAULT_MAX_RUNNING_JOBS_PER_TARGET = 1
DEFAULT_MAX_INFLIGHT_CASES_PER_TARGET = 3
TERMINAL_PROBE_STATUSES = {"complete", "skipped", "failed"}
# Rows flushed per store write during a dispatch. A process killed mid-dispatch
# loses at most this many paid target/judge attempts.
DEFAULT_DISPATCH_CHECKPOINT_ROWS = 10
RECONCILED_TERMINAL_PROBE_STATUSES = TERMINAL_PROBE_STATUSES | {
    "incomplete",
    COULD_NOT_RUN_PROBE_STATUS,
}
ACQUISITION_BASES = {
    "target-measured",
    "not-applicable",
    "invariant-derived",
    "missing",
    "failed",
}
UNDERWRITING_RELEVANCES = {"direct", "category-proxy", "failed"}
EVIDENCE_DISPOSITIONS = {"report-only"}
_MAX_MANIFEST_EVIDENCE_REF_LENGTH = 512
_MAX_MANIFEST_EVIDENCE_PATH_SEGMENTS = 3
_SAFE_EVIDENCE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,179}$")
_EVIDENCE_DIGEST_FILENAME = re.compile(r"^[0-9a-f]{64}\.json$")


def _manifest_evidence_ref(value: str | None, *, job_id: str | None = None) -> str | None:
    """Preserve only structurally valid refs for this scan; seal the rest."""
    if not value:
        return value
    if _valid_manifest_evidence_ref(value, job_id=job_id):
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _valid_manifest_evidence_ref(value: str, *, job_id: str | None) -> bool:
    """Check the complete worker URI before allowing it into a manifest."""
    if (
        not isinstance(value, str)
        or not isinstance(job_id, str)
        or not job_id
        or len(value) > _MAX_MANIFEST_EVIDENCE_REF_LENGTH
        or "?" in value
        or "#" in value
    ):
        return False

    prefix = f"evidence://{job_id}/"
    if not value.startswith(prefix):
        return False
    path = value[len(prefix) :]
    segments = path.split("/")
    if not 1 <= len(segments) <= _MAX_MANIFEST_EVIDENCE_PATH_SEGMENTS:
        return False
    if any(not _SAFE_EVIDENCE_SEGMENT.fullmatch(segment) for segment in segments):
        return False
    if not segments[-1].endswith(".json"):
        return False

    # Day-one evidence is the only three-segment form and must terminate in
    # the content digest emitted by the in-process writer.
    if len(segments) == 3:
        return segments[0] == "day1" and bool(
            _EVIDENCE_DIGEST_FILENAME.fullmatch(segments[-1])
        )
    return True


# BEAAA-14401 — a recovered job whose stale attempt is cleared is re-queued for
# resume. A job that cannot make progress across this many restart recoveries is
# finalized to an explicit terminal failure so a poison job never resumes
# forever (acceptance: "resume OR reach an explicit terminal failure").
DEFAULT_MAX_RECOVERY_ATTEMPTS = 5
RESTART_RECOVERY_INCOMPLETE_REASON = "process_restart_recovery_exhausted"


class FullScanExecutorError(ValueError):
    pass


class UpstreamQuotaAbort(Exception):
    """Raised when N consecutive upstream auth/quota errors indicate a credential failure.

    The dispatch loop checkpoints all rows collected so far before raising so
    evidence is never lost.  Callers should finalize the partial job and record
    ``abort_reason`` in the run manifest.
    """

    def __init__(self, *, abort_reason: str, rows_processed: int, consecutive: int) -> None:
        super().__init__(abort_reason)
        self.abort_reason = abort_reason
        self.rows_processed = rows_processed
        self.consecutive = consecutive


_UPSTREAM_QUOTA_ABORT_THRESHOLD = 8
_UPSTREAM_QUOTA_RE = re.compile(
    r"^(?:transport_retry_exhausted:)?upstream_http_(401|402|403|429)"
)


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
    seed_binding: str = "unbound"
    incomplete_reason: str | None = None
    test_id: str = ""
    verdict: str | None = None
    target_calls: int = 0
    judge_calls: int = 0
    estimated_judge_input_tokens: int = 0
    estimated_judge_output_tokens: int = 0
    estimated_judge_cost_usd: float = 0.0
    duration_ms: int = 0
    numeric_result: float | None = None
    hit_count: int | None = None
    sample_size: int | None = None
    acquisition_basis: str = "missing"
    underwriting_relevance: str = "failed"
    source_acquisition_basis: str | None = None
    source_underwriting_relevance: str | None = None
    score_contribution: bool = False
    disposition: str = "report-only"
    case_version: str | None = None
    derived_result_id: str | None = None
    derived_result_version: str = "registered-case-result/v1"
    refusal: dict[str, Any] | None = None
    # Populated only for ``applicable_when_capability_declared`` rows that
    # terminate ``not_applicable``: carries the two fields named by the
    # contract's ``na_evidence_required`` list.
    na_evidence: dict[str, Any] | None = None
    # Judge model that produced the verdict for this row (BEAAA-35757).
    # None when no judge was invoked (e.g. not_applicable rows).
    judge_model: str | None = None
    # BEAAA-38957 — observed spend for this row, judge and target separately:
    # ``{"judge_cost_usd", "judge_cost_basis", "target_cost_usd",
    # "target_cost_basis", "estimated_judge_cost_usd",
    # "estimated_target_cost_usd", "estimated"}``. A basis is
    # ``provider_reported`` (usage.cost returned), ``usage_tokens_priced``
    # (token usage returned, priced from the pinned table) or ``estimated``
    # (no usage returned). ``None`` on rows that predate paid packs; the key is
    # then absent from the manifest row so existing manifests are unchanged.
    cost: dict[str, Any] | None = None
    evidence_sha256: str | None = None
    diagnostic_measurements: dict[str, int] | None = None

    def to_manifest_row(self) -> dict[str, Any]:
        row = {
            "probe_id": self.probe_id,
            "rubric_point_id": self.rubric_point_id,
            "status": self.status,
            "framework": self.framework,
            "corpus_version": self.corpus_version,
            "model_version": self.model_version,
            "seed": self.seed,
            "seed_binding": self.seed_binding,
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
            "numeric_result": self.numeric_result,
            "hit_count": self.hit_count,
            "sample_size": self.sample_size,
            "acquisition_basis": self.acquisition_basis,
            "underwriting_relevance": self.underwriting_relevance,
            "source_acquisition_basis": self.source_acquisition_basis,
            "source_underwriting_relevance": self.source_underwriting_relevance,
            "score_contribution": self.score_contribution,
            "disposition": self.disposition,
            "case_version": self.case_version,
            "derived_result_id": self.derived_result_id,
            "derived_result_version": self.derived_result_version,
            "refusal": self.refusal,
            "na_evidence": self.na_evidence,
            "judge_model": self.judge_model,
        }
        if self.cost is not None:
            row["cost"] = dict(self.cost)
        if self.evidence_sha256 is not None:
            row["sha256"] = self.evidence_sha256
        if self.diagnostic_measurements is not None:
            row["diagnostic_measurements"] = dict(self.diagnostic_measurements)
        return row


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
    numeric_result: float | None = None
    hit_count: int | None = None
    sample_size: int | None = None
    seed_binding: str = "unbound"
    # Structured judge output.  Empty means the worker cannot prove which
    # criteria produced its verdict, so capability-based reclassification must
    # fail closed and preserve the verdict.
    unmet_criteria: tuple[str, ...] = ()
    refusal: dict[str, Any] | None = None
    # Set by the worker when an observational contract error fired before any
    # dispatch.  The verdict may still read "fail" (fail-closed semantics), but
    # the basis is a precondition failure, not a structured invariant result.
    observational_rejection: str | None = None
    # Judge model that produced the verdict (BEAAA-35757); None when no judge ran.
    judge_model: str | None = None
    evidence_sha256: str | None = None
    diagnostic_measurements: dict[str, int] | None = None


_MAX_CASE_TRANSPORT_RETRIES = 3
_MAX_CASE_BACKOFF_SECONDS = 8.0


def _transport_status(value: Any) -> int | None:
    """Extract a provider HTTP status without retaining provider payloads."""
    for candidate in (
        getattr(value, "status_code", None),
        getattr(getattr(value, "response", None), "status_code", None),
    ):
        try:
            status = int(candidate)
        except (TypeError, ValueError):
            continue
        if status in {429, 503}:
            return status
    text = str(value or "")
    match = re.search(r"(?:upstream_http_|http\s*)(429|503)", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _retry_after_seconds(value: Any) -> float | None:
    headers = getattr(value, "headers", None)
    if headers is None:
        headers = getattr(getattr(value, "response", None), "headers", None)
    if not isinstance(headers, Mapping):
        return None
    for name in ("Retry-After", "retry-after", "X-RateLimit-Reset", "x-ratelimit-reset"):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            delay = float(raw)
        except (TypeError, ValueError):
            continue
        # Reset headers are commonly epoch seconds; keep the result bounded.
        if delay > 1_000_000_000:
            delay = max(0.0, delay - time.time())
        return max(0.0, min(_MAX_CASE_BACKOFF_SECONDS, delay))
    return None


def _transport_result(status: int, *, attempts: int) -> WorkerAttemptResult:
    return WorkerAttemptResult(
        status=COULD_NOT_RUN_PROBE_STATUS,
        evidence_ref=None,
        incomplete_reason=f"transport_retry_exhausted:upstream_http_{status}:attempts={attempts}",
    )


def _invoke_worker_with_retry(
    worker: FrameworkWorker,
    registry_row: Mapping[str, Any],
    job: FullScanJob,
    *,
    limiter: Semaphore,
    sleep: Callable[[float], None] | None = None,
    random_value: Callable[[], float] | None = None,
) -> WorkerAttemptResult:
    """Run one case under target pressure and bounded 429/503 backoff."""
    last_status: int | None = None
    sleep = sleep or time.sleep
    random_value = random_value or random.random
    for attempt in range(_MAX_CASE_TRANSPORT_RETRIES + 1):
        with limiter:
            try:
                result = worker(registry_row, job)
            except Exception as exc:  # noqa: BLE001 - normalize transport failures
                status = _transport_status(exc)
                if status is None:
                    raise
                last_status = status
                result = None
                retry_after = _retry_after_seconds(exc)
            else:
                status = _transport_status(result.incomplete_reason)
                if status is None and result.status == COULD_NOT_RUN_PROBE_STATUS:
                    status = _transport_status(result)
                if status is None:
                    return result
                last_status = status
                retry_after = _retry_after_seconds(result)
                if attempt == _MAX_CASE_TRANSPORT_RETRIES:
                    return _transport_result(status, attempts=attempt + 1)

        if attempt >= _MAX_CASE_TRANSPORT_RETRIES:
            break
        base = retry_after if retry_after is not None else min(
            _MAX_CASE_BACKOFF_SECONDS, 0.5 * (2**attempt)
        )
        # Full jitter keeps concurrent cases from retrying in lockstep.  A
        # provider Retry-After remains the upper bound requested by the peer.
        delay = min(_MAX_CASE_BACKOFF_SECONDS, base * (0.5 + random_value()))
        if delay > 0:
            sleep(delay)
    return _transport_result(last_status or 503, attempts=_MAX_CASE_TRANSPORT_RETRIES + 1)


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
    scan_id: str | None = None
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
    started_at: datetime | None = None
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
    # BEAAA-29804 — the target surfaces this scan established, read by
    # ``registered_case_executor._resolve_applicability`` to decide whether a
    # conditionally-applicable family (lane C's ``has_structured_output``,
    # ``mcp_tool_integrity``'s ``has_mcp_tool_integration``) is measured or
    # terminates ``not_applicable``. ``None`` means no surface was established,
    # which is the fail-closed direction: every gated family goes N/A. Populate
    # it from ``harness.structured_output_probe``, never by hand — a declared
    # surface is a promise, a probed one is a demonstration.
    capability_manifest: Mapping[str, Any] | None = None
    # BEAAA-38957 — versioned paid-pack id (e.g. ``full-scan-conv-en@0.2``).
    # ``None`` for legacy free-pack jobs.
    pack_id: str | None = None
    # BEAAA-38957 — hard cost cap (USD).  ``None`` means no cap.
    max_cost_usd: float | None = None
    # BEAAA-38957 — accumulated actual spend (USD), updated at every checkpoint.
    observed_judge_cost_usd: float = 0.0
    observed_target_cost_usd: float = 0.0
    # BEAAA-38957 — tenant identifier for per-target concurrency and entitlement
    # checks.  ``None`` keeps the legacy unchecked behaviour.
    tenant_id: str | None = None
    target_id: str | None = None
    entitlement_id: str | None = None
    # BEAAA-38987 — durable variable-form id from FormStore; None for legacy runs
    # without form-based scenario selection.
    form_id: str | None = None
    # BEAAA-39990 — case-level target pressure is independent from the number
    # of worker slots.  A customer may run N cases while a provider target is
    # held to a lower, explicit in-flight cap.
    max_concurrent_per_target: int = DEFAULT_MAX_INFLIGHT_CASES_PER_TARGET
    # Durable live-progress projection.  These are case identities only; no
    # prompt, response, credential, or evidence content crosses this boundary.
    in_flight_case_ids: tuple[str, ...] = ()
    operations_scope: Mapping[str, Any] | None = None


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
        entitlements: Any | None = None,
        max_running_jobs_per_target: int = DEFAULT_MAX_RUNNING_JOBS_PER_TARGET,
    ) -> None:
        if max_running_jobs_per_target <= 0:
            raise FullScanExecutorError("max_running_jobs_per_target must be positive")
        self._clock = clock or _utcnow
        self._jobs: dict[str, FullScanJob] = {}
        self._idempotency_index: dict[str, str] = {}
        # BEAAA-38957: maps job_id -> target_id for per-target concurrency.
        self._job_target_ids: dict[str, str | None] = {}
        self._lock = RLock()
        self._case_limiters: dict[tuple[str, int], Semaphore] = {}
        self._persistence = persistence
        # BEAAA-38957 — ``api.full_scan_entitlement.FullScanEntitlementStore``.
        # A paid-pack job is only accepted against an unconsumed entitlement.
        self._entitlements = entitlements
        self._max_running_jobs_per_target = max_running_jobs_per_target
        if persistence is not None:
            for record in persistence.load_jobs():
                job = _job_from_record(record)
                self._jobs[job.job_id] = job
                self._job_target_ids[job.job_id] = record.get("_target_id")
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

    def case_limiter(self, target_id: str | None, limit: int) -> Semaphore:
        """Return a process-local semaphore shared by jobs for one target."""
        if limit <= 0:
            raise FullScanExecutorError("case in-flight limit must be positive")
        key = (str(target_id or "__unbound_target__"), int(limit))
        with self._lock:
            limiter = self._case_limiters.get(key)
            if limiter is None:
                limiter = Semaphore(limit)
                self._case_limiters[key] = limiter
            return limiter

    def submit(
        self,
        *,
        agent_id: str,
        scanner_version: str,
        corpus_version: str,
        model_versions: Mapping[str, Any],
        seed: int,
        registry_cases: Iterable[FullSuiteCase] = (),
        scan_id: str | None = None,
        idempotency_key: str | None = None,
        approved_blocker_ref: str | None = None,
        wall_clock_budget_seconds: int | None = None,
        rate_limit_per_minute: int | None = None,
        terminal_webhook: Mapping[str, Any] | None = None,
        capability_manifest: Mapping[str, Any] | None = None,
        # BEAAA-38957 new fields -----------------------------------------
        pack_id: str | None = None,
        max_cost_usd: float | None = None,
        tenant_id: str | None = None,
        entitlement_store: "Any | None" = None,
        max_concurrent_per_target: int = DEFAULT_MAX_INFLIGHT_CASES_PER_TARGET,
        target_id: str | None = None,
        # BEAAA-38987 — optional FormStore for private scenario reservation.
        form_store: "Any | None" = None,
        form_bank_version: str | None = None,
        form_id: str | None = None,
        form_history_window: int = 1,
        operations_scope: Mapping[str, Any] | None = None,
    ) -> FullScanJob:
        with self._lock:
            cases = tuple(registry_cases)
            paid_pack = False
            if wall_clock_budget_seconds is not None and wall_clock_budget_seconds <= 0:
                raise FullScanExecutorError("wall_clock_budget_seconds must be positive")
            if rate_limit_per_minute is not None and rate_limit_per_minute <= 0:
                raise FullScanExecutorError("rate_limit_per_minute must be positive")
            if max_cost_usd is not None and not float(max_cost_usd) > 0:
                raise FullScanExecutorError("max_cost_usd must be positive")
            if max_concurrent_per_target <= 0:
                raise FullScanExecutorError("max_concurrent_per_target must be positive")
            if idempotency_key and idempotency_key in self._idempotency_index:
                return self._jobs[self._idempotency_index[idempotency_key]]

            # BEAAA-38957: pack_id validation --------------------------------
            if pack_id is not None:
                from api.full_scan_packs import PAID_PACKS, pack_registry_cases
                paid_pack = pack_id in PAID_PACKS
                if paid_pack:
                    if cases:
                        raise FullScanExecutorError("registry_cases must not be passed with pack_id")
                    if not tenant_id:
                        raise FullScanExecutorError("pack_id requires tenant_id")
                    cases = pack_registry_cases(pack_id)
                else:
                    from api.full_scan_pack_registry import VariablePack, resolve_pack
                    try:
                        resolved_pack = resolve_pack(pack_id)
                    except KeyError as exc:
                        raise FullScanExecutorError(
                            f"unknown_pack: Unknown pack_id {pack_id!r}"
                        ) from exc
                    if isinstance(resolved_pack, VariablePack):
                        if form_store is None:
                            raise FullScanExecutorError(f"variable pack {pack_id!r} requires form_store")
                        form_bank_version = resolved_pack.bank_version
                    elif not cases:
                        cases = tuple(FullSuiteCase(c.case_id, c.rubric_point_id) for c in resolved_pack)

            if not cases and form_store is None:
                raise FullScanExecutorError("registry_cases must not be empty")

            job_id = str(uuid.uuid4())
            entitlement_id: str | None = None
            if paid_pack:
                if self._entitlements is None:
                    raise FullScanExecutorError("entitlement_store_unconfigured")
                entitlement = self._entitlements.check(
                    tenant_id=str(tenant_id), pack_id=str(pack_id)
                )
                if entitlement is None:
                    raise FullScanExecutorError("entitlement_required")
                if max_cost_usd is None:
                    max_cost_usd = entitlement.max_cost_usd
                elif float(max_cost_usd) > entitlement.max_cost_usd:
                    raise FullScanExecutorError(
                        "max_cost_usd exceeds the entitlement's max_cost_usd"
                    )
                entitlement = self._entitlements.consume(
                    tenant_id=str(tenant_id), pack_id=str(pack_id), job_id=job_id,
                    entitlement_id=entitlement.entitlement_id,
                )
                entitlement_id = entitlement.entitlement_id
            elif pack_id is not None and entitlement_store is not None:
                if not entitlement_store.check(tenant=tenant_id or "", pack_id=pack_id):
                    raise FullScanExecutorError(
                        f"No unconsumed entitlement for tenant={tenant_id!r} "
                        f"pack_id={pack_id!r}"
                    )

            # Variable-form and registry jobs retain the current acceptance-
            # time limit. Fixed paid packs queue and enforce the limit at lease
            # time so a purchased job is never discarded.
            if (
                not paid_pack
                and tenant_id is not None
                and target_id is not None
                and max_concurrent_per_target > 0
            ):
                running_count = sum(
                    1
                    for jid, existing in self._jobs.items()
                    if existing.tenant_id == tenant_id
                    and self._job_target_ids.get(jid) == target_id
                    and existing.status not in TERMINAL_JOB_STATUSES
                )
                if running_count >= max_concurrent_per_target:
                    raise FullScanExecutorError(
                        "Per-target concurrency limit reached for "
                        f"tenant={tenant_id!r} target={target_id!r} "
                        f"(max={max_concurrent_per_target})"
                    )

            # BEAAA-38987: with a FormStore, reserve a fresh private form
            # atomically before the job exists, and dispatch only the test
            # specifications it selected. No static fallback: insufficient
            # supply fails the submit. Only form_id reaches the job record.
            resolved_form_id: str | None = None
            if form_store is not None:
                from scanners.full_scan_form_adapter import (
                    assert_form_payloads_current,
                    create_full_scan_form,
                    form_test_spec_ids,
                )
                from scanners.variable_test_forms import FormError
                if not tenant_id:
                    raise FullScanExecutorError(
                        "variable forms require a trusted tenant_id"
                    )
                try:
                    _scope = (tenant_id, agent_id)
                    if form_id is not None:
                        try:
                            _form = form_store.get(form_id, scope=_scope, product="full")
                        except FormError as exc:
                            if str(exc) != "private form not found for this scope and product":
                                raise
                            _form = create_full_scan_form(
                                form_store, scope=_scope,
                                bank_version=form_bank_version or corpus_version,
                                history_window=form_history_window, form_id=form_id,
                            )
                    else:
                        _form = create_full_scan_form(
                            form_store, scope=_scope,
                            bank_version=form_bank_version or corpus_version,
                            history_window=form_history_window,
                        )
                except FormError as exc:
                    raise FullScanExecutorError(
                        f"variable form unavailable: {exc}"
                    ) from exc
                resolved_form_id = _form["form_id"]
                try:
                    assert_form_payloads_current(_form)
                except ValueError as exc:
                    raise FullScanExecutorError(str(exc)) from exc
                _reserved = form_test_spec_ids(_form)
                if not cases:
                    cases = tuple(
                        FullSuiteCase(
                            row["test_spec_id"], row["payload"]["rubric_point_id"]
                        )
                        for row in _form["cases"]
                    )
                cases = tuple(c for c in cases if c.case_id in _reserved)
                _matched = {c.case_id for c in cases}
                if _matched != _reserved:
                    missing = sorted(_reserved - _matched)
                    raise FullScanExecutorError(
                        f"form reservation missing registry cases: {missing}"
                    )

            now = self._clock()
            job = FullScanJob(
                job_id=job_id,
                agent_id=agent_id,
                scanner_version=scanner_version,
                corpus_version=corpus_version,
                model_versions=dict(model_versions),
                seed=seed,
                registry_cases=cases,
                scan_id=scan_id,
                approved_blocker_ref=approved_blocker_ref,
                created_at=now,
                updated_at=now,
                wall_clock_budget_seconds=wall_clock_budget_seconds,
                rate_limit_per_minute=rate_limit_per_minute,
                capability_manifest=(
                    dict(capability_manifest)
                    if capability_manifest is not None
                    else None
                ),
                terminal_webhook=(
                    dict(terminal_webhook) if terminal_webhook is not None else None
                ),
                pack_id=pack_id,
                max_cost_usd=max_cost_usd,
                tenant_id=tenant_id,
                target_id=target_id,
                entitlement_id=entitlement_id,
                form_id=resolved_form_id,
                max_concurrent_per_target=max_concurrent_per_target,
                operations_scope=(
                    dict(operations_scope) if operations_scope is not None else None
                ),
            )
            self._jobs[job.job_id] = job
            self._job_target_ids[job.job_id] = target_id

            # BEAAA-38957: consume entitlement atomically with job creation --
            if not paid_pack and pack_id is not None and entitlement_store is not None:
                entitlement_store.consume(
                    tenant=tenant_id or "",
                    pack_id=pack_id,
                    job_id=job.job_id,
                    external_ref=idempotency_key,
                )

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

    def progress(
        self,
        job_id: str,
        *,
        tenant_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return a bounded, owner-scoped live progress projection.

        Counts are derived from the accepted registry and the store's durable,
        deduplicated evidence rows.  No probe content, target replies, evidence
        references, credentials, or cross-tenant existence signal is exposed.
        """
        with self._lock:
            job = self._get(job_id)
            if not tenant_id or job.tenant_id != tenant_id:
                # Use the same not-found shape for absent and foreign jobs.
                raise KeyError(job_id)

            total = len(job.registry_cases)
            counts = {"completed": 0, "timed_out": 0, "incomplete": 0, "failed": 0}
            accepted = {case.case_id for case in job.registry_cases}
            for row in job.evidence_rows:
                if row.probe_id not in accepted:
                    continue
                if row.status == "complete":
                    counts["completed"] += 1
                elif row.status == "failed":
                    counts["failed"] += 1
                elif row.status == "incomplete" and row.incomplete_reason == "timeout":
                    counts["timed_out"] += 1
                elif row.status in {"incomplete", "skipped", COULD_NOT_RUN_PROBE_STATUS}:
                    counts["incomplete"] += 1

            observed = min(total, sum(counts.values()))
            clock_now = now or self._clock()
            created_at = job.created_at or clock_now
            started_at = job.started_at
            elapsed_seconds = max(0.0, (clock_now - created_at).total_seconds())
            queue_seconds = max(0.0, ((started_at or clock_now) - created_at).total_seconds())
            execution_seconds = max(0.0, (clock_now - started_at).total_seconds()) if started_at else 0.0
            observed_rate = (
                observed / execution_seconds
                if job.status == "running" and observed and execution_seconds > 0
                else None
            )
            remaining = max(0, total - observed)
            eta_seconds = remaining / observed_rate if observed_rate else None
            family_totals: Counter[str] = Counter()
            family_done: Counter[str] = Counter()
            family_in_flight: Counter[str] = Counter()
            case_families: dict[str, str] = {}
            for case in job.registry_cases:
                family = _progress_family(case.case_id, None)
                case_families[case.case_id] = family
                family_totals[family] += 1
            for row in job.evidence_rows:
                family = _progress_family(row.probe_id, row.test_id or row.framework)
                if row.probe_id in accepted and row.status in RECONCILED_TERMINAL_PROBE_STATUSES:
                    family_done[family] += 1
            for case_id in job.in_flight_case_ids:
                family_in_flight[case_families.get(case_id, _progress_family(case_id, None))] += 1
            families = {
                family: {
                    "done": family_done[family],
                    "total": family_totals[family],
                    "in_flight": family_in_flight[family],
                }
                for family in sorted(family_totals)
            }
            state = {
                "queued": "queued",
                "running": "running",
                "complete": "finished",
                "incomplete": "incomplete",
                "incomplete_cost_cap": "incomplete",
                "failed": "error",
                "cancelled": "error",
            }.get(job.status, "unconfirmed")
            unavailable = counts["timed_out"] + counts["incomplete"] + counts["failed"]
            return {
                "job_id": job.job_id,
                "scan_id": job.scan_id,
                "state": state,
                "phase": "report_preparation" if job.status in {"complete", "incomplete", "incomplete_cost_cap"} else "execution",
                "stage_label": "Preparing report" if job.status in {"complete", "incomplete", "incomplete_cost_cap"} else (
                    "Scan stopped" if job.status in {"failed", "cancelled"} else
                    "Waiting for capacity" if job.status == "queued" else "Evidence checks"
                ),
                "counts": {**counts, "observed": observed, "total": total},
                "rows_done": observed,
                "rows_total": total,
                "in_flight": len(job.in_flight_case_ids),
                "families": families,
                "processed_count": observed,
                "accepted_pack_total": total,
                "usable_count": counts["completed"],
                "unavailable_count": unavailable,
                "timing": {
                    "elapsed_seconds": round(elapsed_seconds, 3),
                    "queue_seconds": round(queue_seconds, 3),
                    "execution_seconds": round(execution_seconds, 3),
                    "observed_rate_per_second": round(observed_rate, 6) if observed_rate else None,
                    "estimated_remaining_seconds": (
                        {"low": round(eta_seconds * 0.8, 3), "high": round(eta_seconds * 1.2, 3)}
                        if eta_seconds else None
                    ),
                },
                "heartbeat_at": _iso(job.updated_at or created_at),
                "updated_at": _iso(job.updated_at or created_at),
            }

    def progress_for_scan(
        self, scan_id: str, *, tenant_id: str, now: datetime | None = None
    ) -> dict[str, Any]:
        """Resolve a public scan id without disclosing foreign job existence."""
        with self._lock:
            job_id = next((job.job_id for job in self._jobs.values()
                           if job.scan_id == scan_id and job.tenant_id == tenant_id), None)
        if job_id is None:
            raise KeyError(scan_id)
        return self.progress(job_id, tenant_id=tenant_id, now=now)

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
            if self._target_running_count(job, now=now) >= self._max_running_jobs_per_target:
                # BEAAA-38957 — the job stays accepted and queued; a worker
                # defers it until a running job on the same target finishes.
                return {
                    "job_id": job.job_id,
                    "leased": False,
                    "reason": "target_concurrency_limit",
                }
            attempt_id = str(uuid.uuid4())
            lease_expires_at = now + timedelta(seconds=ttl_seconds)
            updated = replace(
                job,
                status="running",
                started_at=job.started_at or now,
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

    def job_ids(self) -> list[str]:
        """Return all job ids in creation order, including terminal jobs."""
        with self._lock:
            return list(self._jobs)

    def lease_next(
        self,
        *,
        ttl_seconds: int = 300,
        job_filter: Callable[[FullScanJob], bool] | None = None,
    ) -> dict[str, Any] | None:
        """Lease the next resumable queued job, or ``None`` if no job is ready.

        ``job_filter`` lets a worker skip jobs it cannot run (BEAAA-38957: a
        worker with no pack runner leaves paid-pack jobs queued).
        """
        with self._lock:
            for job_id in self.queued_job_ids():
                if job_filter is not None and not job_filter(self._jobs[job_id]):
                    continue
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

    def set_in_flight(
        self,
        job_id: str,
        *,
        attempt_id: str,
        case_ids: Iterable[str],
    ) -> FullScanJob:
        """Persist the bounded case set currently occupying worker slots."""
        with self._lock:
            job = self._get(job_id)
            if job.status in TERMINAL_JOB_STATUSES:
                raise FullScanExecutorError("cannot update terminal job progress")
            self._require_active_lease(job, attempt_id=attempt_id)
            normalized = tuple(sorted({str(case_id) for case_id in case_ids if case_id}))
            updated = replace(job, in_flight_case_ids=normalized, updated_at=self._clock())
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
            if any(
                row.incomplete_reason == COST_CAP_REACHED_REASON
                for row in job.evidence_rows
            ):
                final_status = "incomplete_cost_cap"
            updated = replace(
                job,
                status=final_status,
                active_attempt_id=None,
                lease_expires_at=None,
                in_flight_case_ids=(),
                final_manifest=manifest,
                final_manifest_hash=manifest_hash,
                bind_eligibility=bind_eligibility,
                updated_at=self._clock(),
            )
            self._persist(updated)
            return updated

    def finalize_cost_cap(
        self, job_id: str, *, attempt_id: str | None = None
    ) -> FullScanJob:
        """Finalize a job that hit its ``max_cost_usd`` cap.

        Sets terminal status ``incomplete_cost_cap`` so callers can distinguish
        a deliberate budget stop from a general incomplete run.
        """
        with self._lock:
            job = self._get(job_id)
            active_attempt_id = attempt_id or job.active_attempt_id
            if active_attempt_id is None:
                raise FullScanExecutorError("job has no active attempt")
            self._require_active_lease(job, attempt_id=active_attempt_id)
            manifest = build_attempt_manifest(job, finished_at=self._clock())
            manifest_hash = manifest_content_hash(manifest)
            updated = replace(
                job,
                status="incomplete_cost_cap",
                active_attempt_id=None,
                lease_expires_at=None,
                in_flight_case_ids=(),
                final_manifest=manifest,
                final_manifest_hash=manifest_hash,
                bind_eligibility="blocked_base_suite_incomplete",
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
                    in_flight_case_ids=(),
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
        manifest["terminal_failure_reason"] = reason
        updated = replace(
            job,
            status="failed",
            active_attempt_id=None,
            lease_expires_at=None,
            in_flight_case_ids=(),
            final_manifest=manifest,
            final_manifest_hash=manifest_content_hash(manifest),
            bind_eligibility=_bind_eligibility(job, manifest),
            updated_at=self._clock(),
        )
        self._persist(updated)
        return updated

    def _target_running_count(self, job: FullScanJob, *, now: datetime) -> int:
        """Live-leased jobs on the same ``(tenant_id, target_id)``, excluding ``job``."""
        if not job.target_id:
            return 0
        return sum(
            1
            for other in self._jobs.values()
            if other.job_id != job.job_id
            and other.target_id == job.target_id
            and other.tenant_id == job.tenant_id
            and other.status not in TERMINAL_JOB_STATUSES
            and other.active_attempt_id is not None
            and other.lease_expires_at is not None
            and other.lease_expires_at > now
        )

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
    untested_coverage = build_untested_coverage(rows)
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
    if getattr(job, "pack_id", None) is not None or getattr(
        job, "max_cost_usd", None
    ) is not None or any(
        row.get("cost") for row in rows
    ):
        budget["cost"] = cost_budget_block(job)
    incomplete_reasons = [
        f"required_case_not_attempted:{case_id}"
        for case_id in missing_required
    ]
    for row in rows:
        if row["status"] != "complete" and row.get("incomplete_reason"):
            incomplete_reasons.append(
                f"{row['probe_id']}:{row['incomplete_reason']}"
            )
    reconciliation_attempted = len(rows)
    reconciliation_terminal = sum(
        count
        for status, count in status_counts.items()
        if status in RECONCILED_TERMINAL_PROBE_STATUSES
    )
    canonical_reconciliation_equal = (
        len(job.registry_cases)
        == reconciliation_attempted
        == reconciliation_terminal
        == len(rows)
    )
    # BEAAA-33252: the fourth term of exit criterion 2.  `attempted` and
    # `terminal` only observe that a row reached an end state; neither can see
    # whether the row acquired anything from the target, so the three-way
    # equality cannot fail on a row that completed without ever contacting it.
    reconciliation_evidenced = len(evidenced_rows(rows))
    reconciliation_unjustified = len(unjustified_non_acquisition_rows(rows))
    four_way_reconciliation_equal = (
        canonical_reconciliation_equal
        and reconciliation_evidenced == reconciliation_terminal
    )
    acceptance_passed = (
        four_way_reconciliation_equal
        and not incomplete_reasons
        and not missing_required
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
        "attacker_pins": attacker_pins_manifest_block(),
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
            "attempted": reconciliation_attempted,
            "terminal": reconciliation_terminal,
            "evidenced": reconciliation_evidenced,
            "unjustified_non_acquisition": reconciliation_unjustified,
            "by_status": dict(sorted(status_counts.items())),
        },
        "acceptance_gate": {
            # Compares expected/attempted/terminal/len(rows) only -- kept under
            # its historical name so existing readers keep their meaning.
            "canonical_reconciliation_equal": canonical_reconciliation_equal,
            # BEAAA-21446 exit criterion 2 in full, including the only term
            # that can observe acquisition (BEAAA-33252).
            "expected_attempted_terminal_evidenced_equal": four_way_reconciliation_equal,
            "incomplete_false": not incomplete_reasons,
            "zero_unapproved_missing_required": not missing_required,
            "passed": acceptance_passed,
        },
        "verdict_counts": dict(sorted(verdict_counts.items())),
        "untested_coverage": untested_coverage,
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
        # BEAAA-38987 — form identity; None for legacy runs without form-based
        # scenario selection. Public manifest carries only the id, never payloads.
        "form_id": getattr(job, "form_id", None),
        "bind_eligibility": _bind_eligibility_from_missing(
            incomplete_reasons,
            approved_blocker_ref=job.approved_blocker_ref,
        ),
        "finished_at": _iso(finished_at),
    }
    max_cost_usd = getattr(job, "max_cost_usd", None)
    pack_id = getattr(job, "pack_id", None)
    if max_cost_usd is not None:
        manifest["execution_budget"]["max_cost_usd"] = max_cost_usd
    if pack_id is not None:
        manifest["pack_id"] = pack_id
        manifest["tenant_id"] = getattr(job, "tenant_id", None)
        manifest["target_id"] = getattr(job, "target_id", None)
    evidence_class = job.model_versions.get("evidence_class")
    if evidence_class:
        manifest["evidence_class"] = str(evidence_class)
        manifest["live_service_signed"] = False
    return seal_manifest(manifest)


def job_observed_cost_usd(job: FullScanJob) -> float:
    """Observed spend so far (judge + target) across the job's rows.

    BEAAA-38957: rows carry the provider-reported cost when usage was
    returned, else their estimate (flagged ``estimated``) — so a row never
    counts as free just because the provider was silent.
    """
    total = 0.0
    for row in job.evidence_rows:
        cost = row.cost or {}
        total += float(cost.get("judge_cost_usd") or 0.0)
        total += float(cost.get("target_cost_usd") or 0.0)
    return round(total, 6)


def cost_budget_block(job: FullScanJob) -> dict[str, Any]:
    """Manifest budget block: estimate vs observed, judge vs target."""
    estimated_judge = estimated_target = observed_judge = observed_target = 0.0
    basis_counts: Counter[str] = Counter()
    estimated_rows = 0
    for row in job.evidence_rows:
        cost = row.cost
        if not cost:
            continue
        estimated_judge += float(cost.get("estimated_judge_cost_usd") or 0.0)
        estimated_target += float(cost.get("estimated_target_cost_usd") or 0.0)
        observed_judge += float(cost.get("judge_cost_usd") or 0.0)
        observed_target += float(cost.get("target_cost_usd") or 0.0)
        basis_counts[f"judge:{cost.get('judge_cost_basis') or 'none'}"] += 1
        basis_counts[f"target:{cost.get('target_cost_basis') or 'none'}"] += 1
        if cost.get("estimated"):
            estimated_rows += 1
    return {
        "max_cost_usd": job.max_cost_usd,
        "estimated": {
            "judge_usd": round(estimated_judge, 6),
            "target_usd": round(estimated_target, 6),
            "total_usd": round(estimated_judge + estimated_target, 6),
        },
        "observed": {
            "judge_usd": round(observed_judge, 6),
            "target_usd": round(observed_target, 6),
            "total_usd": round(observed_judge + observed_target, 6),
        },
        "observed_basis_counts": dict(sorted(basis_counts.items())),
        "rows_with_estimated_cost": estimated_rows,
        "cost_cap_reached": any(
            row.incomplete_reason == COST_CAP_REACHED_REASON
            for row in job.evidence_rows
        ),
    }


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
        if _is_countable_active_executable(row) or (
            row.get("test_id") in EVIDENCE_FAMILIES
            and row.get("status") == "active"
            and row.get("production_runner_executable") is True
            and _case_id_from_row(row) in {
                str(case.get("case_id") or "") for case in attempt_manifest.get("expected_cases", [])
                if isinstance(case, Mapping)
            }
        )
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


def manifest_status_consistency_errors(
    manifest: Mapping[str, Any],
) -> list[str]:
    """Return every disagreement between per-row states and summary counts.

    A derived manifest that rewrites ``evidence_rows`` must rewrite every
    summary of those rows with them. A reader of a signed manifest has no way
    to tell which field is authoritative when they disagree, so the summaries
    are checked against the rows themselves rather than against each other.
    Summaries absent from the document are not invented here.
    """
    rows = manifest.get("evidence_rows")
    if not isinstance(rows, list):
        return []
    observed = Counter(
        str(row.get("status") or "unknown")
        for row in rows
        if isinstance(row, Mapping)
    )
    observed_by_status = dict(sorted(observed.items()))
    observed_terminal = sum(
        count
        for status, count in observed.items()
        if status in RECONCILED_TERMINAL_PROBE_STATUSES
    )
    errors: list[str] = []

    def _compare_counts(field: str, declared: Any) -> None:
        if not isinstance(declared, Mapping):
            return
        if dict(sorted(declared.items())) != observed_by_status:
            errors.append(
                f"{field} {dict(sorted(declared.items()))} disagrees with "
                f"per-row states {observed_by_status}"
            )

    _compare_counts("status_counts", manifest.get("status_counts"))
    reconciliation = manifest.get("reconciliation")
    if isinstance(reconciliation, Mapping):
        _compare_counts(
            "reconciliation.by_status", reconciliation.get("by_status")
        )
        attempted = reconciliation.get("attempted")
        if isinstance(attempted, int) and attempted != len(rows):
            errors.append(
                f"reconciliation.attempted {attempted} disagrees with "
                f"{len(rows)} evidence rows"
            )
        terminal = reconciliation.get("terminal")
        if isinstance(terminal, int) and terminal != observed_terminal:
            errors.append(
                f"reconciliation.terminal {terminal} disagrees with "
                f"{observed_terminal} terminal per-row states"
            )
    return errors


def seal_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return a self-consistent hash and signature over the exact content.

    Existing integrity fields are always discarded first. This makes the
    helper safe for derived manifests: callers cannot accidentally retain a
    signature from the source document after changing acceptance or evidence
    fields.

    A manifest whose row summaries contradict its own ``evidence_rows`` does
    not seal. Signing such a document publishes a contradiction under our
    signature, so it fails here instead.
    """
    unsigned = {
        key: value
        for key, value in manifest.items()
        if key not in {"signed_manifest", "manifest_hash"}
    }
    inconsistencies = manifest_status_consistency_errors(unsigned)
    if inconsistencies:
        raise FullScanExecutorError(
            "manifest is not self-consistent: " + "; ".join(inconsistencies)
        )
    sealed = dict(unsigned)
    sealed["signed_manifest"] = sign_manifest(unsigned)
    sealed["manifest_hash"] = manifest_content_hash(unsigned)
    return sealed


def registry_cases_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    execution_flag: str = "production_runner_executable",
) -> tuple[FullSuiteCase, ...]:
    """Convert selected active-test registry rows into unique executable cases.

    Production callers use the production-executable default. Report-only
    direct-family rows may be runnable before ``execution_evidenced`` becomes
    true; the published executed-test count remains independently evidence
    gated. Evidence-generation workflows may explicitly select another boolean
    capability flag, such as ``target_bound``.
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
    checkpoint_every: int = DEFAULT_DISPATCH_CHECKPOINT_ROWS,
    max_workers: int = 1,
) -> FullScanJob:
    """Run one registry-driven attempt and checkpoint terminal evidence rows.

    This is the production boundary between the canonical registry and framework
    workers. A missing worker, dependency failure, timeout, or unsupported target
    interface is recorded as a terminal incomplete evidence row; it is never
    converted to a pass or omitted from reconciliation.

    Terminal rows are flushed to the store every ``checkpoint_every`` rows so a
    process killed mid-dispatch loses at most that many paid attempts. The
    flush is idempotent: :meth:`FullScanJobStore.checkpoint` merges on
    ``(probe_id, seed)``.
    """
    if max_workers <= 0:
        raise ValueError("max_workers must be positive")
    job = store.status(job_id)
    workers = workers if workers is not None else build_default_framework_workers()
    expected_case_ids = {case.case_id for case in job.registry_cases}
    rows_to_checkpoint: list[EvidenceAttemptRow] = []
    _consecutive_quota_errors = 0
    # BEAAA-38957: accumulated observed judge cost for cost-cap enforcement.
    _observed_judge_cost: float = sum(
        float(row.estimated_judge_cost_usd or 0.0) for row in job.evidence_rows
    )

    # Build the set of all remaining case rows (in order) for cost-cap marking.
    _all_pending_rows: list[Mapping[str, Any]] = []
    _completed_before = {
        (row.probe_id, row.seed): True for row in job.evidence_rows
        if row.status in TERMINAL_PROBE_STATUSES
    }
    for registry_row in registry_rows:
        if registry_row.get("status") != "active":
            continue
        if registry_row.get(execution_flag) is not True:
            continue
        case_id = _case_id_from_row(registry_row)
        if case_id not in expected_case_ids:
            continue
        if (case_id, job.seed) in _completed_before:
            continue
        _all_pending_rows.append(registry_row)

    # Keep the scan-wide wall-clock guard authoritative before admitting a new
    # batch. In-flight workers are allowed to finish and checkpoint; no new
    # target work is started after the durable budget is breached.
    if (
        job.wall_clock_budget_seconds is not None
        and job.started_at is not None
        and (store._clock() - job.started_at).total_seconds()
        >= job.wall_clock_budget_seconds
        and _all_pending_rows
    ):
        cap_rows = [
            EvidenceAttemptRow(
                probe_id=_case_id_from_row(row),
                rubric_point_id=str(row.get("risk_or_rubric_id") or row.get("rubric_point_id") or ""),
                status="incomplete",
                framework="wall_clock_cap",
                corpus_version=job.corpus_version,
                model_version=_primary_model_version(job.model_versions),
                seed=job.seed,
                evidence_ref=None,
                incomplete_reason="wall_clock_cap_reached",
            )
            for row in _all_pending_rows
        ]
        store.checkpoint(job_id, attempt_id=attempt_id, rows=cap_rows)
        return store.status(job_id)

    # Execute independent cases concurrently, but reduce and checkpoint them
    # below in registry order.  Keeping reduction single-threaded preserves
    # stable evidence ordering and makes the existing durable cap checks the
    # shared source of truth.  Cost-capped jobs stay sequential so a worker
    # cannot spend past a known scan-wide cost boundary while queued futures
    # are still running.
    parallel_results: dict[str, WorkerAttemptResult] = {}
    case_rows = {
        _case_id_from_row(row): row for row in _all_pending_rows
    }
    case_limiter = store.case_limiter(
        job.target_id, job.max_concurrent_per_target
    )

    def hard_gate_reason(row: Mapping[str, Any]) -> str | None:
        """Return the deterministic N/A reason before admitting target work.

        Capability-gated families are preconditions, not post-judge hints.  A
        target must explicitly declare the required capability as ``True``;
        otherwise dispatching the family would exercise a surface the target
        does not have (and spend a target call before reaching the gate).
        """
        gate = row.get("applicability_gate")
        if not isinstance(gate, Mapping):
            gate = row.get("applicability")
        if not isinstance(gate, Mapping):
            return None
        if gate.get("status") != "applicable_when_capability_declared":
            return None
        required = str(gate.get("required_capability") or "")
        if not required:
            return None
        declared = (
            job.capability_manifest.get(required)
            if isinstance(job.capability_manifest, Mapping)
            else None
        )
        if declared is True:
            return None
        return f"{required}={declared!r}"

    hard_gated_cases = {
        case_id: reason
        for case_id, row in case_rows.items()
        if (reason := hard_gate_reason(row)) is not None
    }
    callable_case_ids = tuple(
        case_id
        for case_id, row in case_rows.items()
        if case_id not in hard_gated_cases
        if workers.get(worker_id_for_registry_row(dict(row))) is not None
    )
    if max_workers > 1 and job.max_cost_usd is None:
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                pending_case_ids = iter(callable_case_ids)
                futures = {}

                def submit_next() -> bool:
                    try:
                        case_id = next(pending_case_ids)
                    except StopIteration:
                        return False
                    futures[executor.submit(
                        _invoke_worker_with_retry,
                        workers[worker_id_for_registry_row(dict(case_rows[case_id]))],
                        case_rows[case_id],
                        job,
                        limiter=case_limiter,
                    )] = case_id
                    return True

                for _ in range(min(max_workers, len(callable_case_ids))):
                    submit_next()
                store.set_in_flight(
                    job_id, attempt_id=attempt_id, case_ids=futures.values()
                )
                while futures:
                    completed, _ = wait(futures, return_when=FIRST_COMPLETED)
                    for future in completed:
                        case_id = futures.pop(future)
                        try:
                            parallel_results[case_id] = future.result()
                        except TimeoutError:
                            worker_id = worker_id_for_registry_row(dict(case_rows[case_id]))
                            parallel_results[case_id] = WorkerAttemptResult(
                                status="incomplete",
                                evidence_ref=None,
                                incomplete_reason=f"worker_timeout:{worker_id}",
                            )
                        except Exception as exc:
                            worker_id = worker_id_for_registry_row(dict(case_rows[case_id]))
                            parallel_results[case_id] = WorkerAttemptResult(
                                status="incomplete",
                                evidence_ref=None,
                                incomplete_reason=f"worker_error:{worker_id}:{exc.__class__.__name__}",
                            )

                        wall_clock_breached = bool(
                            job.wall_clock_budget_seconds is not None
                            and job.started_at is not None
                            and (store._clock() - job.started_at).total_seconds()
                            >= job.wall_clock_budget_seconds
                        )
                        if not wall_clock_breached:
                            submit_next()
                        else:
                            for pending_case_id in pending_case_ids:
                                parallel_results[pending_case_id] = WorkerAttemptResult(
                                    status="incomplete",
                                    evidence_ref=None,
                                    incomplete_reason="wall_clock_cap_reached",
                                )
                            pending_case_ids = iter(())
                    try:
                        store.set_in_flight(
                            job_id, attempt_id=attempt_id, case_ids=futures.values()
                        )
                    except FullScanExecutorError:
                        # A lease can be revoked by recovery only after this
                        # dispatch returns; keep the original error if the
                        # store is already terminal rather than masking it.
                        if store.status(job_id).status not in TERMINAL_JOB_STATUSES:
                            raise
        finally:
            if callable_case_ids and store.status(job_id).status not in TERMINAL_JOB_STATUSES:
                store.set_in_flight(job_id, attempt_id=attempt_id, case_ids=())

    for registry_row in registry_rows:
        if registry_row.get("status") != "active":
            continue
        if registry_row.get(execution_flag) is not True:
            continue
        case_id = _case_id_from_row(registry_row)
        if case_id not in expected_case_ids:
            continue
        if (case_id, job.seed) in _completed_before:
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

        # Keep the declared applicability contract for evidence classification.
        # Capability-gated rows were already decided before dispatch above.
        applicability = registry_row.get("applicability_gate")
        if not isinstance(applicability, Mapping):
            applicability = registry_row.get("applicability")

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

        if case_id in hard_gated_cases:
            result = WorkerAttemptResult(
                status="complete",
                evidence_ref=(
                    f"applicability://{case_id}/{hard_gated_cases[case_id]}"
                ),
                verdict="not_applicable",
            )
        elif case_id in parallel_results:
            result = parallel_results[case_id]
        else:
            try:
                store.set_in_flight(job_id, attempt_id=attempt_id, case_ids=(case_id,))
                result = _invoke_worker_with_retry(
                    worker,
                    registry_row,
                    job,
                    limiter=case_limiter,
                )
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
            finally:
                store.set_in_flight(job_id, attempt_id=attempt_id, case_ids=())

        required_capability = (
            str(applicability.get("required_capability") or "")
            if isinstance(applicability, Mapping)
            else ""
        )
        capability_criteria = {
            str(value)
            for value in (
                applicability.get("capability_dependent_criteria") or ()
                if isinstance(applicability, Mapping)
                else ()
            )
        }
        unmet_criteria = {str(value) for value in result.unmet_criteria}
        declared_capability = (
            job.capability_manifest.get(required_capability)
            if isinstance(job.capability_manifest, Mapping)
            else None
        )
        capability_unmet = bool(
            isinstance(applicability, Mapping)
            and applicability.get("status") == "applicable_when_capability_declared"
            and applicability.get("na_evidence_required")
            and required_capability
            and (
                declared_capability is not True
            )
        )
        capability_only_failure = bool(
            result.status == "complete"
            and str(result.verdict or "").lower() == "fail"
            and capability_unmet
            and unmet_criteria
            and unmet_criteria <= capability_criteria
        )
        # Capability-gated rows require an explicit True declaration. False or
        # absent declarations are deterministic N/A; True executes normally.
        hard_gate_not_applicable = case_id in hard_gated_cases
        declared_not_applicable = (
            hard_gate_not_applicable
            or (capability_only_failure and declared_capability is False)
        )
        result_status = "complete" if declared_not_applicable else (
            "incomplete" if capability_only_failure else result.status
        )
        result_verdict = "not_applicable" if declared_not_applicable else (
            None if capability_only_failure else result.verdict
        )
        result_reason = result.incomplete_reason
        na_evidence: dict[str, Any] | None = None
        if hard_gate_not_applicable:
            result_reason = None
            na_evidence = {
                "profile_capability_manifest": (
                    dict(job.capability_manifest)
                    if isinstance(job.capability_manifest, Mapping)
                    else None
                ),
                "missing_capability_name": required_capability,
                "declared_value": declared_capability,
                "capability_gate": (
                    applicability.get("capability_gate")
                    if isinstance(applicability, Mapping)
                    else None
                ),
                "applicability_reason": hard_gated_cases[case_id],
            }
        elif capability_only_failure:
            if declared_not_applicable:
                result_reason = None
                na_evidence = {
                    "profile_capability_manifest": dict(job.capability_manifest),  # type: ignore[arg-type]
                    "missing_capability_name": required_capability,
                    "declared_value": declared_capability,
                    "capability_gate": (
                        applicability.get("capability_gate")
                        if isinstance(applicability, Mapping)
                        else None
                    ),
                }
            else:
                result_reason = (
                    "unavailable_capability:"
                    f"{required_capability}={declared_capability!r}"
                )
        elif result.observational_rejection is not None:
            # A precondition failure: the observational contract could not be
            # satisfied before any dispatch.  The verdict may be "fail"
            # (fail-closed semantics) but the row is not a structured invariant
            # result.  Surface the rejection as na_evidence so the row is
            # distinguishable from an unjustified missing-acquisition row.
            na_evidence = {"observational_rejection": result.observational_rejection}

        rows_to_checkpoint.append(
            # These axes describe evidence provenance independently. A completed,
            # attributable target execution is measured; all other terminal paths
            # remain explicitly non-measured and report-only.
            EvidenceAttemptRow(
                probe_id=case_id,
                rubric_point_id=rubric_point_id,
                status=result_status,
                framework=framework,
                corpus_version=job.corpus_version,
                model_version=result.model_version or model_version,
                seed=job.seed,
                evidence_ref=_manifest_evidence_ref(result.evidence_ref, job_id=job.job_id),
                seed_binding=result.seed_binding,
                incomplete_reason=result_reason,
                test_id=str(registry_row.get("test_id") or ""),
                verdict=result_verdict,
                target_calls=0 if declared_not_applicable else result.target_calls,
                judge_calls=0 if declared_not_applicable else result.judge_calls,
                estimated_judge_input_tokens=result.estimated_judge_input_tokens,
                estimated_judge_output_tokens=result.estimated_judge_output_tokens,
                estimated_judge_cost_usd=result.estimated_judge_cost_usd,
                duration_ms=result.duration_ms,
                numeric_result=result.numeric_result,
                hit_count=result.hit_count,
                sample_size=result.sample_size,
                refusal=result.refusal,
                na_evidence=na_evidence,
                acquisition_basis=_acquisition_basis(
                    status=result_status,
                    verdict=result_verdict,
                    target_calls=0 if declared_not_applicable else result.target_calls,
                    judge_calls=0 if declared_not_applicable else result.judge_calls,
                    observational_rejection=result.observational_rejection,
                ),
                underwriting_relevance=(
                    "category-proxy"
                    if result_status == "complete"
                    and str(registry_row.get("test_id") or "") == "halueval"
                    else "direct"
                    if result_status == "complete"
                    else "failed"
                ),
                source_acquisition_basis=(
                    str(registry_row.get("acquisition_basis"))
                    if registry_row.get("acquisition_basis") is not None
                    else None
                ),
                source_underwriting_relevance=(
                    str(registry_row.get("underwriting_relevance"))
                    if registry_row.get("underwriting_relevance") is not None
                    else None
                ),
                score_contribution=False,
                disposition="report-only",
                case_version=(
                    str(registry_row.get("version_pin"))
                    if registry_row.get("version_pin") is not None
                    else None
                ),
                derived_result_id=(
                    f"{job.job_id}:"
                    f"{job.model_versions.get('runner_candidate_sha', 'unbound')}:"
                    f"{case_id}:{job.seed}"
                ),
                judge_model=result.judge_model,
                evidence_sha256=result.evidence_sha256,
                diagnostic_measurements=result.diagnostic_measurements,
            )
        )

        # BEAAA-38957: accumulate observed judge cost for cost-cap check.
        _observed_judge_cost += float(
            rows_to_checkpoint[-1].estimated_judge_cost_usd if rows_to_checkpoint else 0.0
        )

        if checkpoint_every > 0 and len(rows_to_checkpoint) % checkpoint_every == 0:
            store.checkpoint(job_id, attempt_id=attempt_id, rows=rows_to_checkpoint)

        if _UPSTREAM_QUOTA_RE.match(result_reason or ""):
            _consecutive_quota_errors += 1
            if _consecutive_quota_errors >= _UPSTREAM_QUOTA_ABORT_THRESHOLD:
                store.checkpoint(job_id, attempt_id=attempt_id, rows=rows_to_checkpoint)
                raise UpstreamQuotaAbort(
                    abort_reason=(
                        f"consecutive_upstream_quota:{result_reason}"
                        f":{_consecutive_quota_errors}"
                    ),
                    rows_processed=len(rows_to_checkpoint),
                    consecutive=_consecutive_quota_errors,
                )
        else:
            _consecutive_quota_errors = 0

        # BEAAA-38957: cost cap enforcement — after running a case, check if
        # observed spend has exceeded (or will exceed with next case) the cap.
        job_now = store.status(job_id)
        if (
            job_now.max_cost_usd is not None
            and _observed_judge_cost >= job_now.max_cost_usd
        ):
            # Mark all remaining pending rows could_not_run.
            already_run = {
                (r.probe_id, r.seed): True for r in rows_to_checkpoint
            }
            already_run.update(
                {(r.probe_id, r.seed): True for r in job_now.evidence_rows}
            )
            model_version = _primary_model_version(job_now.model_versions)
            for remaining_row in _all_pending_rows:
                r_case_id = _case_id_from_row(remaining_row)
                if (r_case_id, job_now.seed) in already_run:
                    continue
                r_rubric = str(
                    remaining_row.get("risk_or_rubric_id")
                    or remaining_row.get("rubric_point_id")
                    or ""
                )
                rows_to_checkpoint.append(
                    EvidenceAttemptRow(
                        probe_id=r_case_id,
                        rubric_point_id=r_rubric,
                        status="incomplete",
                        framework="cost_cap",
                        corpus_version=job_now.corpus_version,
                        model_version=model_version,
                        seed=job_now.seed,
                        evidence_ref=None,
                        incomplete_reason="cost_cap_reached",
                    )
                )
                already_run[(r_case_id, job_now.seed)] = True
            store.checkpoint(job_id, attempt_id=attempt_id, rows=rows_to_checkpoint)
            break

    return store.checkpoint(job_id, attempt_id=attempt_id, rows=rows_to_checkpoint)


def dispatch_variable_form_attempt(
    store: FullScanJobStore,
    job_id: str,
    *,
    attempt_id: str,
    form_store: Any,
    workers: Mapping[str, FrameworkWorker],
    case_ids: Iterable[str] | None = None,
    max_workers: int = 1,
) -> FullScanJob:
    """Dispatch the exact private payloads reserved for a variable-pack job."""
    job = store.status(job_id)
    if job.pack_id != "full-scan-conv-en@1.0" or not job.form_id or not job.tenant_id:
        raise FullScanExecutorError("job is not a reserved conversation variable pack")
    from scanners.full_scan_form_adapter import assert_form_payloads_current

    form = form_store.get(job.form_id, scope=(job.tenant_id, job.agent_id), product="full")
    assert_form_payloads_current(form)
    selected = set(case_ids) if case_ids is not None else None
    rows = tuple(
        {
            **dict(item["payload"]),
            # A specification may generate more than one private scenario.
            # Dispatch identity must therefore follow the reserved scenario,
            # while test_spec_id remains provenance metadata in the payload.
            "case_id": item["scenario_id"],
            "test_id": item["scenario_id"],
            "risk_or_rubric_id": item["payload"].get("rubric_point_id") or item["stratum_id"].split("|", 2)[1],
            "rubric_point_id": item["payload"].get("rubric_point_id") or item["stratum_id"].split("|", 2)[1],
            "status": "active",
            "production_runner_executable": True,
        }
        for item in form["cases"]
        if selected is None or item["scenario_id"] in selected
    )
    return dispatch_registry_attempt(
        store,
        job_id,
        attempt_id=attempt_id,
        registry_rows=rows,
        workers=workers,
        max_workers=max_workers,
    )


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
    if row.status not in {
        "complete",
        "incomplete",
        "failed",
        "skipped",
        COULD_NOT_RUN_PROBE_STATUS,
    }:
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
    if row.score_contribution or row.disposition != "report-only":
        raise FullScanExecutorError("registered-case evidence must remain report-only")
    if row.acquisition_basis not in ACQUISITION_BASES:
        raise FullScanExecutorError(
            f"invalid acquisition_basis: {row.acquisition_basis!r}"
        )
    if row.underwriting_relevance not in UNDERWRITING_RELEVANCES:
        raise FullScanExecutorError(
            f"invalid underwriting_relevance: {row.underwriting_relevance!r}"
        )
    if row.disposition not in EVIDENCE_DISPOSITIONS:
        raise FullScanExecutorError(f"invalid disposition: {row.disposition!r}")
    if row.acquisition_basis == "target-measured" and (
        row.status != "complete" or row.target_calls <= 0
    ):
        raise FullScanExecutorError(
            "target-measured evidence requires a complete attributable target call"
        )
    if row.acquisition_basis == "not-applicable" and (
        row.status != "complete"
        or row.target_calls != 0
        or row.verdict != "not_applicable"
    ):
        raise FullScanExecutorError(
            "not-applicable evidence requires a complete, unmeasured, "
            "not_applicable row"
        )
    if row.acquisition_basis == "invariant-derived" and (
        row.status != "complete" or row.target_calls != 0 or row.judge_calls != 0
    ):
        raise FullScanExecutorError(
            "invariant-derived evidence requires a complete row with no target "
            "or judge call"
        )


NON_ACQUISITION_BASES = frozenset({"missing", "not-applicable"})


def unjustified_non_acquisition_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Return terminal rows that never contacted the target and never said why.

    BEAAA-33228: the acceptance gate measures terminality, not acquisition.  A
    row that completes with a verdict never enters ``missing_required``, so a
    row carrying ``acquisition_basis: missing`` (or ``not-applicable``) with
    ``na_evidence: null`` is invisible to every published count while asserting
    a result the scan never measured.  ``invariant-derived`` rows are excluded:
    they legitimately carry no target call and state their basis in the
    enum itself.
    """

    flagged: list[Mapping[str, Any]] = []
    for row in rows:
        if row.get("status") != "complete":
            continue
        if int(row.get("target_calls") or 0) > 0:
            continue
        if str(row.get("acquisition_basis") or "") not in NON_ACQUISITION_BASES:
            continue
        if row.get("na_evidence"):
            continue
        flagged.append(row)
    return flagged


def build_untested_coverage(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Count every row the scan did not actually test, not just the refusals.

    BEAAA-33966 TASK B.  This surface used to be built from one predicate --
    ``verdict == edge_refusal`` -- so on the BEAAA-31092 rank-2 manifest it read
    ``count: 0`` while 50 of 187 rows carried no evidence at all, 24 of them with
    ``acquisition_basis: missing`` and ``incomplete_reason: null``.  A coverage
    surface reporting zero untested while 50 rows are unevidenced is a false
    all-clear, and these counts drive underwriting.

    The rule is now the complement of :func:`evidenced_rows`, so it fails closed:
    a row is untested unless it measured the target, derives from a declared
    invariant, or justifies its non-acquisition under the BEAAA-33228 contract.
    A row that states no basis and no reason has nowhere to hide -- it lands in
    ``unevidenced_no_reason``.  Refusals stay listed with their refusal
    provenance even though they are evidenced: the target's own behaviour
    stopped the test, which is the original meaning of this surface.
    """

    rows = list(rows)
    evidenced = {id(row) for row in evidenced_rows(rows)}
    listed: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        refusal = row.get("refusal") or {}
        refused = str(row.get("verdict") or "").lower() == "edge_refusal"
        if not refused and id(row) in evidenced:
            continue
        if refused:
            reason = str(refusal.get("reason") or "edge_refusal")
            category = "edge_refusal"
        else:
            basis = str(row.get("acquisition_basis") or "") or None
            stated = row.get("incomplete_reason") or row.get("na_evidence")
            category = (
                "unevidenced_with_reason" if stated else "unevidenced_no_reason"
            )
            reason = str(
                row.get("incomplete_reason")
                or (f"acquisition_basis:{basis}" if basis else "no_acquisition_basis")
            )
        seen.add(id(row))
        listed.append(
            {
                "probe_id": row["probe_id"],
                "case_id": row["probe_id"],
                "family_id": row.get("test_id") or row.get("framework"),
                "category": category,
                "acquisition_basis": row.get("acquisition_basis"),
                "status": row.get("status"),
                "verdict": row.get("verdict"),
                "refusing_party": refusal.get("refusing_party"),
                "reason": reason,
                "provenance": refusal.get("provenance", {}),
                "evidence_ref": row.get("evidence_ref"),
            }
        )
    counts = Counter(entry["category"] for entry in listed)
    return {
        "count": len(listed),
        "counts_by_category": dict(sorted(counts.items())),
        "rows": listed,
    }


EVIDENCED_ACQUISITION_BASES = frozenset({"target-measured", "invariant-derived"})
FAILED_ACQUISITION_BASES = frozenset({"failed"})


def evidenced_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Return terminal rows whose evidence was actually acquired or justified.

    BEAAA-33252: exit criterion 2 of BEAAA-21446 is a FOUR-way equality --
    ``expected = attempted = terminal = evidenced`` -- and ``evidenced`` is the
    only one of the four that is not satisfied by construction.  ``attempted``
    and ``terminal`` count rows that reached an end state; neither can observe
    whether the row acquired anything from the target.

    A row is evidenced when it measured the target, when it derives from a
    stated invariant, or when its non-acquisition is justified under the
    BEAAA-33228 contract (``na_evidence`` present).  A row whose acquisition
    ``failed`` is not evidenced: the attempt is terminal, the evidence is not
    there.  A row that states no basis at all is evidenced only if it actually
    called the target -- absence of a basis is not a claim of one.
    """

    rows = list(rows)
    unjustified = {id(row) for row in unjustified_non_acquisition_rows(rows)}
    out: list[Mapping[str, Any]] = []
    for row in rows:
        if row.get("status") != "complete":
            continue
        basis = str(row.get("acquisition_basis") or "")
        target_calls = int(row.get("target_calls") or 0)
        if basis in FAILED_ACQUISITION_BASES:
            continue
        if basis in EVIDENCED_ACQUISITION_BASES:
            out.append(row)
        elif basis in NON_ACQUISITION_BASES:
            if id(row) not in unjustified:
                out.append(row)
        elif target_calls > 0:
            out.append(row)
    return out


def _acquisition_basis(
    *,
    status: str,
    verdict: str | None,
    target_calls: int,
    judge_calls: int,
    observational_rejection: str | None = None,
) -> str:
    """Classify how a terminal row's evidence was acquired.

    ``missing`` means a genuine gap: the row should have carried an
    attributable measurement and does not.  Rows that were correctly never
    measured get their own basis so a "zero missing acquisition basis"
    acceptance clause stays satisfiable by a correct scan:

    * ``not-applicable`` - a deterministic applicability gate returned
      ``not_applicable`` without dispatching the target.
    * ``invariant-derived`` - a structured invariant produced the verdict from
      declared state, with no target and no judge call to attribute.

    A row with an ``observational_rejection`` carries a precondition failure,
    not a structured invariant result: it must not be classified as
    ``invariant-derived`` even when the verdict reads ``fail`` and both call
    counts are zero.  It stays ``missing`` so ``unjustified_non_acquisition_rows``
    can detect it unless ``na_evidence`` is also populated.

    Anything else that completes without an attributable target call stays
    ``missing``; classification fails closed.
    """
    if status == "complete":
        if target_calls > 0:
            return "target-measured"
        if verdict == "not_applicable":
            return "not-applicable"
        if judge_calls == 0 and verdict in {"pass", "fail"} and not observational_rejection:
            return "invariant-derived"
        return "missing"
    if status == "failed":
        return "failed"
    return "missing"


def _case_id_from_row(row: Mapping[str, Any]) -> str:
    return str(
        row.get("runtime_case_identity")
        or row.get("case_id")
        or row.get("case_id_or_probe_id")
        or ""
    )


def case_id_from_registry_row(row: Mapping[str, Any]) -> str:
    """Public alias for the one function that names a registry row.

    Resume paths must derive case identity with the *same* function the
    dispatcher uses, or checkpointed evidence and pending registry rows end up
    in two namespaces that only agree by accident.
    """
    return _case_id_from_row(row)


def _row_has_evidence(row: Mapping[str, Any]) -> bool:
    status = row.get("status")
    if status == "complete":
        return bool(row.get("evidence_ref"))
    if status in {"incomplete", "failed", "skipped", COULD_NOT_RUN_PROBE_STATUS}:
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


def _progress_family(case_id: str, hint: str | None) -> str:
    """Return a bounded, stable family label for live progress only."""
    value = str(hint or "").strip()
    if not value or value == "fixture":
        value = re.split(r"[/.:]", str(case_id), maxsplit=1)[0]
    if "@" in value:
        value = value.split("@", 1)[0]
    return value[:120] or "unclassified"


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
        "registry_cases": [case.to_registry_row() for case in job.registry_cases],
        "scan_id": job.scan_id,
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
        "started_at": _iso(job.started_at) if job.started_at else None,
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
        # BEAAA-29804 — must round-trip, or a job resumed after a restart loses
        # the surface its probe established and every gated family silently
        # flips to not_applicable mid-scan.
        "capability_manifest": (
            dict(job.capability_manifest)
            if job.capability_manifest is not None
            else None
        ),
        # BEAAA-38957 — paid-pack fields.
        "pack_id": job.pack_id,
        "max_cost_usd": job.max_cost_usd,
        "observed_judge_cost_usd": job.observed_judge_cost_usd,
        "observed_target_cost_usd": job.observed_target_cost_usd,
        "tenant_id": job.tenant_id,
        "target_id": job.target_id,
        "entitlement_id": job.entitlement_id,
        # BEAAA-38987 — variable-form identity.
        "form_id": job.form_id,
        "max_concurrent_per_target": job.max_concurrent_per_target,
        "in_flight_case_ids": list(job.in_flight_case_ids),
        "operations_scope": (
            dict(job.operations_scope) if job.operations_scope is not None else None
        ),
    }


def _evidence_row_from_record(row: Mapping[str, Any]) -> EvidenceAttemptRow:
    values = dict(row)
    if "sha256" in values:
        values["evidence_sha256"] = values.pop("sha256")
    return EvidenceAttemptRow(**values)


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
                approved_skip_reasons=tuple(case.get("approved_skip_reasons", ())),
            )
            for case in record["registry_cases"]
        ),
        scan_id=record.get("scan_id"),
        status=record["status"],
        attempts=tuple(record.get("attempts", ())),
        active_attempt_id=record.get("active_attempt_id"),
        lease_expires_at=_parse_iso(record.get("lease_expires_at")),
        evidence_rows=tuple(
            _evidence_row_from_record(row) for row in record.get("evidence_rows", ())
        ),
        approved_blocker_ref=record.get("approved_blocker_ref"),
        final_manifest=record.get("final_manifest"),
        final_manifest_hash=record.get("final_manifest_hash"),
        bind_eligibility=record.get(
            "bind_eligibility", "blocked_base_suite_incomplete"
        ),
        created_at=_parse_iso(record.get("created_at")),
        started_at=_parse_iso(record.get("started_at")),
        updated_at=_parse_iso(record.get("updated_at")),
        wall_clock_budget_seconds=record.get("wall_clock_budget_seconds"),
        rate_limit_per_minute=record.get("rate_limit_per_minute"),
        terminal_webhook=record.get("terminal_webhook"),
        terminal_webhook_delivered_at=_parse_iso(
            record.get("terminal_webhook_delivered_at")
        ),
        recovery_count=record.get("recovery_count", 0),
        capability_manifest=record.get("capability_manifest"),
        # BEAAA-38957 — paid-pack fields (backwards-compatible defaults).
        pack_id=record.get("pack_id"),
        max_cost_usd=record.get("max_cost_usd"),
        observed_judge_cost_usd=float(record.get("observed_judge_cost_usd") or 0.0),
        observed_target_cost_usd=float(record.get("observed_target_cost_usd") or 0.0),
        tenant_id=record.get("tenant_id"),
        target_id=record.get("target_id"),
        entitlement_id=record.get("entitlement_id"),
        # BEAAA-38987 — variable-form identity (backwards-compatible None default).
        form_id=record.get("form_id"),
        max_concurrent_per_target=int(
            record.get("max_concurrent_per_target")
            or DEFAULT_MAX_INFLIGHT_CASES_PER_TARGET
        ),
        in_flight_case_ids=tuple(str(value) for value in record.get("in_flight_case_ids", ())),
        operations_scope=record.get("operations_scope"),
    )
