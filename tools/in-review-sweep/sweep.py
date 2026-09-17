#!/usr/bin/env python3
"""In-Review Aging Sweep (BEAAA-2713 Pillar D / routine BEAAA-31939).

Enumerates in_review issues, age-buckets on updatedAt, pings the reviewer in the
24-72h band (24h dedup), and escalates >72h on BEAAA-2713 with an @CEO mention.
Escalation is comment-only: this tool never PATCHes issue status.

Rebuilt 2026-09-11 under BEAAA-31940 after the original v0.2 source was lost from
disk. Canonical copy lives in the are-scanner repo at tools/in-review-sweep/sweep.py;
the host copy under workspaces/<uuid>/tools/in-review-sweep/ is an install, not the
source of truth. Restore with:

    git show origin/master:tools/in-review-sweep/sweep.py > sweep.py

Usage:
    python3 sweep.py                 # live sweep
    python3 sweep.py --dry-run       # resolve + bucket + print, post nothing
    python3 sweep.py --seed          # record current band members as pinged, post nothing
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

SUMMARY_ISSUE_ID = "d41dd535-0d1a-4e2a-be20-21e3b52b7f9c"  # BEAAA-2713
SUMMARY_ISSUE_KEY = "BEAAA-2713"
CEO_AGENT_ID = "ca0edebc-6f54-4a19-b836-4d306e3a3c48"

FRESH_HOURS = 24
STALE_HOURS = 72
PING_DEDUP_HOURS = 24
ESCALATION_SAMPLE = 10

STATE_VERSION = 1
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STATE = os.path.join(HERE, "state.json")

MENTION_RE = re.compile(r"agent://([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


# ---------------------------------------------------------------- API helpers

class Api:
    def __init__(self, base, key, company):
        self.base = base.rstrip("/")
        self.key = key
        self.company = company
        self.forbidden = []

    def _call(self, method, path, body=None):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.key)
        if data:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
        return json.loads(raw) if raw else None

    def get(self, path):
        return self._call("GET", path)

    def post(self, path, body):
        return self._call("POST", path, body)

    def list_in_review(self):
        """Issue lists are company-scoped in the path. A bare /api/issues?companyId=
        returns 400, and /api/companies/{id}/issues/{BEAAA-*} 404s on the identifier,
        so everything here works from the list rows and their UUIDs."""
        out, offset, page = [], 0, 200
        while True:
            rows = self.get(
                "/api/companies/%s/issues?status=in_review&limit=%d&offset=%d"
                % (self.company, page, offset)
            ) or []
            out.extend(rows)
            if len(rows) < page:
                return out
            offset += page

    def comments(self, issue_id, limit=25):
        try:
            rows = self.get("/api/issues/%s/comments?limit=%d" % (issue_id, limit)) or []
        except urllib.error.HTTPError:
            return []
        return rows if isinstance(rows, list) else rows.get("comments", [])

    def interactions(self, issue_id):
        try:
            rows = self.get("/api/issues/%s/interactions" % issue_id) or []
        except urllib.error.HTTPError:
            return []
        if isinstance(rows, dict):
            rows = rows.get("interactions") or rows.get("data") or []
        return rows if isinstance(rows, list) else []

    def comment(self, issue_id, body, issue_key):
        """Cross-agent POSTs that 403 are aggregated into the BEAAA-2713 summary so
        the @mention still lands."""
        try:
            self.post("/api/issues/%s/comments" % issue_id, {"body": body})
            return True
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                self.forbidden.append(issue_key)
                return False
            raise


# -------------------------------------------------------------------- helpers

def parse_ts(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def age_hours(value, now):
    ts = parse_ts(value)
    return None if ts is None else (now - ts).total_seconds() / 3600.0


def load_state(path):
    try:
        with open(path) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    state.setdefault("version", STATE_VERSION)
    state.setdefault("actions", {})
    return state


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def recently_pinged(state, issue_id, now):
    stamp = parse_ts(state["actions"].get("ping:" + issue_id))
    return stamp is not None and (now - stamp) < timedelta(hours=PING_DEDUP_HOURS)


def resolve_reviewer(api, issue, names):
    """v0.2 rule: the reviewer is usually the assignee, but an assignee who parked
    the issue by @-mentioning someone else is waiting on that someone. Follow the
    mention. A board/user assignee is nobody an agent can ping -> CEO triage."""
    if issue.get("assigneeUserId") and not issue.get("assigneeAgentId"):
        return None, "board"
    assignee = issue.get("assigneeAgentId")
    if not assignee:
        return None, "unassigned"
    for row in api.comments(issue["id"])[-3:][::-1]:
        if row.get("authorAgentId") != assignee:
            continue
        targets = [m for m in MENTION_RE.findall(row.get("content") or row.get("body") or "")
                   if m != assignee and m in names]
        if targets:
            return targets[-1], "mention"
    return assignee, "assignee"


def held_by_card(api, issue_id):
    """A row holding a pending interaction already has a reviewer path: the card.
    Pinging it is worse than silence - the pinged seat answers in the thread, that
    reply lands as user auth, and a default-flag card expires on it. Age the card,
    not the status."""
    return any((i.get("status") or "").lower() == "pending" for i in api.interactions(issue_id))


def mention(agent_id, names):
    return "[@%s](agent://%s)" % (names.get(agent_id, "agent"), agent_id)


def link(key):
    return "[%s](/BEAAA/issues/%s)" % (key, key)


# ----------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="resolve and bucket, post nothing")
    ap.add_argument("--seed", action="store_true",
                    help="record the current 24-72h band as already pinged, post nothing")
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--no-summary", action="store_true")
    args = ap.parse_args()

    key = os.environ.get("PAPERCLIP_API_KEY")
    if not key:
        # An unauthenticated POST does not 401 here, it posts as `user local-board`.
        sys.exit("PAPERCLIP_API_KEY is not set - refusing to run (writes would post as local-board)")
    api = Api(os.environ.get("PAPERCLIP_API_URL", "http://127.0.0.1:3100"), key,
              os.environ.get("PAPERCLIP_COMPANY_ID"))

    roster = api.get("/api/companies/%s/agents" % api.company) or []
    names = {a["id"]: a.get("name") or a.get("title") or "agent" for a in roster}
    names.setdefault(CEO_AGENT_ID, "CEO")

    now = datetime.now(timezone.utc)
    state = load_state(args.state)
    issues = api.list_in_review()

    fresh, band, stale, triage = [], [], [], []
    for issue in issues:
        hours = age_hours(issue.get("updatedAt"), now)
        if hours is None:
            continue
        reviewer, how = resolve_reviewer(api, issue, names) if hours >= FRESH_HOURS else (None, "fresh")
        row = (issue, hours, reviewer, how)
        if hours < FRESH_HOURS:
            fresh.append(row)
        elif hours <= STALE_HOURS:
            (band if reviewer else triage).append(row)
        else:
            stale.append(row)
            if not reviewer:
                triage.append(row)

    pinged = 0
    held = []
    for issue, hours, reviewer, _ in band:
        if recently_pinged(state, issue["id"], now):
            continue
        if held_by_card(api, issue["id"]):
            held.append((issue, hours))
            continue
        body = (
            "%s - this issue has been `in_review` for %dh with no status change. "
            "If the review is done, move it to `done` or back to `in_progress`. "
            "If you are not the reviewer, re-assign or say who is.\n\n"
            "Posted by the In-Review Aging Sweep (%s)."
            % (mention(reviewer, names), round(hours), link(SUMMARY_ISSUE_KEY))
        )
        if args.dry_run or args.seed:
            pinged += 1
        elif api.comment(issue["id"], body, issue["identifier"]):
            pinged += 1
        state["actions"]["ping:" + issue["id"]] = now.isoformat().replace("+00:00", "Z")

    summary = "Swept %d in_review. Pinged: %d. Escalated: %d. Unassigned-for-CEO-triage: %d.%s" % (
        len(issues), pinged, len(stale), len(triage),
        (" Held (pending board card): %d." % len(held)) if held else "")
    lines = [summary, "",
             "Sweep at %s UTC." % now.strftime("%Y-%m-%d %H:%M"), "",
             "Bucket: fresh<%dh=%d, ping%d-%dh=%d, escalate>%dh=%d."
             % (FRESH_HOURS, len(fresh), FRESH_HOURS, STALE_HOURS, len(band), STALE_HOURS, len(stale))]
    if held:
        lines += ["", "Not pinged - a pending board card is already the reviewer path:"]
        for issue, hours in sorted(held, key=lambda r: -r[1]):
            lines.append("- %s (%dh, pending interaction)" % (link(issue["identifier"]), round(hours)))
    if stale:
        lines += ["", "%s escalation: %d `in_review` issues >%dh need a decision "
                      "(re-assign / return / cancel / done). Sample:"
                  % (mention(CEO_AGENT_ID, names), len(stale), STALE_HOURS)]
        for issue, hours, reviewer, _ in sorted(stale, key=lambda r: -r[1])[:ESCALATION_SAMPLE]:
            lines.append("- %s (%dh, owner=%s)" % (
                link(issue["identifier"]), round(hours),
                names.get(reviewer, "unassigned") if reviewer else "unassigned"))
    if api.forbidden:
        lines += ["", "Ping POST returned 403 on %d issue(s), so the nudge lands here instead: %s"
                  % (len(api.forbidden), ", ".join(link(k) for k in api.forbidden))]

    body = "\n".join(lines)
    if args.dry_run or args.seed:
        print(body)
        print("\n[%s] nothing posted." % ("seed" if args.seed else "dry-run"))
    else:
        if not args.no_summary:
            api.comment(SUMMARY_ISSUE_ID, body, SUMMARY_ISSUE_KEY)
        print(summary)

    if not args.dry_run:
        save_state(args.state, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
