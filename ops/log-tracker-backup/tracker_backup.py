#!/usr/bin/env python3
"""WAL-safe Log Tracker backup and non-destructive restore verification."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

TABLES = ("scanner_runs", "policy_events", "claim_events", "evidence_refs")
HOURLY_KEEP = 168
DAILY_KEEP = 90


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return "sha256:" + value.hexdigest()


def inspect_db(path: Path) -> dict[str, object]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        present = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        counts = {
            table: db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            for table in TABLES if table in present
        }
    return {"integrity": integrity, "row_counts": counts}


def append_manifest(root: Path, record: dict[str, object]) -> None:
    with (root / "MANIFEST.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def prune(directory: Path, keep: int) -> list[str]:
    snapshots = sorted(directory.glob("*.sqlite3.gz"), key=lambda p: p.stat().st_mtime,
                       reverse=True)
    removed = []
    for snapshot in snapshots[keep:]:
        snapshot.unlink()
        removed.append(snapshot.name)
    return removed


def backup(source: Path, root: Path) -> int:
    if not source.is_file():
        raise FileNotFoundError(source)
    hourly, daily = root / "hourly", root / "daily"
    hourly.mkdir(parents=True, exist_ok=True)
    daily.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with tempfile.TemporaryDirectory(prefix="tracker-backup-", dir=root) as work:
        raw = Path(work) / "log-tracker.sqlite3"
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30) as src, \
                sqlite3.connect(raw) as dst:
            src.backup(dst)
        inspection = inspect_db(raw)
        if inspection["integrity"] != "ok":
            append_manifest(root, {"timestamp": stamp, "status": "FAILED", **inspection})
            return 1
        snapshot = hourly / f"log-tracker-{stamp}.sqlite3.gz"
        with raw.open("rb") as src, gzip.open(snapshot, "wb", compresslevel=9) as dst:
            shutil.copyfileobj(src, dst)
    promoted = None
    if not any(daily.glob(f"log-tracker-{stamp[:8]}T*.sqlite3.gz")):
        promoted = daily / snapshot.name
        shutil.copy2(snapshot, promoted)
    record = {
        "timestamp": stamp, "status": "OK", "snapshot": snapshot.name,
        "sha256": digest(snapshot), **inspection,
        "daily_promoted": promoted.name if promoted else None,
        "pruned_hourly": prune(hourly, HOURLY_KEEP),
        "pruned_daily": prune(daily, DAILY_KEEP),
    }
    append_manifest(root, record)
    print(json.dumps(record, sort_keys=True))
    return 0


def restore_test(root: Path) -> int:
    snapshots = sorted((root / "hourly").glob("*.sqlite3.gz"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    if not snapshots:
        raise FileNotFoundError("no hourly snapshot")
    snapshot = snapshots[0]
    expected = None
    for line in (root / "MANIFEST.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("snapshot") == snapshot.name:
            expected = record.get("sha256")
    actual = digest(snapshot)
    try:
        # Backup media is intentionally read-only in the scheduled unit. Use
        # its PrivateTmp namespace for scratch restore material.
        with tempfile.TemporaryDirectory(prefix="tracker-restore-test-") as work:
            raw = Path(work) / "restored.sqlite3"
            with gzip.open(snapshot, "rb") as src, raw.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            inspection = inspect_db(raw)
    except (gzip.BadGzipFile, EOFError, OSError, sqlite3.DatabaseError) as error:
        report = {"mode": "restore-test", "snapshot": snapshot.name,
                  "sha256_match": expected == actual, "verified": False,
                  "error": f"{type(error).__name__}: {error}"}
        print(json.dumps(report, sort_keys=True))
        return 1
    report = {"mode": "restore-test", "snapshot": snapshot.name,
              "sha256_match": expected == actual, **inspection}
    report["verified"] = report["sha256_match"] and inspection["integrity"] == "ok"
    print(json.dumps(report, sort_keys=True))
    return 0 if report["verified"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("backup", "restore-test"))
    parser.add_argument("--source", type=Path,
                        default=Path("/var/lib/are-scanner/log-tracker.sqlite3"))
    parser.add_argument("--backup-root", type=Path,
                        default=Path("/var/lib/are-scanner/backups/log-tracker"))
    args = parser.parse_args()
    return backup(args.source, args.backup_root) if args.command == "backup" \
        else restore_test(args.backup_root)


if __name__ == "__main__":
    raise SystemExit(main())
