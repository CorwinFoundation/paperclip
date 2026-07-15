#!/usr/bin/env python3
"""Verify the deployed real-agent demo; optionally execute all 203 cases."""
from __future__ import annotations

import argparse
import json
import time
from urllib import request


def _json(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    timeout: int = 20,
) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Demo-Access-Token"] = token
    req = request.Request(
        url,
        data=b"{}" if method == "POST" else None,
        method=method,
        headers=headers,
    )
    with request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def verify_health(base_url: str, *, allow_unconfigured: bool = False) -> dict:
    health = _json(base_url.rstrip("/") + "/api/health")
    if health.get("status") != "ok":
        raise RuntimeError(f"unhealthy deployment: {health}")
    if health.get("mode") != "real-agent-203-case-scan" or health.get("synthetic") is not False:
        raise RuntimeError("deployment is not the real-agent scanner")
    if health.get("runnable_case_count") != 203:
        raise RuntimeError("deployed target-bound execution plan is not exactly 203 cases")
    if health.get("planned_target_calls") != 246 or health.get("planned_judge_calls") != 203:
        raise RuntimeError("deployed call plan is not 246 target / 203 judge")
    if health.get("max_judge_cost_usd") != 100.0:
        raise RuntimeError("deployed judge ceiling is not $100")
    if not allow_unconfigured and health.get("judge_configured") is not True:
        raise RuntimeError("deployed real judge is not configured")
    return health


def execute(base_url: str, token: str, timeout_seconds: int) -> dict:
    base = base_url.rstrip("/")
    started = _json(base + "/api/runs", method="POST", token=token)
    run_id = started["run_id"]
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        snapshot = _json(base + f"/api/runs/{run_id}")
        if snapshot["status"] == "failed":
            raise RuntimeError(snapshot.get("error") or "real scan failed")
        if snapshot["status"] == "complete":
            scan = snapshot["result"]["scan"]
            counts = scan["counts"]
            reconciliation = scan["reconciliation"]
            if not (
                counts["expected"]
                == counts["evidence_rows"]
                == reconciliation["attempted"]
                == reconciliation["terminal"]
                == 203
            ):
                raise RuntimeError("203-case reconciliation failed")
            if scan["budget"]["estimated_judge_cost_usd"] > 100.0:
                raise RuntimeError("approved judge budget exceeded")
            if scan["budget"]["target_calls"] != 246:
                raise RuntimeError("real target did not receive 246 planned calls")
            if scan["budget"]["judge_calls"] > 203:
                raise RuntimeError("real judge exceeded the 203-call plan")
            return {
                "run_id": run_id,
                "analysis_status": scan["analysis_status"],
                "verdict_counts": scan["verdict_counts"],
                "budget": scan["budget"],
                "manifest_hash": scan["manifest_hash"],
            }
        time.sleep(1)
    raise TimeoutError(f"real scan {run_id} exceeded {timeout_seconds}s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--token")
    parser.add_argument("--health-only", action="store_true")
    parser.add_argument("--allow-unconfigured", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--timeout", type=int, default=28800)
    args = parser.parse_args()
    result: dict = {
        "health": verify_health(args.url, allow_unconfigured=args.allow_unconfigured)
    }
    if args.execute:
        if not args.token:
            raise SystemExit("--execute requires --token")
        result["scan"] = execute(args.url, args.token, args.timeout)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
