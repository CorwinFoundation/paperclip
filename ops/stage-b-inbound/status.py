#!/usr/bin/env python3
"""Read-only Stage B receipt summary for agent verification (BEAAA-38728, BEAAA-38775).

Usage: status.py <since> <until>   (ISO timestamps, e.g. 2026-10-06T18:30)

Opens every Stage B sqlite file with mode=ro and prints counts, 10-char
sha256 prefixes and timestamps only. It never prints an email, token,
report body, passphrase or any other stored value in clear. The installer
places it at /opt/backbond-stage-b/bin/status.py and lets are-deploy run it
as backbond-stage-b with group adm through sudo:

    sudo -u backbond-stage-b -g adm /opt/backbond-stage-b/bin/status.py <since> <until>

Group adm lets it read the nginx access log and the receiver's journal for
the request log; without it those sections print "unreadable".
"""
import glob
import gzip
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

STATE_DIR = os.environ.get("BACKBOND_STAGE_B_STATE_DIR", "/var/lib/backbond-stage-b")
NGINX_LOGS = os.environ.get("BACKBOND_STAGE_B_NGINX_LOGS", "/var/log/nginx/*access*.log*")
JOURNAL_CMD = os.environ.get("BACKBOND_STAGE_B_JOURNAL_CMD", "journalctl")
ACCESS_LINE = re.compile(r'\[(?P<t>[^\]]+)\] "(?P<m>[A-Z]+) (?P<p>\S+)[^"]*" (?P<s>\d{3}) (?P<b>\d+|-)')
ROUTE_WORD = re.compile(r"^[a-z][a-z-]{0,23}$")
CODE = re.compile(r"^[a-z0-9_.:-]{1,64}$")
SAFE_TASK = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
REF = re.compile(r"^(h:[0-9a-f]{10}|[A-Za-z0-9._-]{1,40})$")
WINDOW_ARG = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}(:\d{2}(:\d{2})?)?)?$")


def h(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:10] if value else None


def ts(value):
    """Return value only if it looks like a timestamp; otherwise its hash."""
    if value is None:
        return None
    text = str(value)
    return text if re.match(r"^\d{4}-\d{2}-\d{2}[T ][0-9:.+Z-]*$", text) else h(text)


def code(value):
    """Return value only if it looks like a lowercase state/reason code; otherwise its hash."""
    if value is None:
        return None
    text = str(value)
    return text if CODE.match(text) else h(text)


def route(path):
    """A request path with every token-shaped segment replaced by its 10-char hash."""
    base, _, query = str(path).partition("?")
    parts = [p if not p or ROUTE_WORD.match(p) else "h:" + h(p) for p in base.split("/")]
    return "/".join(parts) + ("?h:" + h(query) if query else "")


def request_log(since, until):
    """nginx /stage-b-demo/ requests plus the receiver's reject reason codes."""
    seen = 0
    print("== requests nginx /stage-b-demo/")
    for path in sorted(glob.glob(NGINX_LOGS)):
        opener = gzip.open if path.endswith(".gz") else open
        try:
            with opener(path, "rt", errors="replace") as fh:
                for line in fh:
                    m = ACCESS_LINE.search(line)
                    if not m or not m["p"].startswith("/stage-b-demo/"):
                        continue
                    try:
                        when = datetime.strptime(m["t"], "%d/%b/%Y:%H:%M:%S %z").astimezone(timezone.utc)
                    except ValueError:
                        continue
                    stamp = when.strftime("%Y-%m-%dT%H:%M:%S")
                    if since <= stamp <= until:
                        seen += 1
                        print("  req", stamp, m["m"], route(m["p"]), m["s"], m["b"])
        except OSError as exc:
            print(f"  {os.path.basename(path)} unreadable {type(exc).__name__}")
    print("requests in_window", seen)
    print("== rejects journal backbond-stage-b-inbound")
    cmd = [JOURNAL_CMD, "-u", "backbond-stage-b-inbound.service", "--since", since.replace("T", " "),
           "--until", until.replace("T", " "), "--utc", "-o", "short-iso", "--no-pager", "-q"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  journal unreadable {type(exc).__name__}")
        return
    count = 0
    for line in proc.stdout.splitlines():
        m = re.search(r"^(\S+) .*stage_b_reject method=([A-Z]+) route=(\S+) status=(\d{3}) reason=(\S+)$", line)
        if m:
            count += 1
            print("  reject", m[1][:19], m[2], route(m[3]), m[4], code(m[5]))
    print("rejects in_window", count, "journal_rc", proc.returncode)


def norm(value):
    return str(value)[:19].replace(" ", "T")


def main(argv):
    if len(argv) != 3 or not all(WINDOW_ARG.match(a) for a in argv[1:]):
        print("usage: status.py <since> <until>  (YYYY-MM-DD[THH[:MM[:SS]]])", file=sys.stderr)
        return 2
    since, until = argv[1], argv[2]

    def inw(value):
        return bool(value) and since <= norm(value) <= until

    def rows(db, table):
        try:
            return [dict(r) for r in db.execute(f'select * from "{table}"')]
        except sqlite3.Error as exc:
            print(f"  {table} error {type(exc).__name__}")
            return []

    def stamped(row):
        return any(inw(v) for k, v in row.items() if k.endswith("_at"))

    paths = sorted(glob.glob(os.path.join(STATE_DIR, "*.sqlite3")))
    print(f"stage_b_status window {since}..{until} files {len(paths)}")
    for path in paths:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
        print(f"== {os.path.basename(path)}")
        if "onboarding_sessions" in tables:
            rs = rows(db, "onboarding_sessions")
            sel = [r for r in rs if any(inw(r.get(k)) for k in ("created_at", "claimed_at", "revoked_at"))]
            print("onboarding_sessions total", len(rs), "in_window", len(sel))
            for r in sel:
                print("  session", h(r.get("token_digest")), "acct", h(r.get("account_email")),
                      "sender", h(r.get("sender_email")), "grant", h(r.get("grant_id")),
                      "created", ts(r.get("created_at")), "claimed", ts(r.get("claimed_at")),
                      "revoked", ts(r.get("revoked_at")))
        if "instinct_standing_grants" in tables:
            rs = rows(db, "instinct_standing_grants")
            sel = [r for r in rs if inw(r.get("created_at")) or inw(r.get("revoked_at"))]
            print("instinct_standing_grants total", len(rs), "in_window", len(sel))
            for r in sel:
                print("  grant", h(r.get("grant_digest")), "user", h(r.get("user_id")),
                      "sender", h(r.get("sender_email")), "created", ts(r.get("created_at")),
                      "revoked", ts(r.get("revoked_at")), "expires", ts(r.get("expires_at")))
                if r.get("revoked_at"):
                    kept = [x for x in rows(db, "instinct_standing_reports") if x.get("grant_digest") == r.get("grant_digest")]
                    replies = [x for x in rows(db, "instinct_followup_requests") if x.get("grant_digest") == r.get("grant_digest") and x.get("replied_at")]
                    print("    revoked_grant_retained reports", len(kept), "replies", len(replies))
        if "instinct_standing_reports" in tables:
            rs = rows(db, "instinct_standing_reports")
            sel = [r for r in rs if inw(r.get("received_at"))]
            print("instinct_standing_reports total", len(rs), "in_window", len(sel))
            for r in sorted(rs, key=lambda r: str(r.get("received_at"))):
                print("  report", h(r.get("report_id")), "grant", h(r.get("grant_digest")),
                      "dedupe", h(r.get("deduplication_key")), "body", h(r.get("report_json")),
                      "received", ts(r.get("received_at")))
        if "instinct_followup_requests" in tables:
            rs = rows(db, "instinct_followup_requests")
            sel = [r for r in rs if stamped(r)]
            print("instinct_followup_requests total", len(rs), "in_window", len(sel))
            for r in sel:
                print("  followup", h(r.get("request_id")), "grant", h(r.get("grant_digest")),
                      "created", ts(r.get("created_at")), "expires", ts(r.get("expires_at")),
                      "replied", ts(r.get("replied_at")))
        if "history_requests" in tables:
            rs = rows(db, "history_requests")
            sel = [r for r in rs if stamped(r)]
            print("history_requests total", len(rs), "in_window", len(sel))
            for r in sel:
                print("  history_request", h(r.get("token_digest")), "user", h(r.get("user_id")),
                      "state", code(r.get("state")), "failure", code(r.get("failure_reason")),
                      "purpose", code(r.get("purpose")), "consented", ts(r.get("consented_at")),
                      "consumed", ts(r.get("consumed_at")), "expires", ts(r.get("expires_at")))
        if "history_previews" in tables:
            rs = rows(db, "history_previews")
            print("history_previews total", len(rs), "in_window", sum(1 for r in rs if stamped(r)))
            for r in (x for x in rs if stamped(x)):
                # report_json is sealed at rest; v2 intake stores row_count and
                # row_refs (safe synthetic task_ids or hashes) beside it (BEAAA-38867).
                rc = r.get("row_count")
                refs = str(r.get("row_refs") or "").split()
                n = rc
                if not isinstance(rc, int):
                    # Rows stored before the column existed: only a clear-JSON
                    # report can be counted; a sealed one prints "?".
                    try:
                        rpt = json.loads(r.get("report_json") or "")
                        n = len(rpt["rows"])
                        rc = rpt.get("row_count", n)
                        refs = [str(x.get("task_id")) if x.get("synthetic") is True and SAFE_TASK.match(str(x.get("task_id") or ""))
                                else "h:" + h(x.get("task_id")) for x in rpt["rows"][:10] if x.get("task_id")]
                    except (ValueError, TypeError, KeyError, AttributeError):
                        rc = n = "?"
                refs = " ".join(x if REF.match(x) else h(x) for x in refs[:10]) or "none"
                print("  preview", h(r.get("preview_id")), "state", code(r.get("state")),
                      "row_count", rc, "rows", n, "refs", refs, "created", ts(r.get("created_at")))
        if "history_events" in tables:
            rs = rows(db, "history_events")
            sel = [r for r in rs if inw(r.get("occurred_at"))]
            print("history_events total", len(rs), "in_window", len(sel))
            for r in sel:
                print("  history_event", code(r.get("event_type")), "import", h(r.get("import_id")),
                      "occurred", ts(r.get("occurred_at")))
        if "export_intakes" in tables:
            rs = rows(db, "export_intakes")
            sel = [r for r in rs if inw(r.get("received_at"))]
            print("export_intakes total", len(rs), "in_window", len(sel))
            for r in sel:
                refs = " ".join(x if REF.match(x) else h(x) for x in str(r.get("row_refs") or "").split()) or "none"
                print("  export", str(r.get("code_digest"))[:10], "sender", str(r.get("sender_hash"))[:10],
                      "received", ts(r.get("received_at")), "state", code(r.get("state")),
                      "row_count", r.get("row_count"), "refs", refs)
        if "transport_probes" in tables:
            rs = rows(db, "transport_probes")
            sel = [r for r in rs if stamped(r)]
            print("transport_probes total", len(rs), "in_window", len(sel))
            for r in sel:
                print("  transport_probe", r.get("probe_id"),
                      "state", code(r.get("state")),
                      "created", ts(r.get("created_at")),
                      "expires", ts(r.get("expires_at")),
                      "received", ts(r.get("received_at")),
                      "sender", h(r.get("sender_hash")))
        if "fictional_task_reports" in tables:
            rs = rows(db, "fictional_task_reports")
            sel = [r for r in rs if inw(r.get("received_at"))]
            print("fictional_task_reports total", len(rs), "in_window", len(sel))
            for r in sel:
                print("  fictional_task_report", r.get("report_id"),
                      "probe", r.get("probe_id"),
                      "received", ts(r.get("received_at")),
                      "task_requested", r.get("task_requested"),
                      "proposal", r.get("proposal"),
                      "price", r.get("price"), "currency", r.get("currency"),
                      "cancellation_terms", r.get("cancellation_terms"),
                      "owner_approval", r.get("owner_approval"),
                      "action_taken", r.get("action_taken"),
                      "outcome", r.get("outcome"),
                      "evidence", code(r.get("evidence_origin")),
                      "verification", code(r.get("verification_state")))
        if "fictional_reporting_windows" in tables:
            rs = rows(db, "fictional_reporting_windows")
            sel = [r for r in rs if stamped(r)]
            print("fictional_reporting_windows total", len(rs), "in_window", len(sel))
            for r in sel:
                print("  fictional_reporting_window", r.get("window_id"),
                      "probe", r.get("probe_id"), "state", code(r.get("state")),
                      "created", ts(r.get("created_at")), "expires", ts(r.get("expires_at")),
                      "stopped", ts(r.get("stopped_at")), "max_reports", r.get("max_reports"),
                      "recipient", code(r.get("recipient")), "sender", h(r.get("sender_hash")),
                      "deployed_commit", code(r.get("deployed_commit")),
                      "standing_rule_accepted_at", ts(r.get("standing_rule_accepted_at")),
                      "account_separation_reported", r.get("account_separation_reported"),
                      "account_separation_note", r.get("account_separation_note"),
                      "standing_report_consistent", r.get("standing_report_consistent"),
                      "standing_report_consistency_note", r.get("standing_report_consistency_note"),
                      "time_to_first_task_ms", r.get("time_to_first_task_ms"),
                      "tester_feedback", r.get("tester_feedback"))
        if "fictional_automatic_reports" in tables:
            rs = rows(db, "fictional_automatic_reports")
            sel = [r for r in rs if inw(r.get("received_at"))]
            print("fictional_automatic_reports total", len(rs), "in_window", len(sel))
            for r in sel:
                print("  fictional_automatic_report", r.get("report_id"),
                      "window", r.get("window_id"), "sequence", r.get("task_sequence"),
                      "received", ts(r.get("received_at")),
                      "task_requested", r.get("task_requested"),
                      "proposal", r.get("proposal"),
                      "price", r.get("price"), "currency", r.get("currency"),
                      "cancellation_terms", r.get("cancellation_terms"),
                      "owner_approval", r.get("owner_approval"),
                      "action_taken", r.get("action_taken"), "outcome", r.get("outcome"),
                      "task_id", r.get("task_id"), "task_started_at", ts(r.get("task_started_at")),
                      "pre_action_check_ids", r.get("pre_action_check_ids"),
                      "reported_answer", r.get("reported_answer"),
                      "reported_latency_ms", r.get("reported_latency_ms"),
                      "stop_count", r.get("stop_count"),
                      "executions_without_prior_check_count", r.get("executions_without_prior_check_count"),
                      "forwarded_confirmation_count", r.get("forwarded_confirmation_count"),
                      "displayed_outcome_match", code(r.get("displayed_outcome_match")),
                      "non_recoverable_result", r.get("non_recoverable_result"),
                      "display_defect", r.get("display_defect"),
                      "evidence", code(r.get("evidence_origin")),
                      "verification", code(r.get("verification_state")))
        if "inbound_receipts" in tables:
            rs = rows(db, "inbound_receipts")
            sel = [r for r in rs if stamped(r)]
            print("inbound_receipts total", len(rs), "in_window", len(sel))
            for r in sel:
                extra = [f"{k} {code(v)}" for k, v in r.items()
                         if k in ("kind", "mode", "status", "state", "rejection_reason")]
                print("  receipt", h(r.get("receipt_id")), "sender", h(r.get("sender_address")),
                      "received", ts(r.get("received_at")), "created", ts(r.get("created_at")), *extra)
        db.close()
    request_log(since, until)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
