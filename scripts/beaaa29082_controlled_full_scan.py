#!/usr/bin/env python3
"""BEAAA-29082: controlled NON-MOCKED full scan + signed manifest.

Drives the production full-scan execution layer against the controlled VAPI
assistant and emits a signed attempt manifest whose acceptance_gate must pass.

Modes
  --preflight  validate wiring + one target probe + one judge probe. No suite.
  --run        submit, dispatch every active executable case, sign the manifest.

Acceptance (BEAAA-29082 / BEAAA-29043):
  acceptance_gate.passed == true
  incomplete == false, counts.missing_required == 0
  no mock: model_version rows
  acquisition_basis histogram with zero `missing` rows
  tau_support_crm cases at weight 0.0 with scoring_active false
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.full_scan_executor import (  # noqa: E402
    FullScanJobStore,
    build_attempt_manifest,
    case_id_from_registry_row,
    dispatch_registry_attempt,
    registry_cases_from_rows,
)
from api.full_scan_job_persistence import FullScanJobPersistence  # noqa: E402
from tools.registered_case_executor import (  # noqa: E402
    ScanBudget,
    build_registered_case_workers,
    default_judge_runner,
)

VAPI_ASSISTANT_ID = "09aea361-04b3-4a62-bcb0-2f2582f11ec8"
DEFAULT_ENDPOINT = "https://api.vapi.ai/chat"
EXECUTION_FLAG = "production_runner_executable"
DEFAULT_JOB_STORE = Path(
    os.environ.get(
        "ARE_FULL_SCAN_JOB_STORE",
        REPO_ROOT / "artifacts" / "BEAAA-29082" / "full_scan_jobs.sqlite3",
    )
)
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass
class ScanRequest:
    """Minimal scan-request shape consumed by build_registered_case_workers."""

    agent_id: str
    agent_endpoint: str
    tenant_id: str | None = None


def load_registry_rows() -> list[dict[str, Any]]:
    from tools.active_test_registry import build_registry

    registry = build_registry()
    rows = registry.get("tests") if isinstance(registry, dict) else registry
    if rows is None:
        raise SystemExit("registry produced no rows")
    return [dict(r) for r in rows]


def executable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        r
        for r in rows
        if r.get("status") == "active" and r.get(EXECUTION_FLAG) is True
    ]


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"required env var not set: {name}")
    return value


def build_workers(endpoint: str, evidence_root: Path):
    secret = require_env("VAPI_API_KEY")
    request = ScanRequest(
        agent_id=VAPI_ASSISTANT_ID,
        agent_endpoint=endpoint,
        tenant_id="backbond-internal",
    )
    return build_registered_case_workers(
        request,
        secret,
        budget=ScanBudget.from_env(),
        evidence_root=evidence_root,
    )


def preflight(endpoint: str, evidence_root: Path) -> int:
    print(f"[preflight] repo root      : {REPO_ROOT}")
    print(f"[preflight] endpoint       : {endpoint}")
    print(f"[preflight] evidence root  : {evidence_root}")

    for name in ("VAPI_API_KEY", "OPENROUTER_API_KEY", "ARE_BUNDLE_SIGNING_KEY"):
        print(f"[preflight] env {name:24s}: {'SET' if os.environ.get(name) else 'MISSING'}")

    rows = load_registry_rows()
    active = executable_rows(rows)
    print(f"[preflight] registry rows  : {len(rows)} total, {len(active)} executable")

    cases = registry_cases_from_rows(active, execution_flag=EXECUTION_FLAG)
    print(f"[preflight] executable cases: {len(cases)}")

    tau = [r for r in active if "tau_support_crm" in str(r.get("test_id") or r.get("case_id") or "")]
    print(f"[preflight] tau_support_crm rows: {len(tau)}")
    for r in tau[:5]:
        print(
            f"             {r.get('test_id') or r.get('case_id')} "
            f"scoring_active={r.get('scoring_active')} weight={r.get('score_weight')}"
        )

    print("[preflight] building workers (binds target + judge transports)...")
    workers = build_workers(endpoint, evidence_root)
    print(f"[preflight] workers bound   : {len(workers)}")
    sample = next(iter(workers.values()))
    meta = sample.manifest_metadata()
    print(f"[preflight] target_model    : {meta.get('target_model')}")
    print(f"[preflight] judge_models    : {meta.get('judge_models')}")

    print("[preflight] judge transport probe...")
    judge = default_judge_runner()
    reply = judge.complete("Reply with the single word: ready") if hasattr(judge, "complete") else None
    print(f"[preflight] judge reply     : {str(reply)[:120]!r}")
    print("[preflight] OK")
    return 0


def open_job_store(job_store_path: Path) -> FullScanJobStore:
    """Open the durable job store and run the documented startup recovery sweep.

    Without persistence every invocation started from an empty in-memory store,
    so an interrupted scan was unrecoverable by construction. ``recover()``
    clears the dead lease a killed process left behind and re-queues the job.
    """
    job_store_path = Path(job_store_path)
    job_store_path.parent.mkdir(parents=True, exist_ok=True)
    store = FullScanJobStore(persistence=FullScanJobPersistence(job_store_path))
    recovery = store.recover()
    if recovery.get("resumed") or recovery.get("failed"):
        print(
            f"[store] startup recovery: resumed={recovery.get('resumed')} "
            f"failed={recovery.get('failed')}",
            flush=True,
        )
    return store


def candidate_sha() -> str:
    """Return the exact commit whose runner/probe bytes will execute."""
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not _GIT_SHA_RE.fullmatch(sha):
        raise SystemExit(f"invalid candidate SHA from git: {sha!r}")
    return sha


def completed_case_ids(
    store: FullScanJobStore, job_id: str, *, expected_candidate_sha: str
) -> set[str]:
    """Case ids whose checkpointed evidence is complete and reusable.

    Keyed on ``probe_id``, which ``dispatch_registry_attempt`` sets from
    ``case_id_from_registry_row`` — the same function used to filter pending
    registry rows, so both sides stay in one identity namespace.
    """
    job = store.status(job_id)
    if job.model_versions.get("runner_candidate_sha") != expected_candidate_sha:
        return set()
    snapshot = store.resume(job_id)
    return {
        str(row.get("probe_id"))
        for row in snapshot.get("evidence_rows", [])
        if row.get("status") == "complete"
        and str(row.get("evidence_ref") or "").startswith(f"evidence://{job_id}/")
        and row.get("derived_result_id")
        == f"{job_id}:{expected_candidate_sha}:{row.get('probe_id')}:{job.seed}"
    }


def job_matches_fingerprint(
    job: Any,
    *,
    agent_id: str,
    scanner_version: str,
    corpus_version: str,
    model_versions: dict[str, Any],
    seed: int,
    registry_cases: Any,
) -> bool:
    """Return true only when persisted evidence belongs to this exact run."""
    return (
        job.agent_id == agent_id
        and job.scanner_version == scanner_version
        and job.corpus_version == corpus_version
        and dict(job.model_versions) == dict(model_versions)
        and job.seed == seed
        and tuple(job.registry_cases) == tuple(registry_cases)
    )


def newest_resumable_job_id(store: FullScanJobStore, **fingerprint: Any) -> str | None:
    """Newest job with an exact six-field fingerprint, including completed jobs."""
    for job_id in reversed(store.job_ids()):
        job = store.status(job_id)
        if job_matches_fingerprint(job, **fingerprint):
            return job_id
    return None


def run_scan(
    endpoint: str,
    evidence_root: Path,
    seed: int,
    out: Path,
    *,
    job_store_path: Path | None = None,
    job_id: str | None = None,
    resume: bool = False,
    fresh: bool = False,
) -> int:
    started = time.time()
    rows = load_registry_rows()
    active = executable_rows(rows)
    cases = registry_cases_from_rows(active, execution_flag=EXECUTION_FLAG)
    print(f"[run] executable cases: {len(cases)}", flush=True)

    workers = build_workers(endpoint, evidence_root)
    sample = next(iter(workers.values()))
    run_candidate_sha = candidate_sha()
    model_versions = {
        **sample.manifest_metadata(),
        "runner_candidate_sha": run_candidate_sha,
    }

    store = open_job_store(job_store_path or DEFAULT_JOB_STORE)
    scanner_version = os.environ.get("ARE_SCANNER_VERSION", "1.3.0")
    corpus_version = os.environ.get("ARE_CORPUS_VERSION", "1.3.0")
    fingerprint = {
        "agent_id": VAPI_ASSISTANT_ID,
        "scanner_version": scanner_version,
        "corpus_version": corpus_version,
        "model_versions": dict(model_versions),
        "seed": seed,
        "registry_cases": cases,
    }

    if fresh:
        job_id = None
        print("[run] --fresh requested; starting a new job", flush=True)
    elif job_id is None:
        job_id = newest_resumable_job_id(store, **fingerprint)
        if job_id is None:
            print(
                "[run] no exact fingerprint match; starting a new job",
                flush=True,
            )

    if job_id is not None:
        candidate = store.status(job_id)
        if job_matches_fingerprint(candidate, **fingerprint):
            job = candidate
            print(f"[run] resuming existing job {job.job_id} (status={job.status})", flush=True)
        else:
            print(
                f"[run] job {job_id} fingerprint mismatch; starting a new job",
                flush=True,
            )
            job_id = None
    if job_id is None:
        job = store.submit(
            agent_id=VAPI_ASSISTANT_ID,
            scanner_version=scanner_version,
            corpus_version=corpus_version,
            model_versions=model_versions,
            seed=seed,
            registry_cases=cases,
        )

    if job.status == "complete" and job.final_manifest is not None:
        manifest = dict(job.final_manifest)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        print(f"[run] exact completed job {job.job_id}; manifest re-emitted: {out}", flush=True)
        verify(manifest)
        return 0 if manifest.get("acceptance_gate", {}).get("passed") else 1

    print("=" * 72, flush=True)
    print(f"[run] job_id: {job.job_id}", flush=True)
    print(f"[run] job store: {job_store_path or DEFAULT_JOB_STORE}", flush=True)
    print(
        f"[run] if this run is killed, resume with: --run --job-id {job.job_id}",
        flush=True,
    )
    print("=" * 72, flush=True)

    lease = store.lease(job.job_id, ttl_seconds=6 * 3600)
    if not lease.get("leased"):
        print(
            f"[run] job {job.job_id} already leased by attempt "
            f"{lease.get('active_attempt_id')} until {lease.get('lease_expires_at')}; "
            "refusing to double-dispatch",
            flush=True,
        )
        return 3
    attempt_id = lease["attempt_id"]
    print(f"[run] attempt_id: {attempt_id}", flush=True)

    completed = completed_case_ids(
        store, job.job_id, expected_candidate_sha=run_candidate_sha
    )
    pending = [row for row in active if case_id_from_registry_row(row) not in completed]
    print(
        f"[run] reusing {len(completed)} checkpointed case(s); "
        f"dispatching {len(pending)}",
        flush=True,
    )

    job = dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=attempt_id,
        registry_rows=pending,
        workers=workers,
        execution_flag=EXECUTION_FLAG,
        # A Paperclip heartbeat can terminate between any two rows. Persist
        # each terminal row before starting the next paid target/judge call so
        # restart never repeats already-completed work.
        checkpoint_every=1,
    )
    print(f"[run] dispatch complete in {time.time() - started:.1f}s", flush=True)

    manifest = build_attempt_manifest(job)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[run] manifest written: {out}", flush=True)

    verify(manifest)
    return 0 if manifest.get("acceptance_gate", {}).get("passed") else 1


def verify(manifest: dict[str, Any]) -> None:
    gate = manifest.get("acceptance_gate", {})
    counts = manifest.get("counts", {})
    rows = manifest.get("evidence_rows", [])

    basis: dict[str, int] = {}
    for row in rows:
        key = str(row.get("acquisition_basis") or "missing")
        basis[key] = basis.get(key, 0) + 1

    mock_rows = [
        r for r in rows if str(r.get("model_version") or "").startswith("mock:")
    ]

    print("\n=== ACCEPTANCE ===")
    print(f"acceptance_gate.passed   : {gate.get('passed')}")
    print(f"incomplete               : {manifest.get('incomplete')}")
    print(f"counts.missing_required  : {counts.get('missing_required')}")
    print(f"counts.expected          : {counts.get('expected')}")
    print(f"counts.evidence_rows     : {counts.get('evidence_rows')}")
    print(f"reconciliation           : {manifest.get('reconciliation', {}).get('attempted')} attempted / "
          f"{manifest.get('reconciliation', {}).get('terminal')} terminal")
    print(f"mock: model_version rows : {len(mock_rows)}")
    print(f"acquisition_basis        : {json.dumps(basis, sort_keys=True)}")
    print(f"  missing rows           : {basis.get('missing', 0)}")
    print(f"budget                   : {json.dumps(manifest.get('budget', {}).get('estimated_judge_cost_usd'))}")
    for reason in manifest.get("incomplete_reasons", [])[:20]:
        print(f"  incomplete_reason: {reason}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--endpoint", default=os.environ.get("VAPI_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=Path(os.environ.get("ARE_FULL_SCAN_EVIDENCE_DIR", REPO_ROOT / "artifacts" / "BEAAA-29082" / "evidence")),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "artifacts" / "BEAAA-29082" / "manifest.json",
    )
    parser.add_argument(
        "--job-store",
        type=Path,
        default=DEFAULT_JOB_STORE,
        help="sqlite job store backing resumable full-scan jobs",
    )
    parser.add_argument(
        "--job-id",
        default=None,
        help="resume this job id instead of submitting a new job",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="deprecated; exact-fingerprint resume is automatic",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="start a new job even when an exact-fingerprint checkpoint exists",
    )
    args = parser.parse_args()

    if args.preflight:
        return preflight(args.endpoint, args.evidence_root)
    if args.run:
        return run_scan(
            args.endpoint,
            args.evidence_root,
            args.seed,
            args.out,
            job_store_path=args.job_store,
            job_id=args.job_id,
            resume=args.resume,
            fresh=args.fresh,
        )
    parser.error("choose --preflight or --run")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
