#!/usr/bin/env python3
"""BEAAA-13362 — post the deploy record back to the tracker automatically.

Runs as the final step of the operator-mediated push path, so every deploy
attempt — pass, fail, or rolled-back — leaves an auditable record on the
tracker: SHA, image digest, acceptance-checks output, Founder approver, and
timestamp. Operator-mediated push / ADR-0057 v2.0.

The record is posted as a first-class comment on the tracker issue via the
Paperclip API (PAPERCLIP_API_URL + PAPERCLIP_API_KEY of a deploy-bot key).
Network/tracker failure here MUST NOT fail the deploy that already succeeded,
so all posting errors are swallowed after being logged (the Actions run URL is
the durable fallback record).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone


def load_acceptance(path: str | None) -> dict:
    if not path or not os.path.exists(path):
        return {"passed": None, "checks": {}, "note": "no acceptance artifact"}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as exc:  # pragma: no cover - defensive
        return {"passed": None, "checks": {}, "note": f"unreadable: {exc}"}


def render(args: argparse.Namespace, acceptance: dict, ts: str) -> str:
    passed = acceptance.get("passed")
    verdict = {True: "PASS", False: "FAIL", None: "UNKNOWN"}[passed]
    rolled_back = args.outcome == "failure" and bool(args.previous)
    lines = [
        f"**Deploy record — {args.sha}** (BEAAA-13362 CD pipeline / ADR-0057)",
        "",
        f"- **outcome:** `{args.outcome}`"
        + ("  → **AUTO-ROLLED BACK** to previous image" if rolled_back else ""),
        f"- **image:** `{args.ref}`",
        f"- **digest:** `{args.digest or 'n/a'}`",
        f"- **previous image:** `{args.previous or 'n/a'}`",
        f"- **approver (Founder gate):** `{args.approver}`",
        f"- **acceptance:** **{verdict}**",
        f"- **timestamp (UTC):** {ts}",
        f"- **run:** {args.run_url}",
        "",
        "| check | status | detail |",
        "| --- | --- | --- |",
    ]
    for name, c in (acceptance.get("checks") or {}).items():
        lines.append(f"| {name} | {c.get('status','?')} | {c.get('detail','')[:80]} |")
    return "\n".join(lines)


def post_comment(issue: str, body: str) -> None:
    api = os.environ.get("PAPERCLIP_API_URL")
    key = os.environ.get("PAPERCLIP_API_KEY")
    if not api or not key:
        print("[deploy-record] PAPERCLIP_API_URL/KEY unset — printing record only", file=sys.stderr)
        print(body)
        return
    url = f"{api.rstrip('/')}/api/issues/{issue}/comments"
    payload = json.dumps({"body": body, "comment": body}).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"[deploy-record] posted ({resp.status}) to {issue}", file=sys.stderr)
    except urllib.error.HTTPError as exc:
        print(f"[deploy-record] post failed HTTP {exc.code}: {exc.read()[:200]!r}", file=sys.stderr)
        print(body)
    except Exception as exc:  # pragma: no cover - never fail the deploy
        print(f"[deploy-record] post failed: {exc}", file=sys.stderr)
        print(body)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sha", required=True)
    p.add_argument("--ref", required=True)
    p.add_argument("--digest", default="")
    p.add_argument("--approver", default="unknown")
    p.add_argument("--outcome", default="unknown")
    p.add_argument("--previous", default="")
    p.add_argument("--acceptance", default="")
    p.add_argument("--run-url", dest="run_url", default="")
    p.add_argument("--issue", default=os.environ.get("TRACKER_ISSUE", "BEAAA-13362"))
    args = p.parse_args()

    ts = datetime.now(timezone.utc).isoformat()
    acceptance = load_acceptance(args.acceptance)
    body = render(args, acceptance, ts)
    post_comment(args.issue, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
