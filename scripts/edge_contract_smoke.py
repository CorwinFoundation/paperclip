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

# nginx serves its own error page for a path it does not route; FastAPI never
# emits this string. Observed live on scanner.backbond.ai 2026-09-02.
EDGE_404_MARKER = "404 Not Found"


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


def _is_app_response(text: str) -> bool:
    """True when the body looks like a Scanner (FastAPI) JSON response.

    The edge answers an unrouted path with an nginx HTML error page; the app
    answers a routed path with a JSON object, whatever the status. That is the
    discriminator between "absent" and "routed but currently refusing".
    """
    try:
        payload = json.loads(text)
    except ValueError:
        return False
    return isinstance(payload, dict)


def check_absent(base: str, entry: dict) -> tuple[bool, str]:
    """Assert a path is absent from the edge, not merely unhelpful.

    A 2xx is a FAIL. So is any application response -- 405, 401, or a JSON 404 --
    because it proves the path is routed to the app even though no decision was
    recorded to publish it (BEAAA-28632 F-1).
    """
    method, path = entry["method"], entry["path"]
    status, text = _request(method, base + path, {}, None)
    problems = []
    if 200 <= status < 300:
        problems.append(f"status {status} is 2xx; the surface is served")
    elif status != 404:
        problems.append(f"status {status} != 404; the path is answered, not absent")
    if _is_app_response(text):
        problems.append("body is an application JSON response; the path is routed to the app")
    if EDGE_404_MARKER not in text:
        problems.append(f"body missing edge 404 marker {EDGE_404_MARKER!r}")
    return (not problems), f"HTTP {status}" + ("; " + "; ".join(problems) if problems else "")


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
        if not isinstance(entry, dict):
            print(
                f"FAIL must-not-serve             {entry!r} -- entry must be a mapping with "
                "id, method and path; the bare-string form probed a method the route "
                "never declared (BEAAA-28632 F-1)"
            )
            failures += 1
            continue
        ok, detail = check_absent(base, entry)
        failures += 0 if ok else 1
        label = f"absent:{entry['id']}"
        print(f"{'PASS' if ok else 'FAIL'} {label:<24} {entry['method']:<5} {entry['path']} -- {detail}")

    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
