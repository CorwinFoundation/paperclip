#!/usr/bin/env python3
"""Run one paid Full Scan pack end to end (BEAAA-38957).

Grants a ``manual`` entitlement, accepts the job (idempotent on ``--run-id``),
runs every case to a terminal state under the hard ``--max-cost-usd`` cap, and
writes ``manifest.json``, ``rows.json`` and ``details.jsonl`` under ``--out``.

The target is the ``--model`` behind the OpenRouter adapter, given each case's
own system prompt (the case defines the deployment). The judge is the pinned
Starter judge model (``scanners.starter_judge.JUDGE_MODEL``). Both read
``OPENROUTER_API_KEY`` from the environment; the key is never printed. The
manifest is sealed with ``ARE_BUNDLE_SIGNING_KEY`` (or the dev key when
``ARE_ALLOW_DEV_SIGNING_KEY=true``); the script refuses to start without one.

Re-running with the same ``--run-id`` and ``--db`` resumes a killed run: the
grant and the acceptance replay, finished cases are not run again.

Example::

    ARE_ALLOW_DEV_SIGNING_KEY=true python3 scripts/run_full_scan_pack.py --pack full-scan-conv-en@0.1 \\
        --adapter openrouter --model openai/gpt-4.1-mini --max-cost-usd 3 \\
        --tenant backbond-internal --target-id ref-openrouter-gpt-4.1-mini \\
        --db artifacts/beaaa-38957/jobs.sqlite3 --out artifacts/beaaa-38957/conv-en-0.1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.config import ApiConfig  # noqa: E402
from api.full_scan_entitlement import FullScanEntitlementStore  # noqa: E402
from api.full_scan_executor import FullScanJobStore  # noqa: E402
from api.full_scan_job_persistence import FullScanJobPersistence  # noqa: E402
from api.full_scan_packs import (  # noqa: E402
    PAID_PACKS,
    ConversationCaseRunner,
    OpenRouterChatClient,
    run_pack_attempt,
)
from scanners.starter_judge import JUDGE_MODEL  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pack", required=True, choices=sorted(PAID_PACKS))
    parser.add_argument("--adapter", default="openrouter", choices=("openrouter",))
    parser.add_argument("--model", required=True, help="target model behind the adapter")
    parser.add_argument("--judge-model", default=JUDGE_MODEL)
    parser.add_argument("--max-cost-usd", type=float, required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--db", required=True, help="SQLite file for jobs + entitlements")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument(
        "--run-id",
        default=None,
        help="names the grant and the acceptance; reuse it to resume a killed run",
    )
    parser.add_argument("--lease-ttl-seconds", type=int, default=6 * 3600)
    args = parser.parse_args(argv)

    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 2
    try:
        # The manifest is HMAC-sealed at finalize; fail before spending money.
        ApiConfig.from_env().bundle_signing_key(b"")
    except ValueError:
        print(
            "set ARE_BUNDLE_SIGNING_KEY (or ARE_ALLOW_DEV_SIGNING_KEY=true for an "
            "internal measurement run)",
            file=sys.stderr,
        )
        return 2
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)

    entitlements = FullScanEntitlementStore(args.db)
    store = FullScanJobStore(
        persistence=FullScanJobPersistence(args.db), entitlements=entitlements
    )
    store.recover()
    idempotency_key = f"run:{args.tenant}:{run_id}"
    entitlements.grant(
        tenant_id=args.tenant,
        pack_id=args.pack,
        max_cost_usd=args.max_cost_usd,
        source="manual",
        external_ref=f"manual:{args.tenant}:{run_id}",
    )
    job = store.submit(
        agent_id=args.target_id,
        scanner_version="full-scan-pack-runner/0.1",
        corpus_version=args.pack,
        model_versions={
            "primary": f"{args.adapter}:{args.model}",
            "judge": args.judge_model,
        },
        seed=0,
        idempotency_key=idempotency_key,
        pack_id=args.pack,
        tenant_id=args.tenant,
        target_id=args.target_id,
        max_cost_usd=args.max_cost_usd,
    )

    client = OpenRouterChatClient(key)
    runner = ConversationCaseRunner(
        target=client, target_model=args.model, judge=client, judge_model=args.judge_model
    )
    details_path = out / "details.jsonl"
    started = time.monotonic()

    def on_result(case, result) -> None:
        record = {"case_id": case.case_id, "family_id": case.family_id, **asdict(result)}
        with details_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        print(f"{case.case_id} {result.verdict}", file=sys.stderr, flush=True)

    if job.status == "queued" or job.status == "running":
        lease = store.lease(job.job_id, ttl_seconds=args.lease_ttl_seconds)
        if not lease.get("leased"):
            print(f"job {job.job_id} not leasable: {lease}", file=sys.stderr)
            return 3
        job = run_pack_attempt(
            store, job.job_id, attempt_id=lease["attempt_id"], runner=runner, on_result=on_result
        ).job
    runtime_s = round(time.monotonic() - started, 1)

    manifest = dict(job.final_manifest or {})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (out / "rows.json").write_text(
        json.dumps(manifest.get("evidence_rows", []), indent=2, sort_keys=True) + "\n"
    )
    rec = manifest.get("reconciliation", {})
    cost = manifest.get("budget", {}).get("cost", {})
    summary = {
        "job_id": job.job_id,
        "pack_id": job.pack_id,
        "status": job.status,
        "expected": len(job.registry_cases),
        "attempted": rec.get("attempted"),
        "terminal": rec.get("terminal"),
        "evidenced": rec.get("evidenced"),
        "could_not_run": rec.get("by_status", {}).get("could_not_run", 0),
        "verdict_counts": manifest.get("verdict_counts"),
        "runtime_seconds_this_invocation": runtime_s,
        "max_cost_usd": cost.get("max_cost_usd"),
        "judge_cost_usd": cost.get("observed", {}).get("judge_usd"),
        "target_cost_usd": cost.get("observed", {}).get("target_usd"),
        "estimated_usd": cost.get("estimated"),
        "observed_usd": cost.get("observed"),
        "observed_basis_counts": cost.get("observed_basis_counts"),
        "rows_with_estimated_cost": cost.get("rows_with_estimated_cost"),
        "cost_cap_reached": cost.get("cost_cap_reached"),
        "out": str(out),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
