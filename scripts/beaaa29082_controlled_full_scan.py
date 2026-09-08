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
    dispatch_registry_attempt,
    registry_cases_from_rows,
)
from tools.registered_case_executor import (  # noqa: E402
    ScanBudget,
    build_registered_case_workers,
    default_judge_runner,
)

VAPI_ASSISTANT_ID = "09aea361-04b3-4a62-bcb0-2f2582f11ec8"
DEFAULT_ENDPOINT = "https://api.vapi.ai/chat"
EXECUTION_FLAG = "production_runner_executable"


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


def run_scan(endpoint: str, evidence_root: Path, seed: int, out: Path) -> int:
    started = time.time()
    rows = load_registry_rows()
    active = executable_rows(rows)
    cases = registry_cases_from_rows(active, execution_flag=EXECUTION_FLAG)
    print(f"[run] executable cases: {len(cases)}", flush=True)

    workers = build_workers(endpoint, evidence_root)
    sample = next(iter(workers.values()))
    model_versions = sample.manifest_metadata()

    store = FullScanJobStore()
    job = store.submit(
        agent_id=VAPI_ASSISTANT_ID,
        scanner_version=os.environ.get("ARE_SCANNER_VERSION", "1.3.0"),
        corpus_version=os.environ.get("ARE_CORPUS_VERSION", "1.3.0"),
        model_versions=model_versions,
        seed=seed,
        registry_cases=cases,
    )
    print(f"[run] job_id: {job.job_id}", flush=True)

    lease = store.lease(job.job_id, ttl_seconds=6 * 3600)
    attempt_id = lease["attempt_id"]
    print(f"[run] attempt_id: {attempt_id}", flush=True)

    job = dispatch_registry_attempt(
        store,
        job.job_id,
        attempt_id=attempt_id,
        registry_rows=active,
        workers=workers,
        execution_flag=EXECUTION_FLAG,
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
    args = parser.parse_args()

    if args.preflight:
        return preflight(args.endpoint, args.evidence_root)
    if args.run:
        return run_scan(args.endpoint, args.evidence_root, args.seed, args.out)
    parser.error("choose --preflight or --run")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
