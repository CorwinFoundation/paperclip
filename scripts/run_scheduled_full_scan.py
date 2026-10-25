#!/usr/bin/env python3
"""Run one controlled scheduled full scan and write an operator manifest."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from tools.registered_case_executor import parse_scan_cost_limit

EVIDENCE_LABEL = "scheduled-controlled-evidence"
INCOMPLETE_REASONS = (
    "dispatch_failure",
    "transport",
    "format",
    "missing_judge",
    "unavailable_capability",
)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"required environment variable is missing: {name}")
    return value


def _request_json(url: str, *, method: str = "GET", payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {_required('SCAN_API_TOKEN')}")
    request.add_header("X-Tenant-Id", _required("SCAN_TENANT_ID"))
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise RuntimeError("scanner returned a non-object response")
    return result


def summarize(job: str, response: Mapping[str, Any], *, started: float, estimated_cost_usd: float) -> dict[str, Any]:
    suite = response.get("full_suite_manifest") or response.get("manifest") or {}
    rows = suite.get("evidence_rows", []) if isinstance(suite, Mapping) else []
    family_counts: dict[str, Counter[str]] = {}
    incomplete = Counter({reason: 0 for reason in INCOMPLETE_REASONS})
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, Mapping):
            continue
        family = str(raw.get("family") or raw.get("test_id") or "unknown")
        counts = family_counts.setdefault(family, Counter())
        counts["considered"] += 1
        if raw.get("routed") is not False:
            counts["routed"] += 1
        if raw.get("target_called") or raw.get("target_invoked"):
            counts["target_called"] += 1
        verdict = str(raw.get("verdict") or raw.get("status") or "").upper()
        if verdict == "PASS":
            counts["pass"] += 1
        elif verdict == "FAIL":
            counts["fail"] += 1
        else:
            counts["incomplete"] += 1
            target_called = bool(raw.get("target_called") or raw.get("target_invoked"))
            reason = str(raw.get("incomplete_reason") or raw.get("reason") or "format").lower()
            matched = (
                "dispatch_failure"
                if not target_called
                else next((item for item in INCOMPLETE_REASONS if item in reason), "format")
            )
            incomplete[matched] += 1
    return {
        "schema": "backbond.scheduled-scan-manifest/v1",
        "job": job,
        "evidence_label": EVIDENCE_LABEL,
        "live_service_signed": False,
        "scan_id": response.get("scan_id"),
        "status": response.get("status"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": round(time.monotonic() - started, 3),
        "estimated_cost_usd": round(estimated_cost_usd, 6),
        "families": {name: dict(sorted(counts.items())) for name, counts in sorted(family_counts.items())},
        "incomplete_by_reason": dict(incomplete),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", choices=("vapi", "zoo"), required=True)
    args = parser.parse_args()
    prefix = args.job.upper()
    concurrency = int(os.environ.get("SCAN_CONCURRENCY", "1"))
    rpm = int(os.environ.get("SCAN_RPM", "30"))
    retries = int(os.environ.get("SCAN_RETRIES", "0"))
    cap = parse_scan_cost_limit(os.environ.get("SCAN_COST_CAP_USD", "unlimited"))
    if (concurrency, retries) != (1, 0) or rpm > 30 :
        raise SystemExit("scheduled scan caps exceeded")
    started = time.monotonic()
    payload = {
        "tenant_id": _required("SCAN_TENANT_ID"),
        "agent_endpoint": _required(f"{prefix}_AGENT_ENDPOINT"),
        "credential_ref": _required(f"{prefix}_CREDENTIAL_REF"),
        "credential_secret": _required(f"{prefix}_CREDENTIAL_SECRET"),
        "corpus_snapshot_hash": _required("SCAN_CORPUS_SNAPSHOT_HASH"),
        "scanner_version": _required("SCAN_SCANNER_VERSION"),
        "corpus_version": _required("SCAN_CORPUS_VERSION"),
        "seed": int(os.environ.get("SCAN_SEED", "0")),
        "scan_tier": "full",
        "vendor_id": args.job,
        "scanner_invocation_context": {"purpose": "weekly_scheduled_scan", "evidence_label": EVIDENCE_LABEL},
    }
    base = _required("SCAN_API_BASE_URL").rstrip("/")
    response = _request_json(f"{base}/scanner/v1/scans", method="POST", payload=payload)
    scan_id = response.get("scan_id")
    while response.get("status") == "accepted":
        if time.monotonic() - started > int(os.environ.get("SCAN_TIMEOUT_SECONDS", "21600")):
            raise TimeoutError(f"scan {scan_id} did not finish before timeout")
        time.sleep(10)
        response = _request_json(f"{base}/scan/{scan_id}?audience=operator")
    estimated = float(response.get("estimated_cost_usd") or 0)
    if cap is not None and estimated > cap:
        raise RuntimeError(f"reported estimated cost {estimated} exceeds cap {cap}")
    target = Path(_required("SCAN_MANIFEST_ROOT")) / datetime.now(timezone.utc).date().isoformat() / args.job
    target.mkdir(parents=True, exist_ok=True)
    manifest = summarize(args.job, response, started=started, estimated_cost_usd=estimated)
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(target / "manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
