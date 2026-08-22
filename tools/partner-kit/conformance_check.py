#!/usr/bin/env python3
"""conformance_check.py — BackBond design-partner endpoint self-test.

Ships in the design-partner connection pack. A partner runs it against their
staging agent endpoint before a scan; six PASSes means the endpoint satisfies
the scanner API contract (are-scanner-api-v1) and is scan-ready. Implements the
six required checks documented in the Design Partner Quick Start, section 03:

  1. reachable (authorized POST)   -> authenticated POST returns HTTP 200
  2. valid JSON body               -> body parses as a JSON object
  3. has string 'response' field   -> object has a string field named "response"
  4. response within 256 KiB       -> body is <= 256 KiB
  5. rejects missing auth          -> a request with no bearer gets 401 or 403
  6. handles malformed body        -> a non-JSON body does not 5xx

Usage:
  AGENT_BEARER_SECRET=... python3 conformance_check.py \
      --url https://agent.staging.example.com/scan

Exit code 0 iff all six required checks pass. Standard library only.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 256 * 1024
TIMEOUT = 30


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Reject redirects so credentials can never reach a second origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def _validate_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("endpoint URL must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("endpoint URL must not contain user information")
    if parsed.fragment:
        raise ValueError("endpoint URL must not contain a fragment")


def _read_limited(resp) -> tuple[bytes, bool]:
    """Stream at most MAX_BYTES + 1; never buffer an unbounded response."""
    chunks = []
    remaining = MAX_BYTES + 1
    while remaining:
        chunk = resp.read(min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    return raw[:MAX_BYTES], len(raw) > MAX_BYTES


def _post(url, body: bytes, bearer: str | None):
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept-Encoding", "identity")
    if bearer is not None:
        req.add_header("Authorization", f"Bearer {bearer}")
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            raw, oversized = _read_limited(resp)
            return resp.getcode(), raw, oversized
    except urllib.error.HTTPError as e:
        raw, oversized = _read_limited(e)
        return e.code, raw, oversized
    except Exception:  # connection/TLS/timeout; never reflect secret-bearing errors
        return None, b"", False


def run(url: str, secret: str):
    _validate_url(url)
    results = []  # (label, passed, detail)

    # A single authorized probe reused by checks 1-4.
    code, raw, oversized = _post(
        url,
        json.dumps({"prompt": "Summarize the safe action "
                   "you would take in this situation."}).encode(),
        secret,
    )

    results.append(("reachable (authorized POST)", code == 200, f"HTTP {code}"))

    body = None
    try:
        body = json.loads(raw)
        valid_json = isinstance(body, dict)
    except Exception:
        valid_json = False
    results.append(("valid JSON body", valid_json,
                    "object" if valid_json else "not a JSON object"))

    has_response = isinstance(body, dict) and isinstance(body.get("response"), str)
    results.append(("has string 'response' field", has_response,
                    "present" if has_response else "missing/not a string"))

    within_cap = not oversized
    results.append(("response within 256 KiB", within_cap,
                    "over limit" if oversized else f"{len(raw)} bytes"))

    # 5. rejects missing auth
    code_noauth, _, _ = _post(url, json.dumps({"prompt": "hello"}).encode(), None)
    rejects_missing = code_noauth in (401, 403)
    results.append(("rejects missing auth", rejects_missing, f"HTTP {code_noauth}"))

    # 6. handles malformed body (must not 5xx)
    code_bad, _, _ = _post(url, b"{ this is : not json ", secret)
    handles_bad = code_bad is not None and code_bad < 500
    results.append(("handles malformed body", handles_bad, f"HTTP {code_bad}"))

    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument(
        "--secret-env",
        default="AGENT_BEARER_SECRET",
        metavar="NAME",
        help="environment variable containing the bearer secret (default: AGENT_BEARER_SECRET)",
    )
    args = ap.parse_args()

    secret = os.environ.get(args.secret_env)
    if not secret:
        print("# FAIL: bearer secret environment variable is missing or empty.", file=sys.stderr)
        return 2
    try:
        results = run(args.url, secret)
    except ValueError as exc:
        print(f"# FAIL: {exc}.", file=sys.stderr)
        return 2
    width = max(len(label) for label, _, _ in results)
    all_pass = True
    for label, passed, detail in results:
        mark = "PASS" if passed else "FAIL"
        all_pass = all_pass and passed
        print(f"# {label.ljust(width)}  {mark}   ({detail})")
    if all_pass:
        print("# All required checks passed — endpoint is scan-ready.")
        return 0
    print("# One or more required checks FAILED — endpoint is not scan-ready.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
