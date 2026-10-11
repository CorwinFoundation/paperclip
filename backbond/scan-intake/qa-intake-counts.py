#!/usr/bin/env python3
"""Print privacy-bounded aggregate scan-intake quota evidence for one code."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path


DEFAULT_DB = Path("/var/lib/backbond-scan-intake/scan-intake.sqlite")
ACTIVE = ("reserved", "queued", "starting", "running", "dispatch_unknown")
NON_COUNTING = ("connection_failed", "cancelled", "interrupted")
CODE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def _email_identity(code: str, email: str) -> str:
    # Code-scoped identifiers let QA distinguish its test identities without
    # exposing email addresses or allowing correlation between partner codes.
    value = f"{code}\0{email.strip().lower()}".encode()
    return "sha256:" + hashlib.sha256(value).hexdigest()


def collect(db_path: Path, code: str) -> dict:
    if not CODE_RE.fullmatch(code):
        raise ValueError("code must contain only letters, digits, underscore, or hyphen")
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        code_row = db.execute(
            "SELECT max_emails, scans_per_email FROM codes WHERE code=?", (code,)
        ).fetchone()
        if code_row is None:
            raise LookupError("code not found")
        rows = db.execute(
            "SELECT email,status,outcome FROM scans WHERE code=?", (code,)
        ).fetchall()

    status_counts: dict[str, int] = {}
    email_counts: dict[str, dict[str, int]] = {}
    admitted = 0
    for row in rows:
        status = str(row["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
        identity = _email_identity(code, str(row["email"]))
        grouped = email_counts.setdefault(identity, {"submitted": 0, "admitted": 0})
        grouped["submitted"] += 1
        counts = status not in NON_COUNTING and not (
            status == "finished" and row["outcome"] == "failed"
        )
        if counts:
            admitted += 1
            grouped["admitted"] += 1

    return {
        "code": code,
        "quota": {
            "max_emails": code_row["max_emails"],
            "scans_per_email": code_row["scans_per_email"],
        },
        "email_identity_count": len(email_counts),
        "email_counts": [
            {"email_identity": identity, **email_counts[identity]}
            for identity in sorted(email_counts)
        ],
        "scan_counts": {
            "submitted": len(rows),
            "admitted": admitted,
            "reserved": status_counts.get("reserved", 0),
            "connection_failed": status_counts.get("connection_failed", 0),
            "finished": status_counts.get("finished", 0),
            "terminal": sum(status_counts.get(s, 0) for s in (
                "finished", "connection_failed", "launch_failed", "cancelled", "interrupted"
            )),
            "active_concurrency": sum(status_counts.get(s, 0) for s in ACTIVE),
        },
        "status_counts": dict(sorted(status_counts.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Emit aggregate quota evidence for exactly one QA code"
    )
    parser.add_argument("code")
    args = parser.parse_args()
    try:
        print(json.dumps(collect(DEFAULT_DB, args.code), sort_keys=True))
    except (OSError, sqlite3.Error, ValueError, LookupError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
