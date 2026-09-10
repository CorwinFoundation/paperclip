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
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
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
from tools.framework_workers import build_live_framework_workers  # noqa: E402

SECRET_ENV = (
    "ARE_BUNDLE_SIGNING_KEY",
    "ARE_EVAL_PROVENANCE_KEY",
    "VAPI_API_KEY",
    "VAPI_ASSISTANT_ID",
    "OPENROUTER_API_KEY",
)
# The zoo lane runs against BackBond-operated controlled targets, so it needs
# neither Vapi credential. Listing its secrets separately is what makes
# "Vapi is never constructed or called" checkable rather than asserted.
ZOO_SECRET_ENV = (
    "ARE_BUNDLE_SIGNING_KEY",
    "ARE_EVAL_PROVENANCE_KEY",
    "OPENROUTER_API_KEY",
)
EVIDENCE_LABEL = "scheduled-controlled-evidence"
PREFLIGHT_TARGETS = {
    "github.com": "https://github.com/",
    "api.vapi.ai": "https://api.vapi.ai/",
    "openrouter.ai": "https://openrouter.ai/",
}
ZOO_PREFLIGHT_TARGETS = {
    "github.com": "https://github.com/",
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


def load_garak_dispatch_rows() -> list[dict[str, Any]]:
    """Select all 27 mapped Garak rows without changing production countability."""
    from tools.active_test_registry import build_registry

    selected = []
    for raw in build_registry():
        if raw.get("status") != "active" or raw.get("test_id") not in {
            "garak", "garak_beaaa152"
        }:
            continue
        row = dict(raw)
        row["garak_live_dispatch"] = True
        row["runtime_case_identity"] = f"{row['test_id']}:{row['case_id']}"
        selected.append(row)
    if len(selected) != 27:
        raise RuntimeError(f"expected 27 Garak dispatch rows, found {len(selected)}")
    return selected


def _config(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or not str(value).strip():
        raise RuntimeError(f"scheduled-scan configuration is missing: {name}")
    return str(value).strip()


def _default_execution_environment() -> str:
    """Name the host this unit actually runs on, so provenance is not a guess."""
    return f"{socket.gethostname().split('.')[0].lower()}-scheduled-unit"


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
    execution_environment: str = field(
        default_factory=_default_execution_environment
    )

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
            execution_environment=_config(
                "BACKBOND_SCAN_EXECUTION_ENVIRONMENT",
                _default_execution_environment(),
            ),
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
    targets: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Prove credential-free reachability and retain each HTTP response code."""
    results: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    dependency_failures: list[str] = []
    for host, url in (targets or PREFLIGHT_TARGETS).items():
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
    value = str(reason or "").lower()
    # The deterministic applicability gate decides before dispatch, so its rows
    # always carry ``target_calls == 0``.  Reporting that as a dispatch failure
    # sends a reader looking for a crash that never happened; it is a
    # capability the target did not declare.
    if "not_applicable" in value or "applicab" in value:
        return "unavailable_capability"
    if target_calls <= 0:
        return "dispatch_failure"
    if "judge" in value:
        return "missing_judge"
    if any(
        token in value
        for token in (
            "unavailable",
            "unsupported",
            "capability",
            "worker_error",
            "worker_failure",
            "dispatch",
        )
    ):
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
                        "not_applicable",
                    )
                }
            ),
        )
        counts["considered"] += 1
        if raw.get("status") == "active" and raw.get("production_runner_executable") is True:
            counts["routed"] += 1

    evidence_items = (
        evidence_rows
        if isinstance(evidence_rows, Sequence)
        and not isinstance(evidence_rows, (str, bytes, bytearray))
        else ()
    )
    for raw in evidence_items:
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
        if raw.get("status") == "complete" and verdict == "NOT_APPLICABLE":
            counts["not_applicable"] += 1
        elif raw.get("status") == "complete" and verdict == "EDGE_REFUSAL":
            counts["untested"] += 1
        elif raw.get("status") != "complete" or verdict not in {"PASS", "FAIL"}:
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
    evidence_rows = [
        {**dict(row), "executed_in_latest_evidenced_run": True}
        for row in final.get("evidence_rows", [])
        if isinstance(row, Mapping)
    ]
    families, incomplete = _family_summary(registry_rows, evidence_rows)
    dimensions: dict[str, Counter[str]] = {}
    for row in evidence_rows:
        rubric_point_id = str(row.get("rubric_point_id") or "")
        for dimension in filter(None, re.split(r"[,;]", rubric_point_id)):
            counts = dimensions.setdefault(
                dimension.strip(),
                Counter({"rows": 0, "pass": 0, "fail": 0, "not_applicable": 0, "incomplete": 0}),
            )
            counts["rows"] += 1
            verdict = str(row.get("verdict") or "").upper()
            if row.get("status") == "complete" and verdict in {"PASS", "FAIL", "NOT_APPLICABLE"}:
                counts[verdict.lower()] += 1
            else:
                counts["incomplete"] += 1
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
        "dimensions": {name: dict(counts) for name, counts in sorted(dimensions.items())},
        "incomplete_by_reason": incomplete,
        "evidence_rows": len(evidence_rows),
        "row_results": evidence_rows,
        "final_manifest_hash": job.final_manifest_hash,
        "executor": "api.full_scan_executor.dispatch_registry_attempt",
    }


def map_zoo_secret_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The three secrets the controlled zoo lane needs. No Vapi credential."""
    return {name: _required_secret(name, environ) for name in ZOO_SECRET_ENV}


def _zoo_family_summary(
    decisions: Sequence[Any],
    evidence_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """Per-family counts under zoo routing semantics.

    ``routed`` here means "sent to a controlled zoo target", which is narrower
    than the registry's own executable flag: the four voice families are
    considered and then explicitly not routed. Counting them as routed would
    hide the Vapi exclusion the manifest exists to show.
    """
    families: dict[str, Counter[str]] = {}
    incomplete = Counter({name: 0 for name in INCOMPLETE_GROUPS})
    incomplete.setdefault("unrouted_surface", 0)

    def _counter(family: str) -> Counter[str]:
        return families.setdefault(
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
                        "not_applicable",
                    )
                }
            ),
        )

    for decision in decisions:
        counts = _counter(decision.family)
        counts["considered"] += len(decision.rows)
        if decision.target is not None:
            counts["routed"] += len(decision.rows)
        else:
            # An unrouted family is terminal, not missing: every one of its
            # rows lands as a reason-coded incomplete.
            counts["incomplete"] += len(decision.rows)
            incomplete["unrouted_surface"] += len(decision.rows)

    for raw in evidence_rows:
        if not isinstance(raw, Mapping):
            continue
        counts = _counter(_family_name(raw))
        target_calls = int(raw.get("target_calls") or 0)
        if target_calls > 0:
            counts["target_called"] += 1
        verdict = str(raw.get("verdict") or "").upper()
        if raw.get("status") == "complete" and verdict == "NOT_APPLICABLE":
            counts["not_applicable"] += 1
        elif raw.get("status") != "complete" or verdict not in {"PASS", "FAIL"}:
            counts["incomplete"] += 1
            incomplete[
                _incomplete_group(
                    raw.get("incomplete_reason") or raw.get("verdict"),
                    target_calls=target_calls,
                )
            ] += 1
        else:
            counts[verdict.lower()] += 1

    return (
        {name: dict(counts) for name, counts in sorted(families.items())},
        dict(incomplete),
    )


def _run_zoo_target(
    target: Any,
    decisions: Sequence[Any],
    *,
    config: ScheduleConfig,
    output_dir: Path,
    run_date: str,
    limiter: SharedRateLimiter,
) -> tuple[Any, list[Mapping[str, Any]]]:
    """Submit and dispatch one job against a single controlled zoo surface.

    Each surface gets its own job because the capability manifest is what the
    executor gates family applicability on; one merged job would have to
    declare the union of every surface and would then run families against
    capabilities their assigned target does not have.
    """
    from harness.controlled_zoo import build_zoo_runner, controlled_tau_session_factory

    rows = [row for decision in decisions for row in decision.rows]
    if not rows:
        return None, []

    request = SimpleNamespace(
        agent_endpoint="controlled-zoo://" + target.name,
        agent_id=f"controlled-zoo-{target.name}",
        tenant_id=config.tenant_id,
        invocation_context={"target_capabilities": target.capability_manifest},
    )
    subject = RateLimitedRunner(build_zoo_runner(target), limiter)
    workers = build_registered_case_workers(
        request,
        "",
        budget=ScanBudget(
            max_estimated_judge_cost_usd=config.cost_cap_usd,
            max_wall_clock_seconds=config.timeout_seconds,
        ),
        evidence_root=output_dir / "evidence" / target.name,
        subject_factory=lambda endpoint, secret: subject,
        judge_factory=lambda: RateLimitedRunner(default_judge_runner(), limiter),
        tau_session_factory=controlled_tau_session_factory(subject),
    )
    model_versions = next(iter(workers.values())).manifest_metadata()
    model_versions["evidence_class"] = EVIDENCE_LABEL
    model_versions["zoo_target"] = target.name
    model_versions["adapter_proof"] = target.adapter_proof

    store = FullScanJobStore()
    job = store.submit(
        agent_id=request.agent_id,
        scanner_version=config.scanner_version,
        corpus_version=config.corpus_version,
        model_versions=model_versions,
        seed=config.seed,
        registry_cases=registry_cases_from_rows(rows),
        scan_id=f"scheduled-zoo-{target.name}-{run_date}",
        wall_clock_budget_seconds=config.timeout_seconds,
        rate_limit_per_minute=config.rpm,
        capability_manifest=target.capability_manifest,
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
    final = dict(finished.final_manifest or {})
    return finished, list(final.get("evidence_rows", []))


def run_scheduled_zoo_scan(*, config: ScheduleConfig | None = None) -> Path:
    """Run the controlled target zoo established by BEAAA-29961.

    Vapi is excluded structurally: its secrets are never read, its host is not
    in the zoo preflight set, ``harness.customer_runner`` is never imported on
    this path, and the census's four voice families route to no zoo target.
    """
    from harness.controlled_zoo import (
        EXCLUDED_TARGETS,
        SURFACE_CENSUS_SHA256,
        load_surface_census,
        route_registry_rows,
        zoo_targets,
    )

    map_zoo_secret_environment()
    config = config or ScheduleConfig.from_environment()
    config.validate()
    os.environ["ARE_FULL_SCAN_JUDGE_MODEL"] = config.judge_model

    run_date = datetime.now(timezone.utc).date().isoformat()
    output_dir = config.evidence_root / run_date / "zoo"
    output_dir.mkdir(parents=True, exist_ok=True)
    preflight_path = output_dir / "egress-preflight.json"
    preflight_result = run_positive_egress_preflight(
        preflight_path, targets=ZOO_PREFLIGHT_TARGETS
    )
    verify_live_egress_fail_closed(
        scanner_version=config.scanner_version,
        corpus_version=config.corpus_version,
        seed=config.seed,
        execution_environment=config.execution_environment,
        artifact_path=output_dir / "egress-negative-control.json",
    )

    census = load_surface_census()
    rows = load_default_full_scan_registry_rows()
    decisions = route_registry_rows(rows, census)

    limiter = SharedRateLimiter(config.rpm)
    targets = zoo_targets()
    by_target: dict[str, list[Any]] = {}
    for decision in decisions:
        if decision.target is not None:
            by_target.setdefault(decision.target, []).append(decision)

    runtime_started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    evidence_rows: list[Mapping[str, Any]] = []
    target_reports: dict[str, Any] = {}
    estimated_cost = 0.0
    for name in sorted(by_target):
        finished, target_rows = _run_zoo_target(
            targets[name],
            by_target[name],
            config=config,
            output_dir=output_dir,
            run_date=run_date,
            limiter=limiter,
        )
        evidence_rows.extend(target_rows)
        final = dict(getattr(finished, "final_manifest", None) or {})
        budget = final.get("budget") if isinstance(final.get("budget"), Mapping) else {}
        estimated_cost += float(budget.get("estimated_judge_cost_usd") or 0)
        target_reports[name] = {
            "job_id": getattr(finished, "job_id", None),
            "scan_id": getattr(finished, "scan_id", None),
            "status": getattr(finished, "status", None),
            "final_manifest_hash": getattr(finished, "final_manifest_hash", None),
            "adapter_proof": targets[name].adapter_proof,
            "capability_manifest": targets[name].capability_manifest,
            "families": sorted(d.family for d in by_target[name]),
            "rows": sum(len(d.rows) for d in by_target[name]),
            "target_calls": sum(int(r.get("target_calls") or 0) for r in target_rows),
        }
    finished_at = datetime.now(timezone.utc).isoformat()

    families, incomplete = _zoo_family_summary(decisions, evidence_rows)
    manifest = {
        "schema": "backbond.scheduled-scan-manifest/v2",
        "job": "zoo",
        "run_date": run_date,
        "evidence_label": EVIDENCE_LABEL,
        "live_service_signed": False,
        "scanner_version": config.scanner_version,
        "corpus_version": config.corpus_version,
        "seed": config.seed,
        "concurrency": config.concurrency,
        "rate_limit_per_minute": config.rpm,
        "retries": config.retries,
        "cost_cap_usd": config.cost_cap_usd,
        "started_at": started_at,
        "finished_at": finished_at,
        "total_runtime_seconds": round(time.monotonic() - runtime_started, 3),
        "estimated_cost_usd": round(estimated_cost, 6),
        "egress_preflight_result_path": str(preflight_path),
        "egress_preflight": dict(preflight_result),
        "families": families,
        "incomplete_by_reason": incomplete,
        "evidence_rows": len(evidence_rows),
        "executor": "api.full_scan_executor.dispatch_registry_attempt",
        "zoo": {
            "surface_census_sha256": SURFACE_CENSUS_SHA256,
            "excluded_targets": sorted(EXCLUDED_TARGETS),
            "targets": target_reports,
            "unrouted_families": {
                decision.family: decision.reason
                for decision in decisions
                if decision.target is None
            },
            "target_calls": sum(int(r.get("target_calls") or 0) for r in evidence_rows),
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(manifest_path)
    return manifest_path


def run_scheduled_scan(job_name: str, *, config: ScheduleConfig | None = None) -> Path:
    if job_name not in {"vapi", "vapi-garak", "zoo"}:
        raise ValueError("job must be vapi, vapi-garak, or zoo")
    if job_name == "zoo":
        return run_scheduled_zoo_scan(config=config)
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
        execution_environment=config.execution_environment,
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
    worker_factory = (
        build_live_framework_workers
        if job_name == "vapi-garak"
        else build_registered_case_workers
    )
    workers = worker_factory(
        request,
        secrets["VAPI_API_KEY"],
        budget=budget,
        evidence_root=output_dir / "evidence",
        subject_factory=target_factory,
        judge_factory=judge_factory,
    )
    rows = (
        load_garak_dispatch_rows()
        if job_name == "vapi-garak"
        else list(load_default_full_scan_registry_rows())
    )
    execution_flag = (
        "garak_live_dispatch" if job_name == "vapi-garak" else "production_runner_executable"
    )
    cases = registry_cases_from_rows(rows, execution_flag=execution_flag)
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
        execution_flag=execution_flag,
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
    parser.add_argument("--job", choices=("vapi", "vapi-garak", "zoo"), required=True)
    args = parser.parse_args(argv)
    run_scheduled_scan(args.job)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
