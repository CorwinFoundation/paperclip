#!/usr/bin/env python3
"""Read-only Stage B receipt summary for agent verification (BEAAA-38728).

Usage: status.py <since> <until>   (ISO timestamps, e.g. 2026-10-06T18:30)

Opens every Stage B sqlite file with mode=ro and prints counts, 10-char
sha256 prefixes and timestamps only. It never prints an email, token,
report body, passphrase or any other stored value in clear. The installer
places it at /opt/backbond-stage-b/bin/status.py and lets are-deploy run it
as backbond-stage-b through sudo.
"""
import glob
import hashlib
import os
import re
import sqlite3
import sys

STATE_DIR = os.environ.get("BACKBOND_STAGE_B_STATE_DIR", "/var/lib/backbond-stage-b")
WINDOW_ARG = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}(:\d{2}(:\d{2})?)?)?$")


def h(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:10] if value else None


def ts(value):
    """Return value only if it looks like a timestamp; otherwise its hash."""
    if value is None:
        return None
    text = str(value)
    return text if re.match(r"^\d{4}-\d{2}-\d{2}[T ][0-9:.+Z-]*$", text) else h(text)


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
        if "instinct_standing_reports" in tables:
            rs = rows(db, "instinct_standing_reports")
            sel = [r for r in rs if inw(r.get("received_at"))]
            print("instinct_standing_reports total", len(rs), "in_window", len(sel))
            for r in sel:
                print("  report", h(r.get("report_id")), "grant", h(r.get("grant_digest")),
                      "dedupe", h(r.get("deduplication_key")), "body", h(r.get("report_json")),
                      "received", ts(r.get("received_at")))
        for table in ("history_requests", "history_previews", "history_events",
                      "instinct_followup_requests", "inbound_receipts"):
            if table in tables:
                rs = rows(db, table)
                print(table, "total", len(rs), "in_window", sum(1 for r in rs if stamped(r)))
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
