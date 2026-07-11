#!/usr/bin/env python3
"""Exercise a deployed demo through its public HTTP contract."""

from __future__ import annotations

import argparse
import json
import time
from urllib import error, request


def _json(url: str, *, method: str = "GET", timeout: int = 10) -> dict:
    payload = b"{}" if method == "POST" else None
    req = request.Request(url, data=payload, method=method, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=timeout) as response:
        if response.status not in {200, 202}:
            raise RuntimeError(f"unexpected HTTP {response.status}: {url}")
        return json.load(response)


def _text(url: str, *, timeout: int = 20) -> str:
    with request.urlopen(url, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected HTTP {response.status}: {url}")
        return response.read().decode("utf-8")

def run(base_url: str, timeout_seconds: int) -> dict:
    base = base_url.rstrip("/")
    health = _json(base + "/api/health")
    if health.get("status") != "ok" or health.get("production_contacted") is not False:
        raise RuntimeError(f"unhealthy or unsafe deployment: {health}")
    started = _json(base + "/api/runs", method="POST")
    run_id = started["run_id"]
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        snapshot = _json(base + f"/api/runs/{run_id}", timeout=20)
        if snapshot["status"] == "failed":
            raise RuntimeError(snapshot.get("error") or "deployed proof run failed")
        if snapshot["status"] == "passed":
            result = snapshot["result"]
            report = result["report"]
            if report["status"] != "PASS" or set(report["perils_detected"]) != {"P1", "P2", "P3", "P4", "P5", "P6"}:
                raise RuntimeError("deployed proof result did not meet the acceptance contract")
            if result["tests"]["status"] != "passed" or len(result["tracker_rows"]) != 7:
                raise RuntimeError("deployed test or persistence gate failed")
            lifecycle = result["lifecycle"]
            if lifecycle["initial_scan"]["score"] != 80 or lifecycle["updated_scan"]["score"] != 81:
                raise RuntimeError("scanner lifecycle scores did not match the release proof")
            if lifecycle["bind"]["admittance_class"] != "CONSTRAINED_BIND" or lifecycle["bind"]["real_policy_bound"] is not False:
                raise RuntimeError("demonstration bind boundary failed")
            if lifecycle["installation"]["status"] != "INSTALLED" or lifecycle["ci_gate"]["exit_code"] != 0:
                raise RuntimeError("install or CI gate did not pass")
            if lifecycle["certificate"]["signature_status"] != "valid":
                raise RuntimeError("certificate signature was not valid")
            certificate_html = _text(base + lifecycle["documents"]["certificate_url"])
            report_html = _text(base + lifecycle["documents"]["report_url"])
            if "Certificate of BackBond Scan" not in certificate_html or "BackBond Scanner Report" not in report_html:
                raise RuntimeError("certificate or report document could not be viewed")
            if "ARE Spot-Scan" in report_html or "ARE Insurance" in report_html:
                raise RuntimeError("report document contains stale ARE naming")
            if "BOUND_DEMO and bind-threshold signals" not in report_html or "not evidence of bound insurance coverage" not in report_html:
                raise RuntimeError("report document is missing the synthetic no-coverage boundary")
            return {
                "run_id": run_id,
                "status": "PASS",
                "events": snapshot["event_count"],
                "elapsed_ms": result["elapsed_ms"],
                "initial_score": lifecycle["initial_scan"]["score"],
                "updated_score": lifecycle["updated_scan"]["score"],
                "production_logs": len(result["raw_events"]),
                "passive_findings": len(result["detections"]),
            }
        time.sleep(0.5)
    raise TimeoutError(f"demo run {run_id} did not finish within {timeout_seconds}s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.url, args.timeout), sort_keys=True))
    except (error.URLError, RuntimeError, TimeoutError) as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
