#!/usr/bin/env python3
"""Idempotently add PR #983's two failed-report mail guards to live intake."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile


DEFAULT_TARGET = Path("/opt/backbond/scan-intake/server/qa_service.py")
DEFAULT_DB = Path("/var/lib/backbond-scan-intake/scan-intake.sqlite")
SERVICE = "backbond-scan-intake.service"

REWRITES = (
    (
        "        if row['status'] == 'finished' and row['report_token']:\n"
        "            self.db.execute('INSERT OR IGNORE INTO mail_outbox(scan_id) VALUES(?)', (scan_id,))\n"
        "            return row['report_token']\n",
        "        if row['status'] == 'finished' and row['report_token']:\n"
        "            if row['outcome'] != 'failed':\n"
        "                self.db.execute('INSERT OR IGNORE INTO mail_outbox(scan_id) VALUES(?)', (scan_id,))\n"
        "            return row['report_token']\n",
    ),
    (
        "        self.db.execute('INSERT OR IGNORE INTO mail_outbox(scan_id) VALUES(?)', (scan_id,))\n"
        "        return token\n",
        "        if meta.get(\"outcome\") != \"failed\":\n"
        "            self.db.execute('INSERT OR IGNORE INTO mail_outbox(scan_id) VALUES(?)', (scan_id,))\n"
        "        return token\n",
    ),
)


def guarded_source(source: str) -> tuple[str, bool]:
    changed = False
    for unguarded, guarded in REWRITES:
        if guarded in source:
            continue
        count = source.count(unguarded)
        if count != 1:
            raise SystemExit(f"refusing source with {count} matches for expected PR #983 boundary")
        source = source.replace(unguarded, guarded, 1)
        changed = True
    return source, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--check", action="store_true", help="validate only; do not write")
    args = parser.parse_args()

    target = args.target.resolve()
    original = target.read_text()
    updated, changed = guarded_source(original)
    compile(updated, str(target), "exec")
    if args.check:
        print("guard_present" if not changed else "guard_installable")
        return 0

    db = sqlite3.connect(f"file:{args.db.resolve()}?mode=rw", uri=True)
    undispatched = db.execute(
        "SELECT count(*) FROM scans WHERE status IN ('reserved','queued','starting')"
    ).fetchone()[0]
    if undispatched:
        raise SystemExit(f"refusing intake reload with {undispatched} undispatched scans")
    failed_pending = db.execute(
        "SELECT count(*) FROM mail_outbox m JOIN scans s ON s.id=m.scan_id "
        "WHERE m.status='pending' AND s.outcome='failed'"
    ).fetchone()[0]

    backup = target.with_suffix(target.suffix + ".pre-pr983")
    if not backup.exists():
        shutil.copy2(target, backup)
    stat = target.stat()
    fd, temporary = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.st_mode)
        os.chown(temporary, stat.st_uid, stat.st_gid)
        if changed:
            os.replace(temporary, target)
        else:
            os.unlink(temporary)
        with db:
            db.execute(
                "DELETE FROM mail_outbox WHERE status='pending' AND scan_id IN "
                "(SELECT id FROM scans WHERE outcome='failed')"
            )
        subprocess.run(["systemctl", "restart", SERVICE], check=True)
        subprocess.run(["systemctl", "is-active", "--quiet", SERVICE], check=True)
        remaining = db.execute(
            "SELECT count(*) FROM mail_outbox m JOIN scans s ON s.id=m.scan_id "
            "WHERE m.status='pending' AND s.outcome='failed'"
        ).fetchone()[0]
        if remaining:
            raise RuntimeError(f"failed pending mail remains after reload: {remaining}")
    except Exception:
        if changed and backup.exists():
            shutil.copy2(backup, target)
            subprocess.run(["systemctl", "restart", SERVICE], check=False)
        raise
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
        db.close()
    print(f"installed backup={backup} removed_failed_pending_mail={failed_pending} service=active")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
