#!/usr/bin/env python3
"""Verify + restore a scan_log.sqlite3 backup. BEAAA-13386.

Two modes:
  --dry-run  (DEFAULT, non-destructive): decompress the chosen snapshot into a
             scratch path, re-verify sha256 against MANIFEST.jsonl, run
             PRAGMA integrity_check, and report the scan_log row count. This is
             the RESTORE-TEST that proves recoverability WITHOUT touching the
             live DB. Run it on a schedule and after every deploy.
  --commit   (destructive, operator-gated): stop the scanner unit FIRST (the
             caller is responsible — this script refuses to overwrite a live
             DB unless --i-stopped-the-service is passed), snapshot the current
             live DB to a pre-restore file, install the verified snapshot at
             the live path, and report the post-restore row count.

Stdlib only. Egress-free. See docs/deploy/scan-log-backup-runbook.md.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LIVE = Path("/var/lib/are-scanner/scan_log.sqlite3")
DEFAULT_BACKUP_ROOT = Path("/var/lib/are-scanner/backups/scan_log")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _latest_snapshot(root: Path) -> Path | None:
    snaps = sorted(root.glob("hourly/*.sqlite3.gz"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return snaps[0] if snaps else None


def _manifest_sha_gz(root: Path, name: str) -> str | None:
    manifest = root / "MANIFEST.jsonl"
    if not manifest.exists():
        return None
    match = None
    for line in manifest.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("hourly_file") == name or rec.get("daily_promoted") == name:
            match = rec.get("sha256_gz")
    return match


def _integrity(db: Path) -> str:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30) as c:
        row = c.execute("PRAGMA integrity_check;").fetchone()
    return row[0] if row else "<none>"


def _rowcount(db: Path) -> int | None:
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30) as c:
            return int(c.execute("SELECT count(*) FROM scan_log;").fetchone()[0])
    except sqlite3.Error:
        return None


def _decompress(gz: Path, dst: Path) -> None:
    with gzip.open(gz, "rb") as fin, dst.open("wb") as fout:
        shutil.copyfileobj(fin, fout)


def _verify(gz: Path, root: Path) -> tuple[bool, dict]:
    report: dict = {"snapshot": gz.name}
    actual = _sha256(gz)
    expected = _manifest_sha_gz(root, gz.name)
    report["sha256_gz_actual"] = actual
    report["sha256_gz_manifest"] = expected
    report["sha256_match"] = (expected is None) or (expected == actual)
    if expected is None:
        report["sha256_note"] = "no manifest entry — cannot cross-check (WARN)"

    tmp = Path(tempfile.mkdtemp(prefix="scanlog-restore-", dir=str(root)))
    try:
        raw = tmp / "candidate.sqlite3"
        _decompress(gz, raw)
        integrity = _integrity(raw)
        report["integrity"] = integrity
        report["row_count"] = _rowcount(raw)
        ok = report["sha256_match"] and integrity == "ok"
        report["verify_ok"] = ok
        return ok, report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def dry_run(gz: Path, root: Path) -> int:
    ok, report = _verify(gz, root)
    print(json.dumps({"mode": "dry-run", **report}, indent=2))
    return 0 if ok else 1


def commit(gz: Path, root: Path, live: Path, i_stopped: bool) -> int:
    if not i_stopped:
        print("REFUSED: pass --i-stopped-the-service after stopping the "
              "scanner unit (systemctl stop are-scanner) so no writer races "
              "the restore. See runbook §Restore-commit.", file=sys.stderr)
        return 2
    ok, report = _verify(gz, root)
    if not ok:
        print(json.dumps({"mode": "commit", "aborted": "verify_failed",
                          **report}, indent=2), file=sys.stderr)
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if live.exists():
        pre = live.with_name(f"{live.name}.pre-restore-{stamp}")
        shutil.copy2(live, pre)
        report["pre_restore_copy"] = pre.name

    raw = live.with_name(f".restore-{stamp}.tmp")
    _decompress(gz, raw)
    raw.replace(live)  # atomic same-filesystem rename into place
    report["restored_to"] = str(live)
    report["post_restore_integrity"] = _integrity(live)
    report["post_restore_row_count"] = _rowcount(live)
    print(json.dumps({"mode": "commit", **report}, indent=2))
    print("\nNEXT: systemctl start are-scanner, then confirm health + a live "
          "GET /scan/{id} returns the documented envelope.", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    ap.add_argument("--live", type=Path, default=DEFAULT_LIVE)
    ap.add_argument("--snapshot", type=Path, default=None,
                    help="path to a *.sqlite3.gz; default = latest hourly")
    ap.add_argument("--commit", action="store_true",
                    help="DESTRUCTIVE: install snapshot at the live path")
    ap.add_argument("--i-stopped-the-service", action="store_true",
                    help="required safety ack for --commit")
    args = ap.parse_args()

    gz = args.snapshot or _latest_snapshot(args.backup_root)
    if gz is None or not gz.exists():
        print("FATAL: no snapshot found to restore", file=sys.stderr)
        return 2

    if args.commit:
        return commit(gz, args.backup_root, args.live,
                      args.i_stopped_the_service)
    return dry_run(gz, args.backup_root)


if __name__ == "__main__":
    raise SystemExit(main())
