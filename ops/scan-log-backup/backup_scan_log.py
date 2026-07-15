#!/usr/bin/env python3
"""Recurring, integrity-checked, egress-free backup of scan_log.sqlite3.

BEAAA-13386 (BEAAA-12895 criterion 6). CTO-unilateral ops tooling — touches
no scoring code, rubric, probe set, or contract. The scan_log holds the
forensic bundle URIs + snapshot hashes behind every BackBond Score; no score's
evidence may rest on a single un-backed-up SQLite file.

Design invariants (see docs/deploy/scan-log-backup-runbook.md + ADR):
  * ONLINE backup via sqlite3.Connection.backup() — WAL-safe, no `cp` of a live
    DB (which can copy a torn page). Consistent even while /score is writing.
  * Every snapshot is integrity-checked (PRAGMA integrity_check == 'ok') BEFORE
    it is accepted. A snapshot that fails the check is quarantined, never
    counted toward retention, and the run exits non-zero so the timer surfaces.
  * Append-only MANIFEST.jsonl gives the backups their own evidence trail:
    ts, sha256 (raw + gz), integrity result, scan_log row count, size.
  * ZERO network. Runs entirely on-box; the systemd unit sets PrivateNetwork=
    true. Off-box durability, when wanted, is an OPERATOR-mediated pull of this
    directory — never a box-initiated push. Consistent with the containment
    wall (zero inbound SSH) and the default-deny egress posture.
  * Generational retention: hourly tier + first-of-day daily tier, pruned by
    count. Tiny Phase-1 DB, so retention is generous and disk cost negligible.

Stdlib only (python3). No third-party deps, so it survives any venv slice.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SRC = Path("/var/lib/are-scanner/scan_log.sqlite3")
DEFAULT_BACKUP_ROOT = Path("/var/lib/are-scanner/backups/scan_log")

# Retention (by count). Tune in one place; runbook documents the rationale.
HOURLY_KEEP = 168   # 7 days at hourly cadence
DAILY_KEEP = 90     # ~3 months of first-of-day promotions


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _online_backup(src: Path, dst: Path) -> None:
    """WAL-safe consistent copy via the SQLite online backup API."""
    # Open source read-only so a backup can never mutate the live DB.
    src_uri = f"file:{src}?mode=ro"
    with sqlite3.connect(src_uri, uri=True, timeout=30) as s, \
            sqlite3.connect(str(dst)) as d:
        s.backup(d)  # atomic page-by-page online backup


def _integrity_ok(db: Path) -> tuple[bool, str]:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30) as c:
        row = c.execute("PRAGMA integrity_check;").fetchone()
    result = row[0] if row else "<none>"
    return result == "ok", result


def _scan_log_rowcount(db: Path) -> int | None:
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30) as c:
            row = c.execute("SELECT count(*) FROM scan_log;").fetchone()
        return int(row[0]) if row else None
    except sqlite3.Error:
        return None  # table name may differ across schema revs; non-fatal


def _prune(dirpath: Path, keep: int) -> list[str]:
    """Keep the newest `keep` *.sqlite3.gz by mtime; unlink the rest."""
    snaps = sorted(dirpath.glob("*.sqlite3.gz"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for p in snaps[keep:]:
        p.unlink(missing_ok=True)
        removed.append(p.name)
    return removed


def run(src: Path, root: Path) -> int:
    if not src.exists():
        print(f"FATAL: source not found: {src}", file=sys.stderr)
        return 2

    hourly = root / "hourly"
    daily = root / "daily"
    quarantine = root / "quarantine"
    for d in (hourly, daily, quarantine):
        d.mkdir(parents=True, exist_ok=True)

    stamp = _utc_stamp()
    tmpdir = Path(tempfile.mkdtemp(prefix="scanlog-bk-", dir=str(root)))
    try:
        raw = tmpdir / f"scan_log-{stamp}.sqlite3"
        try:
            _online_backup(src, raw)
        except sqlite3.DatabaseError as exc:
            # Source itself is unreadable/malformed (or disk full): the online
            # backup raised before producing a copy. Record the FAILURE in the
            # evidence trail and exit non-zero so systemd OnFailure surfaces it.
            _append_manifest(root, {
                "ts": stamp, "src": str(src), "status": "FAILED",
                "error": f"{type(exc).__name__}: {exc}",
            })
            print(f"FATAL: online backup failed: {exc}", file=sys.stderr)
            return 4

        ok, integrity = _integrity_ok(raw)
        rows = _scan_log_rowcount(raw)
        sha_raw = _sha256(raw)

        if not ok:
            # Quarantine the bad copy for forensics; fail loudly.
            q = quarantine / f"{raw.name}.BAD"
            shutil.move(str(raw), str(q))
            _append_manifest(root, {
                "ts": stamp, "src": str(src), "status": "QUARANTINED",
                "integrity": integrity, "sha256_raw": sha_raw,
                "row_count": rows, "quarantine_file": q.name,
            })
            print(f"FATAL: integrity_check={integrity!r} -> quarantined {q}",
                  file=sys.stderr)
            return 3

        # Compress into the hourly tier.
        gz = hourly / f"scan_log-{stamp}.sqlite3.gz"
        with raw.open("rb") as fin, gzip.open(gz, "wb", compresslevel=9) as fout:
            shutil.copyfileobj(fin, fout)
        sha_gz = _sha256(gz)
        size = gz.stat().st_size

        # First backup of the UTC day is promoted to the daily tier.
        day = stamp[:8]
        promoted = None
        if not any(daily.glob(f"scan_log-{day}T*.sqlite3.gz")):
            promoted = daily / gz.name
            shutil.copy2(gz, promoted)

        pruned_h = _prune(hourly, HOURLY_KEEP)
        pruned_d = _prune(daily, DAILY_KEEP)

        _append_manifest(root, {
            "ts": stamp, "src": str(src), "status": "OK",
            "integrity": integrity, "sha256_raw": sha_raw,
            "sha256_gz": sha_gz, "row_count": rows, "size_bytes": size,
            "hourly_file": gz.name,
            "daily_promoted": promoted.name if promoted else None,
            "pruned_hourly": pruned_h, "pruned_daily": pruned_d,
        })
        print(json.dumps({
            "status": "OK", "snapshot": gz.name, "sha256_raw": sha_raw,
            "row_count": rows, "integrity": integrity,
            "daily_promoted": bool(promoted),
        }))
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _append_manifest(root: Path, record: dict) -> None:
    manifest = root / "MANIFEST.jsonl"
    line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    with manifest.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    args = ap.parse_args()
    return run(args.src, args.backup_root)


if __name__ == "__main__":
    raise SystemExit(main())
