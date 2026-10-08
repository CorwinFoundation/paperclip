#!/usr/bin/env python3
"""Surgical live fix for BEAAA-39354; patches installed files, never repo copies."""
from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
import py_compile
import secrets
import shutil
import sqlite3
import subprocess
import tempfile

FAILURES = (
    ("8587f3eb7adf8e01a9eeab7a8cb69426", "3e89df46-f338-49ce-964f-23803f1d6f4b",
     "scanner_restart_resume_exhausted"),
    ("b8b41bbac75c9f2dd30ed71c910e65f6", "179b8cd7-76b0-4e07-81a8-7ec3d1b4ec53",
     "scanner_restarted_credential_not_retained"),
)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected exactly one live source match")
    return text.replace(old, new, 1)


def patch_sources(app: str, service: str) -> tuple[str, str]:
    app = replace_once(app,
        "        rows = body.get('result_rows')\n        if not isinstance(rows, list) or not 1 <= len(rows) <= 100 or not all(isinstance(row, dict) for row in rows):",
        "        rows = body.get('result_rows')\n        terminal_failure = (str(body.get('status') or '').lower() == 'failed'\n                            and body.get('incomplete') is True\n                            and bool(body.get('incomplete_reason')))\n        if (not isinstance(rows, list) or len(rows) > 100\n                or (not rows and not terminal_failure)\n                or not all(isinstance(row, dict) for row in rows)):", "callback validation")
    service = replace_once(service,
        '"SELECT count(*) FROM scans WHERE partner=? AND email=? AND status NOT IN (\'connection_failed\',\'cancelled\',\'interrupted\')",',
        '"SELECT count(*) FROM scans WHERE partner=? AND email=? AND status NOT IN (\'connection_failed\',\'cancelled\',\'interrupted\') "\n                "AND NOT (status=\'finished\' AND outcome=\'failed\')",', "email quota")
    service = replace_once(service,
        '"SELECT count(DISTINCT email) FROM scans WHERE code=? AND status NOT IN (\'connection_failed\',\'cancelled\',\'interrupted\')",',
        '"SELECT count(DISTINCT email) FROM scans WHERE code=? AND status NOT IN (\'connection_failed\',\'cancelled\',\'interrupted\') "\n                "AND NOT (status=\'finished\' AND outcome=\'failed\')",', "partner quota")
    service = replace_once(service,
        '"SELECT 1 FROM scans WHERE code=? AND email=? AND status NOT IN (\'connection_failed\',\'cancelled\',\'interrupted\') LIMIT 1"',
        '"SELECT 1 FROM scans WHERE code=? AND email=? AND status NOT IN (\'connection_failed\',\'cancelled\',\'interrupted\') AND NOT (status=\'finished\' AND outcome=\'failed\') LIMIT 1"', "known email")
    service = replace_once(service,
        '        if status == "finished" and row["outcome"] == "incomplete":\n            status = "incomplete"',
        '        if status == "finished" and row["outcome"] == "incomplete":\n            status = "incomplete"\n        elif status == "finished" and row["outcome"] == "failed":\n            status = "error"', "truthful status")
    service = replace_once(service,
        '"outcome": "incomplete" if incomplete else "complete",',
        '"outcome": "failed" if status == "failed" and not (payload.get("result_rows") or []) else "incomplete" if incomplete else "complete",', "failed outcome")
    service = replace_once(service, '    incomplete = outcome == "incomplete"',
                           '    incomplete = outcome in {"incomplete", "failed"}', "failed report")
    return app, service


def reconcile(db_path: Path) -> None:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("SELECT count(*) FROM scans WHERE status IN ('queued','starting','reserved')").fetchone()[0]:
            raise RuntimeError("undispatched intake payload may exist; refusing restart")
        for intake_id, scanner_id, reason in FAILURES:
            row = db.execute("SELECT status,scanner_id,outcome,incomplete_reason FROM scans WHERE id=?", (intake_id,)).fetchone()
            if not row or row["scanner_id"] != scanner_id:
                raise RuntimeError(f"intake/scanner binding mismatch: {intake_id}")
            if row["status"] == "finished" and row["outcome"] == "failed" and row["incomplete_reason"] == reason:
                continue
            if row["status"] != "running":
                raise RuntimeError(f"intake is not running: {intake_id}")
            db.execute("UPDATE scans SET status='finished',report_token=COALESCE(report_token,?),tests_json='[]',finished_at=?,outcome='failed',incomplete_reason=? WHERE id=?",
                       (secrets.token_hex(16), dt.datetime.now(dt.timezone.utc).isoformat(), reason, intake_id))
            db.execute("DELETE FROM mail_outbox WHERE scan_id=?", (intake_id,))
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK"); raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-dir", type=Path, default=Path("/opt/backbond/scan-intake/server"))
    parser.add_argument("--db", type=Path, default=Path("/var/lib/backbond-scan-intake/scan-intake.sqlite"))
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0 and not args.no_restart:
        raise SystemExit("run as root")
    app_path, service_path = args.server_dir / "app.py", args.server_dir / "qa_service.py"
    app, service = patch_sources(app_path.read_text(), service_path.read_text())
    backup = Path(tempfile.mkdtemp(prefix="beaaa-39354-", dir="/var/tmp"))
    shutil.copy2(app_path, backup / "app.py"); shutil.copy2(service_path, backup / "qa_service.py")
    try:
        app_path.write_text(app); service_path.write_text(service)
        py_compile.compile(str(app_path), doraise=True); py_compile.compile(str(service_path), doraise=True)
        reconcile(args.db)
        if not args.no_restart:
            subprocess.run(["systemctl", "restart", "backbond-scan-intake.service"], check=True)
            subprocess.run(["systemctl", "is-active", "--quiet", "backbond-scan-intake.service"], check=True)
    except Exception:
        shutil.copy2(backup / "app.py", app_path); shutil.copy2(backup / "qa_service.py", service_path)
        if not args.no_restart:
            subprocess.run(["systemctl", "restart", "backbond-scan-intake.service"], check=False)
        raise
    print("reconciled " + " ".join(
        f"intake={intake} scanner={scanner} outcome=failed reason={reason}"
        for intake, scanner, reason in FAILURES))


if __name__ == "__main__":
    main()
