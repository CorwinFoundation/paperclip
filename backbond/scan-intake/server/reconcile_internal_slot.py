#!/usr/bin/env python3
"""Idempotently release one stale, unlaunched BackBond internal reservation."""
import argparse
import os
import sqlite3
from pathlib import Path

COUNTING = "status NOT IN ('connection_failed','cancelled','interrupted') AND NOT (status='finished' AND outcome='failed')"


def reconcile(db_path: Path, code: str, email: str):
    email = email.strip().lower()
    if not code.startswith("INTERNAL-VERIFY-") or not email.endswith("@backbond.ai"):
        raise ValueError("only INTERNAL-VERIFY codes and @backbond.ai recipients are allowed")
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        code_row = db.execute("SELECT * FROM codes WHERE code=?", (code,)).fetchone()
        if code_row is None:
            raise ValueError("internal verification code not found")
        known = db.execute(f"SELECT 1 FROM scans WHERE code=? AND email=? AND {COUNTING} LIMIT 1", (code, email)).fetchone()
        used = db.execute(f"SELECT count(DISTINCT email) FROM scans WHERE code=? AND {COUNTING}", (code,)).fetchone()[0]
        if known or used < code_row["max_emails"]:
            db.execute("ROLLBACK")
            return "already_admissible", used, code_row["max_emails"]
        rows = db.execute(f"""SELECT id FROM scans WHERE code=? AND email LIKE '%@backbond.ai'
          AND status IN ('reserved','queued','starting') AND scanner_id IS NULL AND {COUNTING}
          ORDER BY created_at""", (code,)).fetchall()
        if len(rows) != 1:
            raise ValueError(f"expected exactly one stale unlaunched internal row; found {len(rows)}")
        scan_id = rows[0]["id"]
        changed = db.execute("UPDATE scans SET status='cancelled' WHERE id=? AND scanner_id IS NULL AND status IN ('reserved','queued','starting')", (scan_id,)).rowcount
        if changed != 1:
            raise RuntimeError("candidate changed during reconciliation")
        after = db.execute(f"SELECT count(DISTINCT email) FROM scans WHERE code=? AND {COUNTING}", (code,)).fetchone()[0]
        if after >= code_row["max_emails"]:
            raise RuntimeError("reconciliation did not restore an admission slot")
        db.execute("COMMIT")
        return scan_id, after, code_row["max_emails"]
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("QA_DB_PATH", "/var/lib/backbond-scan-intake/scan-intake.sqlite")))
    parser.add_argument("--code", required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    result, used, limit = reconcile(args.db, args.code, args.email)
    print(f"result={result} admission=allowed distinct_emails={used}/{limit} replacement_scan=not_created")
