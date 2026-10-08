#!/usr/bin/env python3
"""Run one scheduled live scan through the in-repository full-scan executor.

The unit supplies exactly five secret environment variables. Everything else
comes from ``ops/scan-schedule/scan-schedule.conf``. This module deliberately
does not call the HTTP scan API: it constructs the same registered workers used
by the live Vapi evidence path and dispatches them in-process.
"""
from __future__ import annotations

import argparse
import hashlib
from collections import Counter
import json
import os
import re
import socket
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.full_scan_executor import (  # noqa: E402
    FullScanJobStore,
    TERMINAL_JOB_STATUSES,
    UpstreamQuotaAbort,
    case_id_from_registry_row,
    dispatch_registry_attempt,
    registry_cases_from_rows,
)
from api.full_scan_job_persistence import FullScanJobPersistence  # noqa: E402
from api.full_scan_worker import load_default_full_scan_registry_rows  # noqa: E402
from harness.customer_runner import customer_agent_http_runner_factory  # noqa: E402
from harness.egress import get as egress_get, verify_live_egress_fail_closed  # noqa: E402
from tools.registered_case_executor import (  # noqa: E402
    ScanBudget,
    build_registered_case_workers,
    default_judge_runner,
)
from tools.framework_workers import build_live_framework_workers  # noqa: E402
from tools._exception_scrubber import strip_http_diagnostics  # noqa: E402


def _pending_rows_for_resume(rows: Sequence[Mapping[str, Any]], resumed: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Bind the persisted pending roster to the dispatcher's stable identity."""
    pending_ids = {str(row.get("case_id") or "") for row in resumed.get("pending_cases", ()) if isinstance(row, Mapping)}
    selected = [row for row in rows if case_id_from_registry_row(row) in pending_ids]
    selected_ids = {case_id_from_registry_row(row) for row in selected}
    if selected_ids != pending_ids:
        raise RuntimeError(f"persisted pending roster does not bind to current registry: missing={sorted(pending_ids - selected_ids)}")
    return selected
from tools.founder_dev_mode import (  # noqa: E402
    ATTESTATION_FOUNDER_DEV,
    ATTESTATION_PRODUCTION_SIGNED,
    attestation_tier as _registry_attestation_tier,
)

SECRET_ENV = (
    "ARE_BUNDLE_SIGNING_KEY",
    "ARE_EVAL_PROVENANCE_KEY",
    "VAPI_API_KEY",
    "VAPI_ASSISTANT_ID",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
)
# The zoo lane runs against BackBond-operated controlled targets, so it needs
# neither Vapi credential. Listing its secrets separately is what makes
# "Vapi is never constructed or called" checkable rather than asserted.
ZOO_SECRET_ENV = (
    "ARE_BUNDLE_SIGNING_KEY",
    "ARE_EVAL_PROVENANCE_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
)
EVIDENCE_LABEL = "scheduled-controlled-evidence"
LIVE_CONTAINMENT_FAILURE_CASE = (
    "out_of_scope_containment",
    "oosc-001",
)
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
    "aborted_upstream",
    "upstream_http",
    "transport",
    "format",
    "missing_judge",
    "unavailable_capability",
    "insufficient_sample",
    "unclassified",
)


def _required_secret(name: str, environ: Mapping[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    value = source.get(name, "").strip()
    if not value:
        raise RuntimeError(f"required scheduled-scan secret is missing: {name}")
    return value


def map_secret_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the six-variable contract, allowing Anthropic fallback absence."""
    source = os.environ if environ is None else environ
    mapped = {
        name: _required_secret(name, source)
        for name in SECRET_ENV
        if name != "ANTHROPIC_API_KEY"
    }
    anthropic_key = source.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        mapped["ANTHROPIC_API_KEY"] = anthropic_key
    return mapped


GARAK_DISPATCH_MAPPINGS = (
    ("garak", "rubric/probe_mappings/garak.yaml"),
    ("garak_beaaa152", "rubric/probe_mappings/garak_beaaa152.yaml"),
)


def load_garak_live_dispatch_optins() -> set[tuple[str, str]]:
    """Read the authored `garak_live_dispatch: true` opt-ins from the mappings.

    The field is authored on the mapping row, not synthesized here. A row that
    does not carry it is out of the live scheduled scan, so registering a new
    upstream family cannot silently expand live dispatch: opting in is a
    reviewable mapping edit.
    """
    import yaml

    root = Path(__file__).resolve().parents[1]
    optins: set[tuple[str, str]] = set()
    for benchmark, rel in GARAK_DISPATCH_MAPPINGS:
        data = yaml.safe_load((root / rel).read_text(encoding="utf-8")) or {}
        for probe in data.get("probes") or []:
            if probe.get("garak_live_dispatch") is not True:
                continue
            case_id = str(probe.get("probe_id") or probe.get("id") or "")
            if case_id:
                optins.add((benchmark, case_id))
    return optins


def load_garak_dispatch_rows() -> list[dict[str, Any]]:
    """Select the Garak rows that opt in to live dispatch through the mapping.

    BEAAA-31700: this gate fails closed. Previously a hardcoded denylist of
    probe ids excluded the BEAAA-31018 PR 1 registrations, which meant the
    default for any newly mapped family was "dispatch live". Selection is now
    positive: only rows carrying an authored `garak_live_dispatch: true` are
    dispatched.
    """
    from tools.active_test_registry import build_registry

    optins = load_garak_live_dispatch_optins()
    selected = []
    for raw in build_registry():
        if raw.get("status") != "active":
            continue
        key = (str(raw.get("test_id") or ""), str(raw.get("case_id") or ""))
        if key not in optins:
            continue
        row = dict(raw)
        row["garak_live_dispatch"] = True
        row["runtime_case_identity"] = f"{row['test_id']}:{row['case_id']}"
        selected.append(row)
    if len(selected) != 29:
        raise RuntimeError(f"expected 29 Garak dispatch rows, found {len(selected)}")
    return selected


def _worker_factory_for_job(job_name: str) -> Callable[..., dict[str, Any]]:
    """Return the worker union needed by a scheduled Vapi job.

    The live factory starts with every registered-case worker and replaces the
    Garak entry with its native isolated worker.  Using it for the default
    registry preserves ordinary rows while ensuring promoted Garak rows cannot
    be shadowed by the generic executor.  The dedicated ``vapi-garak`` lane
    remains available for one-shot debugging.
    """
    if job_name in {"vapi", "vapi-garak", "vapi-containment-failure"}:
        return build_live_framework_workers
    return build_registered_case_workers


def _rows_for_job(job_name: str) -> tuple[list[dict[str, Any]], str]:
    """Return registry rows and their dispatch flag for a Vapi job."""
    if job_name == "vapi-garak":
        return load_garak_dispatch_rows(), "garak_live_dispatch"
    rows = list(load_default_full_scan_registry_rows())
    if job_name == "vapi-containment-failure":
        family, case_id = LIVE_CONTAINMENT_FAILURE_CASE
        rows = [
            row for row in rows
            if row.get("test_id") == family and row.get("case_id") == case_id
        ]
        if len(rows) != 1:
            raise RuntimeError(
                f"expected one live containment case {family}/{case_id}, found {len(rows)}"
            )
    return rows, "production_runner_executable"


def _config(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or not str(value).strip():
        raise RuntimeError(f"scheduled-scan configuration is missing: {name}")
    return str(value).strip()


def _ensure_writable_worker_home(evidence_root: Path) -> Path:
    """Guarantee the worker process has a writable ``HOME``.

    Garak, and the huggingface libraries it imports, write caches and config
    under ``HOME`` at import time. The scan unit runs as ``backbond-scan``,
    whose passwd home does not exist on the host, so an unset or unwritable
    ``HOME`` aborts the run before a single probe dispatches. The evidence root
    is already owned by the scan user, so a directory beneath it is writable
    without a privileged step. An already-writable ``HOME`` is left alone.
    """
    current = os.environ.get("HOME", "").strip()
    if current:
        candidate = Path(current)
        probe = candidate / ".backbond-home-probe"
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe.touch()
            probe.unlink()
            return candidate
        except OSError:
            pass

    home = evidence_root / ".worker-home"
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    os.environ["HOME"] = str(home)
    for name, leaf in (
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
    ):
        path = home / leaf
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)
    return home


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
    judge_provider: str = "anthropic"
    execution_environment: str = field(
        default_factory=_default_execution_environment
    )
    # Row cap per family. ``None`` is unlimited and is the only value the
    # scheduled units ever produce, so the installed timers keep running the
    # full corpus byte-identically. It is set from ``--limit-per-family`` for
    # an operator-run acceptance slice.
    limit_per_family: int | None = None
    conversation_retention_days: int = 14

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
            judge_provider=_config("BACKBOND_SCAN_JUDGE_PROVIDER", "anthropic"),
            judge_model=_config("BACKBOND_SCAN_JUDGE_MODEL"),
            execution_environment=_config(
                "BACKBOND_SCAN_EXECUTION_ENVIRONMENT",
                _default_execution_environment(),
            ),
            conversation_retention_days=int(
                _config("BACKBOND_SCAN_CONVERSATION_RETENTION_DAYS", "14")
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
        if self.limit_per_family is not None and self.limit_per_family < 1:
            raise RuntimeError("limit-per-family must be at least 1 when set")
        if self.judge_provider not in {"anthropic", "openrouter"}:
            raise RuntimeError("scheduled judge provider must be anthropic or openrouter")
        if self.conversation_retention_days < 1:
            raise RuntimeError("conversation retention must be at least 1 day")


def _prepare_conversation_log(config: ScheduleConfig, output_dir: Path) -> Path:
    """Create a private scan-time log and expire older conversation logs."""
    cutoff = time.time() - config.conversation_retention_days * 86400
    for path in config.evidence_root.glob("*/*/private-conversations.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except FileNotFoundError:
            pass
    output_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(output_dir, 0o700)
    path = output_dir / "private-conversations.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.close(fd)
    os.chmod(path, 0o600)
    return path


class ConversationRecordingRunner:
    """Record every target exchange immediately in generic conversation JSONL."""

    def __init__(self, base: Any, path: Path, *, target: str) -> None:
        self._base = base
        self._path = path
        self._target = target

    def model_id(self) -> str:
        return self._base.model_id()

    def complete(self, prompt: str) -> str:
        started = datetime.now(timezone.utc).isoformat()
        response = self._base.complete(prompt)
        record = {
            "internal_conversation_key": f"{self._target}:{uuid.uuid4().hex}",
            "messages": [
                {"role": "user", "content": prompt, "ts": started},
                {"role": "assistant", "content": response, "ts": datetime.now(timezone.utc).isoformat()},
            ],
        }
        payload = (json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        fd = os.open(self._path, os.O_WRONLY | os.O_APPEND)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
        return response


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


def _incomplete_reason_value(raw: Mapping[str, Any]) -> Any:
    """The most specific reason a row failed to yield a verdict.

    Runners that die inside the provider call never set ``incomplete_reason``;
    they set ``error``/``error_detail`` and leave the verdict ``unresolved``.
    Reading only the reason field made every one of those rows fall through to
    ``format``, which tells an operator the target returned malformed output
    when in fact the provider rejected the request before any output existed.
    """
    for key in ("incomplete_reason", "error_detail", "error"):
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return raw.get("verdict")


def _incomplete_group(reason: Any, *, target_calls: int) -> str:
    # BEAAA-31617 acceptance criterion 4. The reason now carries appended HTTP
    # diagnostics whose body and header text come from the customer endpoint or
    # an intermediary CDN, so it is attacker-influenced. The substring rules
    # below are unanchored, so leaving that text in the classified value would
    # let a response body choose this row's operator bucket: a body containing
    # "judge" routes to ``missing_judge`` and sends an operator after a
    # credential that is working. Classify on the reason as it read before the
    # diagnostics were appended, which is what keeps ``incomplete_by_reason``
    # comparable across dispatches.
    value = strip_http_diagnostics(str(reason or "")).lower()
    if value.startswith("aborted_upstream"):
        return "aborted_upstream"
    # The deterministic applicability gate decides before dispatch, so its rows
    # always carry ``target_calls == 0``.  Reporting that as a dispatch failure
    # sends a reader looking for a crash that never happened; it is a
    # capability the target did not declare.
    if "not_applicable" in value or "applicab" in value:
        return "unavailable_capability"
    # Provider HTTP failures get their own operator bucket.  Folding them into
    # "transport" hides the difference between a network fault we retry and a
    # 401/403/429 from the provider, which needs a credential or quota fix.
    # The row keeps the status-specific upstream_http_<code> reason for
    # diagnosis; the manifest groups it under upstream_http.
    if value.startswith("upstream_http_"):
        return "upstream_http"
    if value.startswith("garak_sample_size_below_floor"):
        return "insufficient_sample"
    if target_calls <= 0:
        return "dispatch_failure"
    # BEAAA-30934 work item 3. These two reach the coder with a target call
    # already spent, and both must be read before the generic ``judge``
    # substring below: an INCONCLUSIVE correctness label is a judge that
    # answered, not a judge that was missing, and routing it to
    # ``missing_judge`` would send an operator after a credential that is
    # working.
    if "unparsed" in value or "unparseable" in value:
        return "format"
    if "inconclusive" in value:
        return "inconclusive_judgement"
    if "adapter_supplied_no_reason" in value:
        return "unclassified"
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
    if "case_execution_error" in value:
        return "format"
    return "unclassified"


def _family_name(raw: Mapping[str, Any]) -> str:
    return str(
        raw.get("family")
        or raw.get("test_id")
        or raw.get("corpus_or_family")
        or raw.get("framework")
        or "unknown"
    )


def _limit_rows_per_family(
    rows: Sequence[Mapping[str, Any]],
    limit: int | None,
) -> list[Mapping[str, Any]]:
    """Keep at most ``limit`` registry rows per family, in registry order.

    ``None`` returns the rows unchanged, which is what keeps the scheduled
    lanes identical to before the flag existed.
    """
    if limit is None:
        return list(rows)
    kept: list[Mapping[str, Any]] = []
    seen: Counter[str] = Counter()
    for raw in rows:
        family = _family_name(raw)
        if seen[family] >= limit:
            continue
        seen[family] += 1
        kept.append(raw)
    return kept


def _limit_decisions_per_family(decisions: Sequence[Any], limit: int | None) -> list[Any]:
    """Cap the rows a *routed* zoo family contributes to the run.

    Unrouted decisions are returned untouched. Their rows never dispatch; they
    land as reason-coded incompletes, and truncating them would understate the
    unrouted surface in the manifest rather than shrink the run.
    """
    if limit is None:
        return list(decisions)
    limited = []
    for decision in decisions:
        if decision.target is None:
            limited.append(decision)
            continue
        limited.append(replace(decision, rows=tuple(decision.rows)[:limit]))
    return limited


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
                        "measured",
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
                        "measured",
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
        elif (
            raw.get("status") == "complete"
            and raw.get("numeric_result") is not None
            and raw.get("verdict") is None
        ):
            counts["measured"] += 1
        elif raw.get("status") != "complete" or verdict not in {"PASS", "FAIL"}:
            counts["incomplete"] += 1
            incomplete[
                _incomplete_group(
                    _incomplete_reason_value(raw),
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
    vapi_authorization: Mapping[str, Any] | None = None,
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
                Counter(
                    {
                        "rows": 0,
                        "pass": 0,
                        "fail": 0,
                        "measured": 0,
                        "not_applicable": 0,
                        "incomplete": 0,
                    }
                ),
            )
            counts["rows"] += 1
            verdict = str(row.get("verdict") or "").upper()
            if row.get("status") == "complete" and verdict in {"PASS", "FAIL", "NOT_APPLICABLE"}:
                counts[verdict.lower()] += 1
            elif (
                row.get("status") == "complete"
                and row.get("numeric_result") is not None
                and row.get("verdict") is None
            ):
                counts["measured"] += 1
            else:
                counts["incomplete"] += 1
    budget = final.get("budget") if isinstance(final.get("budget"), Mapping) else {}
    model_versions = getattr(job, "model_versions", {}) or {}
    judge_models = list(model_versions.get("judge_models") or [])
    judge_provider = (
        "anthropic"
        if judge_models and str(judge_models[0]).startswith("anthropic:")
        else "openrouter"
        if judge_models and str(judge_models[0]).startswith("openrouter:")
        else "unknown"
    )
    manifest_vapi_authorization = (
        dict(vapi_authorization) if vapi_authorization is not None else None
    )
    return {
        "schema": "backbond.scheduled-scan-manifest/v2",
        "job": job_name,
        # Which written authorization this run dispatched under (BEAAA-35702).
        "vapi_authorization": manifest_vapi_authorization,
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
        "limit_per_family": config.limit_per_family,
        "started_at": started_at,
        "finished_at": finished_at,
        "total_runtime_seconds": round(total_runtime_seconds, 3),
        "estimated_cost_usd": round(
            float(budget.get("estimated_judge_cost_usd") or 0), 6
        ),
        "judge_provider": judge_provider,
        "judge_models": judge_models,
        "egress_preflight_result_path": str(preflight_path),
        "egress_preflight": dict(preflight_result or {}),
        "families": families,
        "dimensions": {name: dict(counts) for name, counts in sorted(dimensions.items())},
        "incomplete_by_reason": incomplete,
        "evidence_rows": len(evidence_rows),
        "row_results": evidence_rows,
        "final_manifest_hash": job.final_manifest_hash,
        "executor": "api.full_scan_executor.dispatch_registry_attempt",
        # BEAAA-35757: index for PI/E&O underwriters (Aon five-category framework).
        # Maps each Aon category to the manifest field that answers it, or records
        # the gap when no field is present. Derived from data already in this
        # manifest — no new probe cost.
        "underwriter_evidence": _underwriter_evidence_index(
            evidence_rows=evidence_rows,
            job=job,
            attestation_tiers=_attestation_tier_by_test_id(registry_rows),
        ),
    }


# BEAAA-35793: an evidence row whose test_id is absent from the registry rows
# handed to this run has no declared attestation tier. It is labelled, never
# defaulted to production_signed — silently crediting an unknown row with
# production attestation is the exact shape BEAAA-24008 ruling D.4 forbids.
ATTESTATION_TIER_UNKNOWN = "unknown_not_in_registry"


def _attestation_tier_by_test_id(
    registry_rows: Sequence[Mapping[str, Any]] | None,
) -> dict[str, str]:
    """Map test_id -> declared attestation tier from the run's registry rows."""
    tiers: dict[str, str] = {}
    for raw in registry_rows or ():
        if not isinstance(raw, Mapping):
            continue
        test_id = str(raw.get("test_id") or "")
        if not test_id:
            continue
        tiers[test_id] = _registry_attestation_tier(raw)
    return tiers


def _row_attestation_tier(
    row: Mapping[str, Any], tiers: Mapping[str, str]
) -> str:
    """Attestation tier for one evidence row; unknown rather than assumed."""
    test_id = str(row.get("test_id") or "")
    if not test_id:
        return ATTESTATION_TIER_UNKNOWN
    return tiers.get(test_id, ATTESTATION_TIER_UNKNOWN)


def _underwriter_evidence_index(
    *,
    evidence_rows: list[dict[str, Any]],
    job: Any,
    attestation_tiers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Five-category PI/E&O evidence index for underwriter review (BEAAA-35757).

    Source: Aon September 2026 PI/E&O guidance — five things underwriters ask
    AI buyers to show. Each key names the category; the value names the manifest
    path that answers it or records the gap.
    """
    # (b) materiality — one row per declared capability with exercised rubric
    # points and their pass/fail verdicts. Derived from capability_manifest +
    # row_results. Not rated for dollar exposure (no field for that yet).
    tiers = attestation_tiers or {}
    cap_manifest = getattr(job, "capability_manifest", None) or {}
    cap_rubrics: dict[str, dict[str, Any]] = {}
    # BEAAA-35793: run-wide tier census, so a reader can tell dev-scored rows
    # from production-attested ones without re-deriving it from the registry.
    rows_by_tier: Counter[str] = Counter()
    for row in evidence_rows:
        cap = str(row.get("rubric_point_id") or "")
        verdict = row.get("verdict")
        family = str(row.get("family") or "")
        tier = _row_attestation_tier(row, tiers)
        rows_by_tier[tier] += 1
        for flag, declared in cap_manifest.items():
            if declared and flag.lower() in cap.lower():
                entry = cap_rubrics.setdefault(
                    flag,
                    {
                        "pass": 0,
                        "fail": 0,
                        "other": 0,
                        "families": set(),
                        "tiers": {},
                    },
                )
                if verdict == "PASS":
                    bucket = "pass"
                elif verdict == "FAIL":
                    bucket = "fail"
                else:
                    bucket = "other"
                entry[bucket] += 1
                tier_entry = entry["tiers"].setdefault(
                    tier, {"pass": 0, "fail": 0, "other": 0}
                )
                tier_entry[bucket] += 1
                if family:
                    entry["families"].add(family)
    capability_materiality = {
        flag: {
            "declared": cap_manifest.get(flag),
            "pass": v["pass"],
            "fail": v["fail"],
            "other": v["other"],
            "families_exercised": sorted(v["families"]),
            "materiality_rating": "not_rated",
            # Counts above are tier-blind totals; this splits them so
            # founder_dev_mode rows are separable from production_signed ones.
            "by_attestation_tier": {
                tier: dict(counts) for tier, counts in sorted(v["tiers"].items())
            },
            "dev_scored_rows": sum(
                counts["pass"] + counts["fail"] + counts["other"]
                for tier, counts in v["tiers"].items()
                if tier != ATTESTATION_PRODUCTION_SIGNED
            ),
        }
        for flag, v in cap_rubrics.items()
    }

    # (d) client-data protection — families list
    data_protection_families = sorted({
        str(row.get("family") or "")
        for row in evidence_rows
        if str(row.get("family") or "").startswith(("data", "exfil", "leak", "multi_tenant", "pii"))
    } - {""})

    # (c) human review — GOVERNANCE rubric point rows
    governance_rows = [
        r for r in evidence_rows
        if "governance" in str(r.get("rubric_point_id") or "").lower()
        or "approval" in str(r.get("probe_id") or "").lower()
        or "kill" in str(r.get("probe_id") or "").lower()
        or "logging" in str(r.get("probe_id") or "").lower()
    ]

    # (e) reconstruction — fields present in every manifest
    reconstruction_fields = [
        "evidence_ref",        # per-row
        "final_manifest_hash", # manifest
        "started_at",          # manifest
        "finished_at",         # manifest
        "scanner_version",     # manifest
        "corpus_version",      # manifest
        "seed",                # manifest
        "judge_models",        # manifest
    ]

    # BEAAA-35793: top-level tier summary. Names every tier present and states
    # plainly when any row in the run was scored under Founder Development Mode.
    dev_scored_rows = sum(
        count
        for tier, count in rows_by_tier.items()
        if tier != ATTESTATION_PRODUCTION_SIGNED
    )
    if not rows_by_tier:
        attestation_note = "No evidence rows in this run; no attestation tier to report."
    elif dev_scored_rows:
        attestation_note = (
            f"{dev_scored_rows} of {sum(rows_by_tier.values())} evidence rows in this "
            "run are NOT production-attested. Rows tagged "
            f"{ATTESTATION_FOUNDER_DEV!r} were scored under Founder Development Mode "
            f"and rows tagged {ATTESTATION_TIER_UNKNOWN!r} have no declared tier in "
            "this run's registry. Counts in this index that are not split by tier "
            "include them. Do not read this manifest as production-attested evidence."
        )
    else:
        attestation_note = (
            f"All {sum(rows_by_tier.values())} evidence rows in this run are tagged "
            f"{ATTESTATION_PRODUCTION_SIGNED!r}."
        )

    return {
        "attestation": {
            "tiers_present": sorted(rows_by_tier),
            "rows_by_attestation_tier": dict(sorted(rows_by_tier.items())),
            "evidence_rows": sum(rows_by_tier.values()),
            "dev_scored_rows": dev_scored_rows,
            "production_signed_rows": rows_by_tier.get(
                ATTESTATION_PRODUCTION_SIGNED, 0
            ),
            "all_rows_production_signed": bool(rows_by_tier)
            and dev_scored_rows == 0,
            "note": attestation_note,
        },
        "aon_category_a_ai_use_map": {
            "status": "present",
            "manifest_field": "capability_manifest",
            "supporting_field": "capability_declaration",
            "note": "Declares what the subject can do; not a per-business-process usage map.",
        },
        "aon_category_b_autonomy_and_materiality": {
            "status": "partial",
            "manifest_field": "capability_manifest",
            "gap": "materiality_rating is not_rated — no field ties a capability to financial exposure",
            "attestation_note": (
                "pass/fail/other are tier-blind totals; read by_attestation_tier "
                "before treating any count as production-attested."
            ),
            "capability_materiality": capability_materiality,
        },
        "aon_category_c_human_review": {
            "status": "present",
            "manifest_field": "dimensions",
            "supporting_field": "row_results[rubric_point_id contains governance]",
            "governance_row_count": len(governance_rows),
            "note": "Rubric point ids are engineering labels; a glossary mapping is not in this manifest.",
        },
        "aon_category_d_client_data_protection": {
            "status": "present",
            "manifest_field": "families",
            "data_protection_families": data_protection_families,
        },
        "aon_category_e_incident_reconstruction": {
            "status": "present",
            "manifest_fields": reconstruction_fields,
        },
    }


def map_zoo_secret_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return zoo secrets; direct Anthropic remains optional for fallback."""
    source = os.environ if environ is None else environ
    mapped = {
        name: _required_secret(name, source)
        for name in ZOO_SECRET_ENV
        if name != "ANTHROPIC_API_KEY"
    }
    anthropic_key = source.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        mapped["ANTHROPIC_API_KEY"] = anthropic_key
    return mapped


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
                        "measured",
                        "incomplete",
                        "not_applicable",
                    )
                }
            ),
        )

    evidenced_families = {
        _family_name(raw) for raw in evidence_rows if isinstance(raw, Mapping)
    }
    for decision in decisions:
        counts = _counter(decision.family)
        counts["considered"] += len(decision.rows)
        if decision.target is not None:
            counts["routed"] += len(decision.rows)
        elif decision.family not in evidenced_families:
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
        counts["judge_calls"] += int(raw.get("judge_calls") or 0)
        verdict = str(raw.get("verdict") or "").upper()
        if raw.get("status") == "complete" and verdict == "NOT_APPLICABLE":
            counts["not_applicable"] += 1
        elif (
            raw.get("status") == "complete"
            and raw.get("numeric_result") is not None
            and raw.get("verdict") is None
        ):
            counts["measured"] += 1
        elif raw.get("status") != "complete" or verdict not in {"PASS", "FAIL"}:
            counts["incomplete"] += 1
            incomplete[
                _incomplete_group(
                    _incomplete_reason_value(raw),
                    target_calls=target_calls,
                )
            ] += 1
        else:
            counts[verdict.lower()] += 1

    return (
        {name: dict(counts) for name, counts in sorted(families.items())},
        dict(incomplete),
    )


#: The verdicts that decide a case.  ``_zoo_family_summary`` already scores a
#: row against exactly this set before bucketing anything else as
#: ``incomplete``; the coverage predicate reads the same set so the manifest's
#: incomplete count and its coverage failures cannot disagree about a row.
TERMINAL_VERDICTS = frozenset({"PASS", "FAIL", "NOT_APPLICABLE"})


def _row_produced_grade(row: Mapping[str, Any]) -> bool:
    """Report whether a row reached a terminal grade.

    Judge calls are not proof of grading: deterministic oracles
    (``deterministic_local_oracle``, ``structured_invariant``, the registered
    lane-C runners) call the target, decide, and return a verdict without ever
    invoking an LLM.  Applicability gates grade ``not_applicable`` without
    calling the target at all.  The verdict, or a numeric measurement for the
    families that report one instead, is what says the row was graded.

    Having *a* verdict string is not the same as having a decision.  A runner
    that dies inside the provider call still writes ``unresolved`` (see
    ``_incomplete_reason_value``), and ``partial`` records a case the grader
    could not call either way - ``harness.bench.scored_lane_common`` names both
    as the outcomes a well-formed case never produces.  Those are the exact
    state this predicate exists to surface, so gradedness is an allowlist:
    a blacklist would have to grow every time a runner invents a sentinel.
    Reading the allowlist case-insensitively also keeps the mixed ``PASS``/
    ``pass`` spellings in the evidence rows from mattering here.
    """
    if str(row.get("status") or "") != "complete":
        return False
    verdict = str(row.get("verdict") or "").strip().upper()
    if verdict:
        return verdict in TERMINAL_VERDICTS
    return row.get("numeric_result") is not None


def _row_score_ineligible_by_design(row: Mapping[str, Any]) -> bool:
    """True when a row is deliberately outside the scored set.

    Executors normalize producer-specific markers to the top-level
    ``reported_not_scored`` field before a row reaches scan aggregation. The
    nested checks remain as a compatibility fallback for persisted evidence
    written before that normalization was introduced.
    """
    return bool(
        row.get("reported_not_scored") is True
        or (row.get("expected") or {}).get("synthetic_only") is True
        or (row.get("expected") or {}).get("reported_not_scored") is True
        or (row.get("judge") or {}).get("reported_not_scored") is True
    )


def _zoo_coverage_failures(
    decisions: Sequence[Any],
    evidence_rows: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """Report missing calls without rewriting genuine routing decisions."""
    calls: dict[str, Counter[str]] = {}
    ineligible: set[str] = set()
    for raw in evidence_rows:
        if not isinstance(raw, Mapping):
            continue
        family = _family_name(raw)
        family_calls = calls.setdefault(family, Counter())
        family_calls["target"] += int(raw.get("target_calls") or 0)
        family_calls["graded"] += int(_row_produced_grade(raw))
        if _row_score_ineligible_by_design(raw):
            ineligible.add(family)

    failures: dict[str, str] = {}
    for decision in decisions:
        if decision.target is None:
            continue
        family_calls = calls.get(decision.family, Counter())
        if family_calls["graded"] > 0:
            continue
        if decision.family in ineligible:
            failures[decision.family] = "score_ineligible_by_design"
        elif family_calls["target"] <= 0:
            failures[decision.family] = "no_target_calls_observed"
        else:
            failures[decision.family] = "no_graded_verdict_observed"
    return failures


def _zoo_routing_facts(
    decisions: Sequence[Any],
    target_reports: Mapping[str, Mapping[str, Any]],
    environ: Mapping[str, str] | None = None,
    *,
    job_store_preexisting: bool | None = None,
    evidence_rows: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Count-only routing facts for the 0644 operator summary.

    Uses the decisions as routed, before coverage failures re-label routed
    families as ``unrouted_surface``, so an operator can tell a routing gap
    from a dispatch gap. Credentials are reported as present/absent only.
    """
    # ``job_store_preexisting`` is true when an earlier run on the same date
    # already wrote this lane's job store. Jobs reopen by idempotency key, so a
    # terminal job is replayed from its stored rows with no target calls and
    # without the original abort reason.
    source = os.environ if environ is None else environ
    families_by_reason: Counter[str] = Counter()
    rows_by_reason: Counter[str] = Counter()
    for decision in decisions:
        reason = "routed" if decision.target is not None else str(decision.reason).split(":", 1)[0]
        families_by_reason[reason] += 1
        rows_by_reason[reason] += len(decision.rows)
    # BEAAA-39088: the executor aborts a target after eight consecutive
    # upstream 401/402/403/429 results, and a replay drops that reason. The
    # status code survives on the stored rows, so count it here; the key is
    # identified by a hash prefix only, never its value.
    upstream_http_by_status: Counter[str] = Counter()
    for row in evidence_rows:
        if not isinstance(row, Mapping):
            continue
        match = re.match(
            r"upstream_http_\d{3}",
            str(row.get("incomplete_reason") or row.get("reason") or ""),
        )
        if match:
            upstream_http_by_status[match.group(0)] += 1
    openrouter_key = source.get("OPENROUTER_API_KEY", "").strip()
    return {
        "job_store_preexisting": job_store_preexisting,
        "upstream_http_by_status": dict(sorted(upstream_http_by_status.items())),
        "openrouter_key_sha256_prefix": (
            hashlib.sha256(openrouter_key.encode("utf-8")).hexdigest()[:12]
            if openrouter_key
            else None
        ),
        "families_with_target": sum(1 for d in decisions if d.target is not None),
        "families_without_target": sum(1 for d in decisions if d.target is None),
        "families_by_reason": dict(sorted(families_by_reason.items())),
        "rows_by_reason": dict(sorted(rows_by_reason.items())),
        "targets": {
            name: {
                key: report.get(key)
                for key in ("status", "rows", "target_calls", "abort_reason")
            }
            | {"families": len(report.get("families") or ())}
            for name, report in sorted(target_reports.items())
        },
        "credentials_present": {
            name: bool(source.get(name, "").strip()) for name in ZOO_SECRET_ENV
        },
    }


def _reason_code_uncovered_zoo_routes(
    decisions: Sequence[Any], coverage_failures: Mapping[str, str]
) -> list[Any]:
    """Reclassify routes that never exercised both target and judge."""
    return [
        replace(
            decision,
            target=None,
            reason=coverage_failures[decision.family],
        )
        if decision.family in coverage_failures
        else decision
        for decision in decisions
    ]


def _zoo_coverage_passed(
    *, routed_families: int, target_calls: int, coverage_failures: Mapping[str, str]
) -> bool:
    """Fail closed when target work exists but the routed denominator is empty."""
    return not coverage_failures and not (target_calls > 0 and routed_families == 0)


def _reason_code_aborted_zoo_rows(
    decisions: Sequence[Any],
    evidence_rows: Sequence[Mapping[str, Any]],
    abort_reason: str,
) -> list[Mapping[str, Any]]:
    """Materialize every routed row omitted by an upstream fail-fast."""
    evidenced: set[tuple[str, str]] = set()
    for row in evidence_rows:
        if not isinstance(row, Mapping):
            continue
        test_id = str(row.get("test_id") or "")
        for key in ("probe_id", "case_id", "case_id_or_probe_id"):
            value = row.get(key)
            if value not in (None, ""):
                evidenced.add((test_id, str(value)))
    residual: list[Mapping[str, Any]] = []
    for decision in decisions:
        if decision.target is None:
            continue
        for row in decision.rows:
            test_id = str(row.get("test_id") or "")
            case_ids = {
                str(row.get(key))
                for key in ("runtime_case_identity", "case_id", "case_id_or_probe_id")
                if row.get(key) not in (None, "")
            }
            if any((test_id, case_id) in evidenced for case_id in case_ids):
                continue
            case_id = next(
                (
                    str(row.get(key))
                    for key in ("runtime_case_identity", "case_id", "case_id_or_probe_id")
                    if row.get(key) not in (None, "")
                ),
                "",
            )
            residual.append(
                {
                    "probe_id": case_id,
                    "case_id": case_id,
                    "test_id": test_id or decision.family,
                    "family": decision.family,
                    "rubric_point_id": str(
                        row.get("risk_or_rubric_id") or row.get("rubric_point_id") or ""
                    ),
                    "status": "incomplete",
                    "verdict": None,
                    "incomplete_reason": f"aborted_upstream:{abort_reason}",
                    "target_calls": 0,
                    "judge_calls": 0,
                    "score_contribution": False,
                    "disposition": "report-only",
                }
            )
    return residual


def _run_zoo_target(
    target: Any,
    decisions: Sequence[Any],
    *,
    config: ScheduleConfig,
    output_dir: Path,
    run_date: str,
    limiter: SharedRateLimiter,
    conversation_log: Path | None = None,
) -> tuple[Any, list[Mapping[str, Any]], str | None]:
    """Submit and dispatch one job against a single controlled zoo surface.

    Each surface gets its own job because the capability manifest is what the
    executor gates family applicability on; one merged job would have to
    declare the union of every surface and would then run families against
    capabilities their assigned target does not have.

    Returns (finished_job, evidence_rows, abort_reason_or_None).  When the
    dispatch loop trips the upstream-quota fail-fast the partial rows are
    checkpointed, the job is finalised, and the abort reason is returned so the
    caller can record it in the manifest and stop iterating over targets.
    """
    from harness.controlled_zoo import build_zoo_runner, controlled_tau_session_factory

    rows = [row for decision in decisions for row in decision.rows]
    if not rows:
        return None, [], None

    # Callers normally create the aggregate zoo directory, but the per-target
    # helper is also exercised directly.  Ensure SQLite's parent exists before
    # constructing the durable job store in either path.
    output_dir.mkdir(parents=True, exist_ok=True)
    if conversation_log is None:
        conversation_log = _prepare_conversation_log(config, output_dir)

    request = SimpleNamespace(
        agent_endpoint="controlled-zoo://" + target.name,
        agent_id=f"controlled-zoo-{target.name}",
        tenant_id=config.tenant_id,
        invocation_context={"target_capabilities": target.capability_manifest},
    )
    subject = ConversationRecordingRunner(
        RateLimitedRunner(build_zoo_runner(target), limiter),
        conversation_log,
        target=target.name,
    )
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

    # One durable database per scheduled zoo run keeps accepted work and each
    # case checkpoint across unit/process restarts.  The stable idempotency key
    # makes a restarted invocation reopen this target's original job instead of
    # creating a second job (and paying for its target/judge calls again).
    store = FullScanJobStore(
        persistence=FullScanJobPersistence(output_dir / "full_scan_jobs.sqlite3")
    )
    job = store.submit(
        agent_id=request.agent_id,
        scanner_version=config.scanner_version,
        corpus_version=config.corpus_version,
        model_versions=model_versions,
        seed=config.seed,
        registry_cases=registry_cases_from_rows(rows),
        scan_id=f"scheduled-zoo-{target.name}-{run_date}",
        idempotency_key=(
            f"scheduled-zoo:{run_date}:{target.name}:"
            f"{config.scanner_version}:{config.corpus_version}:{config.seed}"
            f"{_zoo_credential_epoch()}"
        ),
        wall_clock_budget_seconds=config.timeout_seconds,
        rate_limit_per_minute=config.rpm,
        capability_manifest=target.capability_manifest,
    )
    if job.status in TERMINAL_JOB_STATUSES:
        final = dict(job.final_manifest or {})
        return job, list(final.get("evidence_rows", [])), None

    # A fresh process owns no live attempt, even if the persisted lease has not
    # reached its wall-clock expiry. Recover clears that orphaned attempt and
    # retains every checkpoint already written by the prior process.
    if job.active_attempt_id is not None:
        store.recover()
        job = store.status(job.job_id)

    resume = store.resume(job.job_id)
    terminal_case_ids = {
        str(row.get("probe_id") or "")
        for row in resume["evidence_rows"]
        if isinstance(row, Mapping)
        and row.get("status") in {"complete", "incomplete", "failed", "skipped"}
    }
    pending_rows = [
        row
        for row in rows
        if str(
            row.get("runtime_case_identity")
            or row.get("case_id")
            or row.get("case_id_or_probe_id")
            or ""
        )
        not in terminal_case_ids
    ]
    lease = store.lease(job.job_id, ttl_seconds=config.timeout_seconds)
    attempt_id = str(lease["attempt_id"])
    abort_reason = None
    try:
        dispatch_registry_attempt(
            store,
            job.job_id,
            attempt_id=attempt_id,
            registry_rows=pending_rows,
            workers=workers,
        )
    except UpstreamQuotaAbort as exc:
        abort_reason = exc.abort_reason
    finished = store.finalize(job.job_id, attempt_id=attempt_id)
    final = dict(finished.final_manifest or {})
    target_rows = list(final.get("evidence_rows", []))
    if abort_reason is not None:
        # Keep this target's output terminally reconciled even when callers use
        # the per-target helper directly; the aggregate zoo path remains
        # idempotent because it only fills rows absent from evidence_rows.
        target_rows.extend(
            _reason_code_aborted_zoo_rows(decisions, target_rows, abort_reason)
        )
    return finished, target_rows, abort_reason



def _zoo_credential_epoch(environ: Mapping[str, str] | None = None) -> str:
    """Job-key suffix that changes when the OpenRouter credential changes.

    BEAAA-39088: on 2026-10-08 every zoo target aborted on upstream 401 with
    a dead key. Jobs reopen by idempotency key and a terminal job replays its
    stored rows, so rotating the key would not dispatch again until the next
    UTC date. Keying the job on a hash prefix of the credential makes a
    rotated key a fresh attempt the same day; an unchanged key still resumes.
    """
    source = os.environ if environ is None else environ
    key = source.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return ""
    return ":or-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _require_available_judge() -> str:
    """Fail closed before dispatch when no judge credential is configured.

    ``default_judge_runner`` degrades to a judge whose every ``complete`` call
    raises rather than refusing to build, so a scan with no usable credential
    still spends every target call and then grades none of them. Resolving the
    judge once here turns a six-hour run that returns only verdict-less rows
    into an immediate failure that names the missing credential.
    """
    from tools.registered_case_executor import default_judge_runner

    model_id = default_judge_runner().model_id()
    if model_id == "judge:unavailable":
        provider = os.environ.get("ARE_FULL_SCAN_JUDGE_PROVIDER", "") or "(unset)"
        raise RuntimeError(
            "scheduled scan has no usable judge: provider "
            f"{provider} is configured but its credential is absent; "
            "set ANTHROPIC_API_KEY or OPENROUTER_API_KEY"
        )
    return model_id


def run_scheduled_zoo_scan(
    *,
    config: ScheduleConfig | None = None,
    limit_per_family: int | None = None,
) -> Path:
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
    if limit_per_family is not None:
        config = replace(config, limit_per_family=limit_per_family)
    config.validate()
    _ensure_writable_worker_home(config.evidence_root)
    os.environ["ARE_FULL_SCAN_JUDGE_PROVIDER"] = config.judge_provider
    os.environ["ARE_FULL_SCAN_JUDGE_MODEL"] = config.judge_model
    os.environ["ARE_FULL_SCAN_JUDGE_BATCH"] = "true"
    _require_available_judge()

    run_date = datetime.now(timezone.utc).date().isoformat()
    output_dir = config.evidence_root / run_date / "zoo"
    output_dir.mkdir(parents=True, exist_ok=True)
    conversation_log = _prepare_conversation_log(config, output_dir)
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
    decisions = _limit_decisions_per_family(
        route_registry_rows(rows, census), config.limit_per_family
    )

    limiter = SharedRateLimiter(config.rpm)
    targets = zoo_targets()
    by_target: dict[str, list[Any]] = {}
    for decision in decisions:
        if decision.target is not None:
            by_target.setdefault(decision.target, []).append(decision)

    job_store_preexisting = (output_dir / "full_scan_jobs.sqlite3").exists()
    runtime_started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    evidence_rows: list[Mapping[str, Any]] = []
    target_reports: dict[str, Any] = {}
    estimated_cost = 0.0
    run_abort_reason: str | None = None
    for name in sorted(by_target):
        finished, target_rows, target_abort = _run_zoo_target(
            targets[name],
            by_target[name],
            config=config,
            output_dir=output_dir,
            run_date=run_date,
            limiter=limiter,
            conversation_log=conversation_log,
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
            "abort_reason": target_abort,
        }
        if target_abort is not None and run_abort_reason is None:
            run_abort_reason = target_abort
    finished_at = datetime.now(timezone.utc).isoformat()

    if run_abort_reason is not None:
        evidence_rows.extend(
            _reason_code_aborted_zoo_rows(decisions, evidence_rows, run_abort_reason)
        )

    coverage_failures = _zoo_coverage_failures(decisions, evidence_rows)
    effective_decisions = _reason_code_uncovered_zoo_routes(
        decisions, coverage_failures
    )
    families, incomplete = _zoo_family_summary(effective_decisions, evidence_rows)
    routed_families = sum(1 for counts in families.values() if counts["routed"] > 0)
    target_calls = sum(int(r.get("target_calls") or 0) for r in evidence_rows)
    judge_calls = sum(int(r.get("judge_calls") or 0) for r in evidence_rows)
    routed_rows = sum(len(decision.rows) for decision in decisions if decision.target is not None)
    terminal_routed_rows = len(evidence_rows)
    routed_reconciled = routed_rows == terminal_routed_rows
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
        "limit_per_family": config.limit_per_family,
        "started_at": started_at,
        "finished_at": finished_at,
        "total_runtime_seconds": round(time.monotonic() - runtime_started, 3),
        "estimated_cost_usd": round(estimated_cost, 6),
        "egress_preflight_result_path": str(preflight_path),
        "egress_preflight": dict(preflight_result),
        "aborted": run_abort_reason is not None,
        "abort_reason": run_abort_reason,
        "families": families,
        "routed_families": routed_families,
        "routing": _zoo_routing_facts(
            decisions,
            target_reports,
            job_store_preexisting=job_store_preexisting,
            evidence_rows=evidence_rows,
        ),
        "judge_calls": judge_calls,
        "coverage_failures": coverage_failures,
        "coverage_passed": _zoo_coverage_passed(
            routed_families=routed_families,
            target_calls=target_calls,
            coverage_failures=coverage_failures,
        ),
        "reconciliation": {
            "routed": routed_rows,
            "terminal": terminal_routed_rows,
            "equal": routed_reconciled,
        },
        "incomplete_by_reason": incomplete,
        "evidence_rows": len(evidence_rows),
        "executor": "api.full_scan_executor.dispatch_registry_attempt",
        "zoo": {
            "surface_census_sha256": SURFACE_CENSUS_SHA256,
            "excluded_targets": sorted(EXCLUDED_TARGETS),
            "targets": target_reports,
            "unrouted_families": {
                decision.family: decision.reason
                for decision in effective_decisions
                if decision.target is None
            },
            "target_calls": target_calls,
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(manifest_path)
    return manifest_path


def _vapi_capability_declaration(
    assistant_id: str,
    subject_factory: Callable[[], Any],
) -> tuple[dict[str, bool] | None, dict[str, Any]]:
    """Declare the scan subject's architecture flags before the job is submitted.

    BEAAA-30934. The lane submitted its job with no ``capability_manifest``, so
    ``evals.aiuc_path_b.applicability.resolve`` saw ``None`` and returned the
    unattested policy default — ``applicable=False``, ``source="policy_default"``,
    reason ``"<flag>=absent_capability_manifest"`` — for every gated family. That
    is a record that nobody was asked, and it is where 44 rows of the census died
    (``human_escalation_flagging`` 20, ``customer_defined_risk_pack`` 12,
    ``mcp_tool_integrity`` 12).

    The two tool-surface flags come from the captured assistant configuration,
    and so do the five ``insecure_code_generation`` gates on
    (``code_generation``, ``repository_write``, ``filesystem_write``,
    ``ci_execution``, ``package_publish``) — see
    ``harness.vapi_capability_manifest.CODE_GENERATION_READING`` for the one of
    those five that is a reading rather than a reading-off.
    ``has_structured_output`` cannot: it is derived by spending one bounded target
    call per gated family on that family's own benign canary and admitting the
    reply through the same reader lane C grades with (BEAAA-29804). A probe that
    cannot run leaves the flag undeclared rather than guessing it, which restores
    exactly today's behaviour for those rows and says so in the evidence.
    """

    from harness.vapi_capability_manifest import (
        CapabilityDeclarationError,
        declaration_evidence,
        declare_for_assistant,
    )

    try:
        declared = declare_for_assistant(assistant_id)
    except CapabilityDeclarationError as exc:
        return None, {
            "declared": False,
            "reason": f"capability_snapshot_unusable:{exc}",
        }
    if declared is None:
        return None, {
            "declared": False,
            "reason": "assistant_id_absent_from_captured_configuration",
        }

    evidence: dict[str, Any] = {
        "declared": True,
        "configuration": declaration_evidence(assistant_id, declared),
    }
    try:
        from harness.structured_output_probe import probe_live

        probe = probe_live(assistant_id, subject_factory().complete)
    except Exception as exc:  # a probe failure must not fabricate a surface
        evidence["structured_output_probe"] = {
            "ran": False,
            "error": f"{type(exc).__name__}: {exc}",
            "effect": "has_structured_output left undeclared",
        }
        return dict(declared), evidence

    merged = dict(declared)
    merged.update(probe.capability_manifest())
    evidence["structured_output_probe"] = {"ran": True, **probe.evidence()}
    return merged, evidence


# ---------------------------------------------------------------------------
# BEAAA-35702: fail closed on unauthorized VAPI traffic.
#
# Every lane below dispatches probes at ``https://api.vapi.ai``, a partner's
# live production API. The BEAAA-32522 release boundary allows that traffic
# only under a current written authorization; the July authorization covered
# one bounded run and lapsed. Until 2026-09-25 the only thing stopping the
# weekly timer from re-arming was a broken unit file, which is a bug, not a
# control. The gate below is the control: no authorization asserted in
# configuration, no credential read, no HTTP client, no traffic.
#
# The spec on BEAAA-35702 names ``vapi`` and ``vapi-garak``. The
# ``vapi-containment-failure`` lane sends to the same partner endpoint under
# the same release boundary, so it is gated here too; leaving it open would
# leave the hazard reachable by one CLI argument.
# ---------------------------------------------------------------------------
VAPI_TRAFFIC_JOBS = frozenset({"vapi", "vapi-garak", "vapi-containment-failure"})

VAPI_AUTH_REF_ENV = "BACKBOND_SCAN_VAPI_AUTH_REF"
VAPI_AUTH_EXPIRES_ENV = "BACKBOND_SCAN_VAPI_AUTH_EXPIRES"


class VapiAuthorizationError(RuntimeError):
    """Raised when a VAPI lane runs without a current written authorization."""


def _parse_authorization_expiry(raw: str) -> datetime:
    """Parse an ISO-8601 UTC instant, rejecting anything ambiguous.

    A value we cannot read is treated exactly like a missing value. Guessing at
    a malformed expiry is how a lapsed authorization gets honoured.
    """
    text = raw.strip()
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise VapiAuthorizationError(
            f"{VAPI_AUTH_EXPIRES_ENV} is not a readable ISO-8601 UTC instant: "
            f"{raw!r}. No VAPI traffic was sent."
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def require_vapi_authorization(job_name: str) -> dict[str, Any] | None:
    """Refuse to dispatch VAPI traffic without a current written authorization.

    Returns the authorization record for the run manifest, or ``None`` for a
    job that sends no VAPI traffic. Raises :class:`VapiAuthorizationError`
    before any credential is read or any HTTP client is constructed.
    """
    if job_name not in VAPI_TRAFFIC_JOBS:
        return None

    ref = os.environ.get(VAPI_AUTH_REF_ENV, "").strip()
    expires_raw = os.environ.get(VAPI_AUTH_EXPIRES_ENV, "").strip()
    missing = [
        name
        for name, value in ((VAPI_AUTH_REF_ENV, ref), (VAPI_AUTH_EXPIRES_ENV, expires_raw))
        if not value
    ]
    if missing:
        raise VapiAuthorizationError(
            f"job {job_name!r} targets a partner's live production API and is "
            f"gated on a current written authorization. Missing: "
            f"{', '.join(missing)}. No VAPI traffic was sent."
        )

    expires_at = _parse_authorization_expiry(expires_raw)
    now = datetime.now(timezone.utc)
    if expires_at <= now:
        raise VapiAuthorizationError(
            f"the VAPI authorization referenced by {VAPI_AUTH_REF_ENV}={ref!r} "
            f"expired at {expires_at.isoformat()} (now {now.isoformat()}). "
            f"No VAPI traffic was sent."
        )

    return {
        "authorization_ref": ref,
        "authorization_expires_at": expires_at.isoformat(),
        "authorization_checked_at": now.isoformat(),
        "gate": "BEAAA-35702",
    }


def run_scheduled_scan(
    job_name: str,
    *,
    config: ScheduleConfig | None = None,
    limit_per_family: int | None = None,
) -> Path:
    if job_name not in {"vapi", "vapi-garak", "vapi-containment-failure", "zoo"}:
        raise ValueError(
            "job must be vapi, vapi-garak, vapi-containment-failure, or zoo"
        )
    if job_name == "zoo":
        return run_scheduled_zoo_scan(config=config, limit_per_family=limit_per_family)
    # Fail closed before the credential read below and before any HTTP client
    # exists. Order is the whole control; a later check would still have read
    # the Vapi key.
    vapi_authorization = require_vapi_authorization(job_name)
    secrets = map_secret_environment()
    config = config or ScheduleConfig.from_environment()
    if limit_per_family is not None:
        config = replace(config, limit_per_family=limit_per_family)
    config.validate()
    _ensure_writable_worker_home(config.evidence_root)
    os.environ["ARE_FULL_SCAN_JUDGE_PROVIDER"] = config.judge_provider
    os.environ["ARE_FULL_SCAN_JUDGE_MODEL"] = config.judge_model
    os.environ["ARE_FULL_SCAN_JUDGE_BATCH"] = "true"
    _require_available_judge()

    run_date = datetime.now(timezone.utc).date().isoformat()
    output_dir = config.evidence_root / run_date / job_name
    output_dir.mkdir(parents=True, exist_ok=True)
    conversation_log = _prepare_conversation_log(config, output_dir)
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
    target_factory = lambda endpoint, secret: ConversationRecordingRunner(  # noqa: E731
        RateLimitedRunner(
            customer_agent_http_runner_factory(endpoint, secret, secrets["VAPI_ASSISTANT_ID"]),
            shared_limiter,
        ),
        conversation_log,
        target="own-account",
    )
    judge_factory = lambda: RateLimitedRunner(default_judge_runner(), shared_limiter)  # noqa: E731
    capability_manifest, capability_evidence = _vapi_capability_declaration(
        secrets["VAPI_ASSISTANT_ID"],
        lambda: target_factory(config.agent_endpoint, secrets["VAPI_API_KEY"]),
    )
    request.invocation_context = {"target_capabilities": capability_manifest}
    budget = ScanBudget(
        max_estimated_judge_cost_usd=config.cost_cap_usd,
        max_wall_clock_seconds=config.timeout_seconds,
    )
    worker_factory = _worker_factory_for_job(job_name)
    workers = worker_factory(
        request,
        secrets["VAPI_API_KEY"],
        budget=budget,
        evidence_root=output_dir / "evidence",
        subject_factory=target_factory,
        judge_factory=judge_factory,
    )
    rows, execution_flag = _rows_for_job(job_name)
    rows = _limit_rows_per_family(rows, config.limit_per_family)
    cases = registry_cases_from_rows(rows, execution_flag=execution_flag)
    model_versions = next(iter(workers.values())).manifest_metadata()
    model_versions["evidence_class"] = EVIDENCE_LABEL
    # Persist the live run beside its evidence.  The stable idempotency key
    # reopens the same job after a service/process restart, and the per-case
    # checkpoints let us dispatch only rows that have not reached a terminal
    # state.  This prevents a restart from repeating paid target/judge calls.
    store = FullScanJobStore(
        persistence=FullScanJobPersistence(output_dir / "full_scan_jobs.sqlite3")
    )
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
        idempotency_key=(
            f"scheduled-{job_name}:{run_date}:"
            f"{config.scanner_version}:{config.corpus_version}:{config.seed}"
        ),
        wall_clock_budget_seconds=config.timeout_seconds,
        rate_limit_per_minute=config.rpm,
        capability_manifest=capability_manifest,
    )
    vapi_abort_reason: str | None = None
    if job.status in TERMINAL_JOB_STATUSES:
        finished = job
    else:
        if job.active_attempt_id is not None:
            store.recover()
            job = store.status(job.job_id)
        resumed = store.resume(job.job_id)
        pending_rows = _pending_rows_for_resume(rows, resumed)
        lease = store.lease(job.job_id, ttl_seconds=config.timeout_seconds)
        attempt_id = str(lease["attempt_id"])
        try:
            dispatch_registry_attempt(
                store,
                job.job_id,
                attempt_id=attempt_id,
                registry_rows=pending_rows,
                workers=workers,
                execution_flag=execution_flag,
            )
        except UpstreamQuotaAbort as exc:
            vapi_abort_reason = exc.abort_reason
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
        vapi_authorization=vapi_authorization,
    )
    manifest["aborted"] = vapi_abort_reason is not None
    manifest["abort_reason"] = vapi_abort_reason
    manifest["capability_manifest"] = capability_manifest
    manifest["capability_declaration"] = capability_evidence
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(manifest_path)
    return manifest_path


#: Manifest fields an operator seat may read without the scan user's
#: privileges.  Counts, timings and versions only: no transcripts, no target
#: payloads, no credentials (BEAAA-39088).
OPERATOR_SUMMARY_FIELDS = (
    "job", "run_date", "scanner_version", "corpus_version", "seed",
    "started_at", "finished_at", "total_runtime_seconds",
    "estimated_cost_usd", "aborted", "abort_reason", "coverage_passed",
    "coverage_failures", "reconciliation", "incomplete_by_reason",
    "evidence_rows", "routed_families", "judge_calls", "families", "routing",
)


def publish_operator_summary(manifest_path: Path) -> Path:
    """Write a world-readable count-only summary beside the private run dir.

    The run directory stays 0700 because it holds conversation logs.  The
    summary lands at ``<evidence_root>/<run_date>/<job>.summary.json`` with
    mode 0644 and is echoed as one journal line, so an operator can see why
    a run passed or failed without root.
    """
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # Observability must never change a scan's outcome. A real run that
        # reached this point already has its manifest; the zoo branch in main()
        # re-reads it and still fails loudly if it is missing. So report and
        # return rather than turning an unreadable manifest into a crash.
        print(
            json.dumps({"operator_summary_skipped": str(manifest_path), "error": str(exc)},
                       sort_keys=True),
            file=sys.stderr,
        )
        return manifest_path
    summary = {key: manifest[key] for key in OPERATOR_SUMMARY_FIELDS if key in manifest}
    summary["schema"] = "backbond.scheduled-scan-operator-summary/v1"
    job = str(manifest.get("job") or manifest_path.parent.name)
    path = manifest_path.parent.parent / f"{job}.summary.json"
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o644)
    brief = {key: summary.get(key) for key in (
        "job", "total_runtime_seconds", "aborted", "abort_reason",
        "coverage_passed", "reconciliation", "evidence_rows", "incomplete_by_reason",
    )}
    print(json.dumps({"operator_summary": str(path), **brief}, sort_keys=True), file=sys.stderr)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--job",
        choices=("vapi", "vapi-garak", "vapi-containment-failure", "zoo"),
        required=True,
    )
    parser.add_argument(
        "--limit-per-family",
        type=int,
        default=None,
        metavar="N",
        help=(
            "cap each routed family at N cases for this run; omitted means "
            "unlimited, which is what the scheduled units run"
        ),
    )
    args = parser.parse_args(argv)
    if args.limit_per_family is not None and args.limit_per_family < 1:
        parser.error("--limit-per-family must be at least 1")
    try:
        manifest_path = run_scheduled_scan(
            args.job, limit_per_family=args.limit_per_family
        )
    except VapiAuthorizationError as exc:
        # Fail closed, loudly, with a non-zero exit and no traceback: the
        # journal line an operator reads should say what is missing.
        print(f"scheduled-scan refused to run: {exc}", file=sys.stderr)
        return 2
    publish_operator_summary(manifest_path)
    if args.job == "zoo":
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("aborted") or not manifest.get("reconciliation", {}).get("equal", False):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
