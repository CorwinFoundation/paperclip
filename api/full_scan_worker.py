"""DB-backed full-scan queue worker.

BEAAA-14843 rule-execution scope: this worker consumes jobs already durably
accepted by ``FullScanJobStore``. It does not expose a public async contract and
does not alter rubric, judge, scoring, or response-envelope rules.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from api.config import ApiConfig
from api.full_scan_executor import (
    DEFAULT_MAX_RECOVERY_ATTEMPTS,
    FrameworkWorker,
    FullScanJobStore,
    dispatch_registry_attempt,
)
from api.full_scan_job_persistence import FullScanJobPersistence
from api.scan_side_effects import fire_webhook
from tools.framework_workers import build_default_framework_workers


LOGGER = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTIVE_REGISTRY_JSON = REPO_ROOT / "tools" / "probe-registry" / "active_registry.json"
ARE_SCAN_LOG_DEFAULT_PATH = "/var/lib/are-scanner/scan_log.sqlite3"
FULL_SCAN_WORKER_POLL_INTERVAL_SECONDS = 5.0
FULL_SCAN_WEBHOOK_SECRETS_ENV = "ARE_SCAN_WEBHOOK_SECRETS_JSON"


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
        webhook_secret_resolver: Callable[[str], str | bytes | None] | None = None,
        webhook_http_poster: Callable[[str, dict, bytes], None] | None = None,
    ) -> None:
        if lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be positive")
        self._store = store
        self._registry_rows = tuple(dict(row) for row in registry_rows)
        self._workers = dict(workers)
        self._lease_ttl_seconds = lease_ttl_seconds
        self._max_recovery_attempts = max_recovery_attempts
        self._webhook_secret_resolver = webhook_secret_resolver
        self._webhook_http_poster = webhook_http_poster or _default_webhook_http_poster

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

        for pending_terminal in self._store.terminal_webhook_pending_jobs():
            if self._deliver_terminal_webhook(pending_terminal):
                return FullScanWorkerResult(
                    status="terminal_webhook_delivered",
                    job_id=pending_terminal.job_id,
                    final_status=pending_terminal.status,
                    final_manifest_hash=pending_terminal.final_manifest_hash,
                )
            return FullScanWorkerResult(
                status="terminal_webhook_retry_pending",
                job_id=pending_terminal.job_id,
                final_status=pending_terminal.status,
                final_manifest_hash=pending_terminal.final_manifest_hash,
            )

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
                self._deliver_terminal_webhook(finalized)
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

    def _deliver_terminal_webhook(self, job: Any) -> bool:
        if not isinstance(job.terminal_webhook, Mapping):
            return False
        if job.terminal_webhook_delivered_at is not None:
            return False
        webhook_cfg = job.terminal_webhook.get("webhook_cfg")
        response_base = job.terminal_webhook.get("response_base")
        if not isinstance(webhook_cfg, Mapping) or not isinstance(response_base, Mapping):
            return False
        payload = dict(response_base)
        payload["status"] = job.status
        payload["partial_completion"] = job.status != "complete"
        payload["confidence_below_adjudication_threshold"] = job.status != "complete"
        payload["job_id"] = job.job_id
        if job.final_manifest_hash:
            payload["full_suite_manifest_hash"] = job.final_manifest_hash
        delivered = fire_webhook(
            webhook_cfg={str(k): str(v) for k, v in webhook_cfg.items()},
            payload=payload,
            resolver=self._webhook_secret_resolver,
            http_poster=self._webhook_http_poster,
        )
        if not delivered:
            return False
        self._store.mark_terminal_webhook_delivered(job.job_id)
        return True

    def run_forever(
        self,
        *,
        poll_interval_seconds: float = FULL_SCAN_WORKER_POLL_INTERVAL_SECONDS,
        stop: Callable[[], bool] | None = None,
    ) -> None:
        """Continuously drain the durable queue until ``stop`` returns true."""
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        stop = stop or (lambda: False)
        self.recover()
        while not stop():
            result = self.run_once()
            if result.status == "idle":
                time.sleep(poll_interval_seconds)


def load_default_full_scan_registry_rows(
    path: Path | str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Load the live registry, or an explicitly requested legacy JSON snapshot."""
    if path is None:
        from tools import active_test_registry

        live_rows = tuple(
            dict(row)
            for row in active_test_registry.build_registry()
            if active_test_registry._is_countable_active_executable(row)
        )
        if not live_rows:
            raise RuntimeError("live production registry has no executable rows")
        return live_rows

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise RuntimeError("active registry rows missing")
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if row.get("bucket") != "runnable_today":
            continue
        if row.get("countable_as_executable") is not True:
            continue
        case_id = str(row.get("case_id_or_probe_id") or "")
        rubric_point_id = str(row.get("rubric_point_id") or "")
        if not case_id or not rubric_point_id:
            continue
        normalized.append(
            {
                "test_id": str(row.get("corpus_or_family") or row.get("framework") or ""),
                "case_id": case_id,
                "status": "active",
                "production_runner_executable": True,
                "risk_or_rubric_id": rubric_point_id,
                "runner": str(row.get("worker_runner") or row.get("framework") or ""),
                "source": str(row.get("source") or ""),
                "version_pin": str(row.get("version_pin") or ""),
                "evidence_uri_template": str(row.get("evidence_uri_template") or ""),
            }
        )
    if not normalized:
        raise RuntimeError("active registry has no runnable production rows")
    return tuple(normalized)


def build_default_full_scan_worker(
    *,
    job_store_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    lease_ttl_seconds: int = 300,
    worker_timeout_seconds: int = 120,
    webhook_secret_resolver: Callable[[str], str | bytes | None] | None = None,
    webhook_http_poster: Callable[[str, dict, bytes], None] | None = None,
) -> FullScanQueueWorker:
    if job_store_path is None:
        scan_log_path = ApiConfig.from_env().scan_log_path(ARE_SCAN_LOG_DEFAULT_PATH)
        job_store_path = ApiConfig.from_env().full_scan_job_store_path(
            str(Path(scan_log_path).with_name("full_scan_jobs.sqlite3"))
        )
    Path(job_store_path).parent.mkdir(parents=True, exist_ok=True)
    store = FullScanJobStore(persistence=FullScanJobPersistence(job_store_path))
    return FullScanQueueWorker(
        store,
        registry_rows=load_default_full_scan_registry_rows(registry_path),
        workers=build_default_framework_workers(timeout_seconds=worker_timeout_seconds),
        lease_ttl_seconds=lease_ttl_seconds,
        webhook_secret_resolver=webhook_secret_resolver or _env_webhook_secret_resolver,
        webhook_http_poster=webhook_http_poster,
    )


def _env_webhook_secret_resolver(secret_ref: str) -> str | bytes | None:
    raw = os.environ.get(FULL_SCAN_WEBHOOK_SECRETS_ENV)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, Mapping):
        return None
    secret = data.get(secret_ref)
    return secret if isinstance(secret, str) and secret else None


def _default_webhook_http_poster(url: str, headers: dict, body_bytes: bytes) -> None:
    import urllib.request

    request = urllib.request.Request(
        url,
        data=body_bytes,
        headers={str(k): str(v) for k, v in headers.items()},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()


def _install_signal_stop() -> Callable[[], bool]:
    stopping = {"value": False}

    def _handler(_signum, _frame) -> None:
        stopping["value"] = True

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
    return lambda: stopping["value"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the durable full-scan queue worker")
    parser.add_argument("command", choices=("run", "run-once"))
    parser.add_argument("--job-store-path")
    parser.add_argument(
        "--registry-path",
        help="Legacy JSON snapshot override; default builds the live production registry",
    )
    parser.add_argument("--lease-ttl-seconds", type=int, default=300)
    parser.add_argument("--poll-interval-seconds", type=float, default=FULL_SCAN_WORKER_POLL_INTERVAL_SECONDS)
    parser.add_argument("--worker-timeout-seconds", type=int, default=120)
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    worker = build_default_full_scan_worker(
        job_store_path=args.job_store_path,
        registry_path=args.registry_path,
        lease_ttl_seconds=args.lease_ttl_seconds,
        worker_timeout_seconds=args.worker_timeout_seconds,
    )
    recovery = worker.recover()
    if recovery["resumed"] or recovery["failed"]:
        LOGGER.info("full scan worker recovery: resumed=%s failed=%s", recovery["resumed"], recovery["failed"])
    if args.command == "run-once":
        result = worker.run_once()
        print(json.dumps(result.__dict__, sort_keys=True))
        return 0
    worker.run_forever(
        poll_interval_seconds=args.poll_interval_seconds,
        stop=_install_signal_stop(),
    )
    return 0


__all__ = [
    "FullScanQueueWorker",
    "FullScanWorkerResult",
    "build_default_full_scan_worker",
    "load_default_full_scan_registry_rows",
]


if __name__ == "__main__":
    raise SystemExit(main())
