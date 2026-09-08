#!/usr/bin/env python3
"""Run one scheduled live scan through the in-repository full-scan executor.

The unit supplies exactly five secret environment variables. Everything else
comes from ``ops/scan-schedule/scan-schedule.conf``. This module deliberately
does not call the HTTP scan API: it constructs the same registered workers used
by the live Vapi evidence path and dispatches them in-process.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.full_scan_executor import (  # noqa: E402
    FullScanJobStore,
    dispatch_registry_attempt,
    registry_cases_from_rows,
)
from api.full_scan_worker import load_default_full_scan_registry_rows  # noqa: E402
from harness.customer_runner import customer_agent_http_runner_factory  # noqa: E402
from harness.egress import get as egress_get, verify_live_egress_fail_closed  # noqa: E402
from tools.registered_case_executor import (  # noqa: E402
    ScanBudget,
    build_registered_case_workers,
    default_judge_runner,
)

SECRET_ENV = (
    "ARE_BUNDLE_SIGNING_KEY",
    "ARE_EVAL_PROVENANCE_KEY",
    "VAPI_API_KEY",
    "VAPI_ASSISTANT_ID",
    "OPENROUTER_API_KEY",
)
EVIDENCE_LABEL = "scheduled-controlled-evidence"
PREFLIGHT_TARGETS = {
    "github.com": "https://github.com/",
    "api.vapi.ai": "https://api.vapi.ai/",
    "openrouter.ai": "https://openrouter.ai/",
}
INCOMPLETE_GROUPS = (
    "dispatch_failure",
    "transport",
    "format",
    "missing_judge",
    "unavailable_capability",
)


def _required_secret(name: str, environ: Mapping[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    value = source.get(name, "").strip()
    if not value:
        raise RuntimeError(f"required scheduled-scan secret is missing: {name}")
    return value


def map_secret_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Validate and return the five explicitly ruled secret bindings."""
    return {name: _required_secret(name, environ) for name in SECRET_ENV}


def _config(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or not str(value).strip():
        raise RuntimeError(f"scheduled-scan configuration is missing: {name}")
    return str(value).strip()


@dataclass(frozen=True)
class ScheduleConfig:
    tenant_id: str
    agent_endpoint: str
    evidence_root: Path
    scanner_version: str
    corpus_version: str
    seed: int
    concurrency: int
    rpm: int
    retries: int
    cost_cap_usd: float
    timeout_seconds: int
    judge_model: str

    @classmethod
    def from_environment(cls) -> "ScheduleConfig":
        return cls(
            tenant_id=_config("BACKBOND_SCAN_TENANT_ID"),
            agent_endpoint=_config("BACKBOND_SCAN_AGENT_ENDPOINT"),
            evidence_root=Path(_config("BACKBOND_SCAN_EVIDENCE_ROOT")),
            scanner_version=_config("BACKBOND_SCAN_SCANNER_VERSION"),
            corpus_version=_config("BACKBOND_SCAN_CORPUS_VERSION"),
            seed=int(_config("BACKBOND_SCAN_SEED", "0")),
            concurrency=int(_config("BACKBOND_SCAN_CONCURRENCY", "1")),
            rpm=int(_config("BACKBOND_SCAN_RPM", "30")),
            retries=int(_config("BACKBOND_SCAN_RETRIES", "0")),
            cost_cap_usd=float(_config("BACKBOND_SCAN_COST_CAP_USD", "100")),
            timeout_seconds=int(_config("BACKBOND_SCAN_TIMEOUT_SECONDS", "21600")),
            judge_model=_config("BACKBOND_SCAN_JUDGE_MODEL"),
        )

    def validate(self) -> None:
        if self.concurrency != 1:
            raise RuntimeError("scheduled scans require concurrency=1")
        if not 1 <= self.rpm <= 30:
            raise RuntimeError("scheduled scans require 1 <= rpm <= 30")
        if self.retries != 0:
            raise RuntimeError("scheduled scans require retries=0")
        if self.cost_cap_usd > 100:
            raise RuntimeError("scheduled scan cost cap exceeds USD 100")


class SharedRateLimiter:
    """One serialized request budget shared by target and judge runners."""

    def __init__(
        self,
        rpm: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = 60.0 / rpm
        self._lock = threading.Lock()
        self._next_call = 0.0
        self._clock = clock
        self._sleep = sleep

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            if now < self._next_call:
                self._sleep(self._next_call - now)
            self._next_call = self._clock() + self._interval


class RateLimitedRunner:
    def __init__(self, base: Any, limiter: SharedRateLimiter) -> None:
        self._base = base
        self._limiter = limiter

    def model_id(self) -> str:
        return self._base.model_id()

    def complete(self, prompt: str) -> str:
        self._limiter.acquire()
        return self._base.complete(prompt)


def run_positive_egress_preflight(
    artifact_path: Path,
    *,
    requester: Callable[..., Any] = egress_get,
) -> dict[str, Any]:
    """Prove credential-free reachability and retain each HTTP response code."""
    results: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    dependency_failures: list[str] = []
    for host, url in PREFLIGHT_TARGETS.items():
        try:
            response = requester(url, timeout=10.0, follow_redirects=False)
            results[host] = {"url": url, "http_status_code": int(response.status_code)}
        except Exception as exc:
            if isinstance(exc, (ModuleNotFoundError, ImportError)):
                error_type = "dependency_missing"
                dependency_failures.append(host)
            else:
                error_type = type(exc).__name__
            failures.append(host)
            results[host] = {"url": url, "error_type": error_type}
    record = {
        "schema": "backbond.scheduled-egress-preflight/v1",
        "credential_free": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "endpoints": results,
        "passed": not failures,
    }
    artifact_path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if dependency_failures:
        raise RuntimeError(
            "scheduled egress preflight dependency missing: "
            + ", ".join(dependency_failures)
        )
    if failures:
        raise RuntimeError(f"scheduled egress preflight failed: {', '.join(failures)}")
    return record


def _incomplete_group(reason: Any, *, target_calls: int) -> str:
    if target_calls <= 0:
        return "dispatch_failure"
    value = str(reason or "").lower()
    if "judge" in value:
        return "missing_judge"
    if any(token in value for token in ("unavailable", "unsupported", "capability")):
        return "unavailable_capability"
    if any(token in value for token in ("transport", "timeout", "connection", "http", "network")):
        return "transport"
    return "format"


def _family_name(raw: Mapping[str, Any]) -> str:
    return str(
        raw.get("family")
        or raw.get("test_id")
        or raw.get("corpus_or_family")
        or raw.get("framework")
        or "unknown"
    )


def _family_summary(
    registry_rows: Any,
    evidence_rows: Any,
) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    families: dict[str, Counter[str]] = {}
    incomplete = Counter({name: 0 for name in INCOMPLETE_GROUPS})
    registry_items = (
        registry_rows
        if isinstance(registry_rows, Sequence)
        and not isinstance(registry_rows, (str, bytes, bytearray))
        else ()
    )
    for raw in registry_items:
        if not isinstance(raw, Mapping):
            continue
        family = _family_name(raw)
        counts = families.setdefault(
            family,
            Counter(
                {
                    name: 0
                    for name in (
                        "considered",
                        "routed",
                        "target_called",
                        "pass",
                        "fail",
                        "incomplete",
                    )
                }
            ),
        )
        counts["considered"] += 1
        if raw.get("status") == "active" and raw.get("production_runner_executable") is True:
            counts["routed"] += 1

    for raw in evidence_rows if isinstance(evidence_rows, list) else []:
        if not isinstance(raw, Mapping):
            continue
        family = _family_name(raw)
        counts = families.setdefault(
            family,
            Counter(
                {
                    name: 0
                    for name in (
                        "considered",
                        "routed",
                        "target_called",
                        "pass",
                        "fail",
                        "incomplete",
                    )
                }
            ),
        )
        if int(raw.get("target_calls") or 0) > 0:
            counts["target_called"] += 1
        verdict = str(raw.get("verdict") or "").upper()
        if raw.get("status") != "complete" or verdict not in {"PASS", "FAIL"}:
            counts["incomplete"] += 1
            incomplete[
                _incomplete_group(
                    raw.get("incomplete_reason"),
                    target_calls=int(raw.get("target_calls") or 0),
                )
            ] += 1
        else:
            counts[verdict.lower()] += 1
    return (
        {name: dict(counts) for name, counts in sorted(families.items())},
        dict(incomplete),
    )


def _operator_manifest(
    *,
    job_name: str,
    config: ScheduleConfig,
    job: Any,
    preflight_path: Path,
    started_at: str,
    finished_at: str,
    total_runtime_seconds: float,
    registry_rows: list[Mapping[str, Any]],
    preflight_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    final = dict(job.final_manifest or {})
    families, incomplete = _family_summary(registry_rows, final.get("evidence_rows", []))
    budget = final.get("budget") if isinstance(final.get("budget"), Mapping) else {}
    return {
        "schema": "backbond.scheduled-scan-manifest/v2",
        "job": job_name,
        "evidence_label": EVIDENCE_LABEL,
        "live_service_signed": False,
        "scan_id": job.scan_id or job.job_id,
        "job_id": job.job_id,
        "status": job.status,
        "bind_eligibility": job.bind_eligibility,
        "scanner_version": config.scanner_version,
        "corpus_version": config.corpus_version,
        "seed": config.seed,
        "concurrency": config.concurrency,
        "rate_limit_per_minute": config.rpm,
        "retries": config.retries,
        "cost_cap_usd": config.cost_cap_usd,
        "started_at": started_at,
        "finished_at": finished_at,
        "total_runtime_seconds": round(total_runtime_seconds, 3),
        "estimated_cost_usd": round(
            float(budget.get("estimated_judge_cost_usd") or 0), 6
        ),
        "egress_preflight_result_path": str(preflight_path),
        "egress_preflight": dict(preflight_result or {}),
        "families": families,
        "incomplete_by_reason": incomplete,
        "evidence_rows": len(final.get("evidence_rows", [])),
        "final_manifest_hash": job.final_manifest_hash,
        "executor": "api.full_scan_executor.dispatch_registry_attempt",
    }


def run_scheduled_scan(job_name: str, *, config: ScheduleConfig | None = None) -> Path:
    if job_name not in {"vapi", "zoo"}:
        raise ValueError("job must be vapi or zoo")
    if job_name == "zoo":
        raise RuntimeError(
            "scheduled zoo scans are disabled until a controlled-target runner is implemented"
        )
    secrets = map_secret_environment()
    config = config or ScheduleConfig.from_environment()
    config.validate()
    os.environ["ARE_FULL_SCAN_JUDGE_MODEL"] = config.judge_model

    run_date = datetime.now(timezone.utc).date().isoformat()
    output_dir = config.evidence_root / run_date / job_name
    output_dir.mkdir(parents=True, exist_ok=True)
    preflight_path = output_dir / "egress-preflight.json"
    negative_control_path = output_dir / "egress-negative-control.json"
    preflight_result = run_positive_egress_preflight(preflight_path)
    verify_live_egress_fail_closed(
        scanner_version=config.scanner_version,
        corpus_version=config.corpus_version,
        seed=config.seed,
        execution_environment="ariclaw-scheduled-unit",
        artifact_path=negative_control_path,
    )

    request = SimpleNamespace(
        agent_endpoint=config.agent_endpoint,
        agent_id=secrets["VAPI_ASSISTANT_ID"],
        tenant_id=config.tenant_id,
    )
    shared_limiter = SharedRateLimiter(config.rpm)
    target_factory = lambda endpoint, secret: RateLimitedRunner(  # noqa: E731
        customer_agent_http_runner_factory(endpoint, secret, secrets["VAPI_ASSISTANT_ID"]),
        shared_limiter,
    )
    judge_factory = lambda: RateLimitedRunner(default_judge_runner(), shared_limiter)  # noqa: E731
    budget = ScanBudget(
        max_estimated_judge_cost_usd=config.cost_cap_usd,
        max_wall_clock_seconds=config.timeout_seconds,
    )
    workers = build_registered_case_workers(
        request,
        secrets["VAPI_API_KEY"],
        budget=budget,
        evidence_root=output_dir / "evidence",
        subject_factory=target_factory,
        judge_factory=judge_factory,
    )
    rows = load_default_full_scan_registry_rows()
    cases = registry_cases_from_rows(rows)
    model_versions = next(iter(workers.values())).manifest_metadata()
    model_versions["evidence_class"] = EVIDENCE_LABEL
    store = FullScanJobStore()
    runtime_started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    job = store.submit(
        agent_id=secrets["VAPI_ASSISTANT_ID"],
        scanner_version=config.scanner_version,
        corpus_version=config.corpus_version,
        model_versions=model_versions,
        seed=config.seed,
        registry_cases=cases,
        scan_id=f"scheduled-{job_name}-{run_date}",
        wall_clock_budget_seconds=config.timeout_seconds,
        rate_limit_per_minute=config.rpm,
    )
    lease = store.lease(job.job_id, ttl_seconds=config.timeout_seconds)
    attempt_id = str(lease["attempt_id"])
    dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=attempt_id,
        registry_rows=rows,
        workers=workers,
    )
    finished = store.finalize(job.job_id, attempt_id=attempt_id)
    finished_at = datetime.now(timezone.utc).isoformat()
    manifest = _operator_manifest(
        job_name=job_name,
        config=config,
        job=finished,
        preflight_path=preflight_path,
        started_at=started_at,
        finished_at=finished_at,
        total_runtime_seconds=time.monotonic() - runtime_started,
        registry_rows=rows,
        preflight_result=preflight_result,
    )
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(manifest_path)
    return manifest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", choices=("vapi", "zoo"), required=True)
    args = parser.parse_args(argv)
    run_scheduled_scan(args.job)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
