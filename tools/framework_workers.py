#!/usr/bin/env python3
"""Subprocess-isolated framework workers for BEAAA-13764.

The dispatcher owns reconciliation. This module owns the worker boundary:
dependency pins are checked inside a fresh Python process, and every outcome is
normalized to the WorkerAttemptResult JSON contract consumed by
api.full_scan_executor.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.framework_worker_registry import WORKERS, WorkerDefinition


WORKER_RESULT_SCHEMA = "framework-worker-attempt-result/v0.1"
PYTHON_PACKAGE_NAMES = {
    "inspect-ai",
    "pyrit",
    "agentdojo",
    "llm-guard",
    "presidio-analyzer",
    "presidio-anonymizer",
    "spacy",
    "transformers",
    "PyYAML",
}
CLI_PACKAGE_NAMES = {"promptfoo"}
SOURCE_PIN_PREFIXES = ("source-",)


class FrameworkWorkerBoundaryError(RuntimeError):
    pass


@dataclass(frozen=True)
class IsolatedFrameworkWorker:
    """Callable adapter used by the full-scan dispatcher.

    The object intentionally has the same call shape as
    api.full_scan_executor.FrameworkWorker. It does not import framework
    packages in the parent process; the child process performs dependency
    checks and emits a common JSON result.
    """

    worker_id: str
    timeout_seconds: int = 120

    def __call__(self, registry_row: Mapping[str, Any], job: Any) -> Any:
        payload = {
            "worker_id": self.worker_id,
            "registry_row": dict(registry_row),
            "job": {
                "job_id": job.job_id,
                "agent_id": job.agent_id,
                "scanner_version": job.scanner_version,
                "corpus_version": job.corpus_version,
                "model_versions": dict(job.model_versions),
                "seed": job.seed,
            },
        }
        proc = subprocess.run(
            [sys.executable, "-m", "tools.framework_workers", "run"],
            input=json.dumps(payload, sort_keys=True),
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            cwd=str(REPO_ROOT),
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            suffix = f":{detail[-1][:180]}" if detail else ""
            return _result(
                status="incomplete",
                evidence_ref=None,
                incomplete_reason=f"worker_process_error:{self.worker_id}{suffix}",
                model_version=_primary_model_version(payload["job"]["model_versions"]),
            )
        try:
            decoded = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            return _result(
                status="incomplete",
                evidence_ref=None,
                incomplete_reason=f"worker_invalid_json:{self.worker_id}:{exc.__class__.__name__}",
                model_version=_primary_model_version(payload["job"]["model_versions"]),
            )
        return _result(
            status=str(decoded.get("status") or "incomplete"),
            evidence_ref=decoded.get("evidence_ref"),
            incomplete_reason=decoded.get("incomplete_reason"),
            model_version=decoded.get("model_version"),
        )


def build_default_framework_workers(
    *,
    timeout_seconds: int = 120,
) -> dict[str, IsolatedFrameworkWorker]:
    return {
        worker_id: IsolatedFrameworkWorker(
            worker_id=worker_id,
            timeout_seconds=timeout_seconds,
        )
        for worker_id in WORKERS
    }


def run_worker_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    worker_id = str(payload.get("worker_id") or "")
    worker = WORKERS.get(worker_id)
    job = payload.get("job") if isinstance(payload.get("job"), Mapping) else {}
    registry_row = (
        payload.get("registry_row")
        if isinstance(payload.get("registry_row"), Mapping)
        else {}
    )
    model_version = _primary_model_version(job.get("model_versions", {}))
    if worker is None:
        return _result_dict(
            status="incomplete",
            evidence_ref=None,
            incomplete_reason=f"worker_definition_missing:{worker_id}",
            model_version=model_version,
        )

    dependency_error = _dependency_error(worker)
    if dependency_error:
        return _result_dict(
            status="incomplete",
            evidence_ref=None,
            incomplete_reason=f"worker_dependency_unavailable:{worker_id}:{dependency_error}",
            model_version=model_version,
        )

    return _adapter_result(worker, registry_row, job, model_version)


def _adapter_result(
    worker: WorkerDefinition,
    registry_row: Mapping[str, Any],
    job: Mapping[str, Any],
    model_version: str,
) -> dict[str, Any]:
    """Return the common contract for adapters not yet bound to live targets.

    BEAAA-13764's isolation layer must not silently exclude a row or fabricate a
    measured pass. Until a worker has a target-specific executable adapter, it
    returns a terminal incomplete evidence row with the same version and seed
    pins the measured adapter will use.
    """
    reason = f"worker_adapter_not_bound:{worker.worker_id}"
    if not registry_row.get("evidence_uri_template"):
        reason = f"{reason}:missing_evidence_uri_template"
    if not job.get("job_id"):
        reason = f"{reason}:missing_job_id"
    return _result_dict(
        status="incomplete",
        evidence_ref=None,
        incomplete_reason=reason,
        model_version=model_version,
    )


def _dependency_error(worker: WorkerDefinition) -> str | None:
    for name, expected in worker.dependency_pins.items():
        expected_s = str(expected)
        if expected_s.startswith(SOURCE_PIN_PREFIXES):
            continue
        if name.endswith(".py") or name.endswith(".txt") or "/" in name:
            if not (REPO_ROOT / name).exists():
                return f"{name}=missing-source"
            continue
        if name in CLI_PACKAGE_NAMES:
            if shutil.which(name) is None:
                return f"{name}=missing-cli"
            continue
        if name in {"tau-bench", "tau2-bench"}:
            # These are git pins in requirements/eval_pins.txt. The executable
            # adapter is blocked until the package exposes an importable module.
            return f"{name}=git-pin-not-importable"
        package_name = name if name in PYTHON_PACKAGE_NAMES else name
        try:
            installed = importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            return f"{name}=missing-package"
        if installed != expected_s:
            return f"{name}=installed-{installed}-expected-{expected_s}"
    return None


def _result(
    *,
    status: str,
    evidence_ref: str | None,
    incomplete_reason: str | None,
    model_version: str | None,
) -> Any:
    from api.full_scan_executor import WorkerAttemptResult

    return WorkerAttemptResult(
        status=status,
        evidence_ref=evidence_ref,
        incomplete_reason=incomplete_reason,
        model_version=model_version,
    )


def _result_dict(
    *,
    status: str,
    evidence_ref: str | None,
    incomplete_reason: str | None,
    model_version: str | None,
) -> dict[str, Any]:
    return {
        "schema": WORKER_RESULT_SCHEMA,
        "status": status,
        "evidence_ref": evidence_ref,
        "incomplete_reason": incomplete_reason,
        "model_version": model_version,
    }


def _primary_model_version(model_versions: Mapping[str, Any]) -> str:
    primary = model_versions.get("primary")
    if isinstance(primary, str) and primary:
        return primary
    return "model-version-unavailable"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run"])
    args = parser.parse_args(argv)
    if args.command == "run":
        payload = json.loads(sys.stdin.read() or "{}")
        result = run_worker_payload(payload)
        sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
