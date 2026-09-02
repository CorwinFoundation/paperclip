#!/usr/bin/env python3
"""Edge contract smoke for production-train read-backs (BEAAA-28631).

Reads the pinned contract file and reports PASS/FAIL per route against the live
public edge. Read-only: every request is either a GET or an unauthenticated POST
that the edge is expected to reject. Exit 0 only when every route passes.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

import yaml


def _request(method: str, url: str, headers: dict, body) -> tuple[int, str]:
    data = None
    if body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def _status_class(status: int) -> str:
    return f"{status // 100}xx"


def check_route(base: str, route: dict) -> tuple[bool, str]:
    expect = route.get("expect", {})
    request = route.get("request", {}) or {}
    status, text = _request(
        route["method"], base + route["path"], request.get("headers") or {}, request.get("body")
    )
    problems = []
    if _status_class(status) != expect.get("status_class"):
        problems.append(f"status class {_status_class(status)} != {expect.get('status_class')}")
    if "status" in expect and status != expect["status"]:
        problems.append(f"status {status} != {expect['status']}")
    if expect.get("body_contains") and expect["body_contains"] not in text:
        problems.append(f"body missing {expect['body_contains']!r}")
    for key in expect.get("keys") or []:
        try:
            payload = json.loads(text)
        except ValueError:
            problems.append("body is not JSON")
            break
        if not isinstance(payload, dict) or key not in payload:
            problems.append(f"missing key {key}")
    return (not problems), f"HTTP {status}" + ("; " + "; ".join(problems) if problems else "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("contract", help="path to docs/deploy/edge-contract.yaml")
    args = parser.parse_args()

    with open(args.contract, encoding="utf-8") as handle:
        contract = yaml.safe_load(handle)
    base = f"{contract['scheme']}://{contract['edge_host']}"

    failures = 0
    for route in contract["routes"]:
        ok, detail = check_route(base, route)
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'} {route['id']:<24} {route['method']:<5} {route['path']} -- {detail}")

    for entry in contract.get("must_not_serve") or []:
        method, path = entry.split(None, 1)
        status, _ = _request(method, base + path, {}, None)
        served = 200 <= status < 300
        failures += 1 if served else 0
        print(f"{'FAIL' if served else 'PASS'} must-not-serve{'':<10} {method:<5} {path} -- HTTP {status}")

    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
