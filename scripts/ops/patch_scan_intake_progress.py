#!/usr/bin/env python3
"""Surgically add bounded engine-progress forwarding to the live intake app."""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import os
from pathlib import Path
import py_compile
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request

INSERT_AFTER = '''def _status(token: str) -> dict:\n    value = store.status(token)\n    if not value:\n        raise HTTPException(404, "scan not found")\n    return value\n'''
OLD_STATUS = '''@app.post("/scan/v1/scan-status")
def scan_status(body: StatusRequest):
    # POST so the bearer-style token never lands in access-log URLs.
    value = _status(body.status_token)
    value.pop("scan_id")
    return value
'''
PROGRESS = '''

_PROGRESS_FIELDS = {
    "state", "phase", "stage_label", "processed_count", "accepted_pack_total",
    "usable_count", "unavailable_count", "timing", "heartbeat_at", "updated_at",
    "last_activity_at", "current_test_id", "current_family",
}


def _engine_progress(scan_id: str) -> dict:
    """Fetch the bounded owner-scoped engine projection, never its evidence rows."""
    row = store.scan(scan_id)
    if not row or not row["scanner_id"] or row["status"] not in {"running", "dispatch_unknown"}:
        return {}
    tenant_id = f"partner-{row['partner']}"
    try:
        with httpx.Client(timeout=5) as client:
            response = client.get(
                f"{SCANNER_URL}/scans/{row['scanner_id']}/progress",
                headers=_headers({"tenant_id": tenant_id}),
            )
        if response.status_code != 200:
            return {}
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: payload[key] for key in _PROGRESS_FIELDS if key in payload}
'''
NEW_STATUS = '''@app.post("/scan/v1/scan-status")
def scan_status(body: StatusRequest):
    # POST so the bearer-style token never lands in access-log URLs.
    value = _status(body.status_token)
    progress = _engine_progress(value["scan_id"])
    if progress:
        value.update(progress)
        value["status"] = progress.get("state", value["status"])
    value.pop("scan_id")
    return value
'''


def patch_source(source: str) -> tuple[str, bool]:
    if PROGRESS.strip() in source and NEW_STATUS in source:
        return source, False
    for label, anchor in (("_status insertion anchor", INSERT_AFTER), ("scan_status live baseline", OLD_STATUS)):
        count = source.count(anchor)
        if count != 1:
            raise RuntimeError(f"{label}: expected exactly one match, found {count}; refusing")
    candidate = source.replace(INSERT_AFTER, INSERT_AFTER + PROGRESS, 1).replace(OLD_STATUS, NEW_STATUS, 1)
    tree = ast.parse(candidate)
    functions = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if not {"_headers", "_engine_progress", "scan_status"} <= functions:
        raise RuntimeError("candidate is missing required _headers/_engine_progress/scan_status functions")
    if "store.scan(scan_id)" not in candidate or 'headers=_headers({"tenant_id": tenant_id})' not in candidate:
        raise RuntimeError("candidate does not use the live store.scan/_headers accessors")
    return candidate, True


def active_intakes(db_path: Path, target: str) -> tuple[int, list[str]]:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT id FROM scans WHERE status IN ('queued','starting','reserved','running','dispatch_unknown')"
        ).fetchall()
    ids = [str(row[0]) for row in rows]
    return len(ids), [value for value in ids if value != target]


def process_environment(pid: int) -> dict[str, str]:
    raw = Path(f"/proc/{pid}/environ").read_bytes()
    return {part.split(b"=", 1)[0].decode(): part.split(b"=", 1)[1].decode()
            for part in raw.split(b"\0") if b"=" in part}


def verify_live(server_dir: Path, intake_id: str) -> None:
    with urllib.request.urlopen("http://127.0.0.1:8793/health", timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"intake health returned HTTP {response.status}")
    pid = int(subprocess.check_output(
        ["systemctl", "show", "--property=MainPID", "--value", "backbond-scan-intake.service"], text=True
    ).strip())
    env = os.environ.copy(); env.update(process_environment(pid))
    code = (
        "import json,sys; sys.path.insert(0,sys.argv[1]); import app; "
        "p=app._engine_progress(sys.argv[2]); "
        "assert all(p.get(k) is not None for k in ('last_activity_at','current_test_id','current_family')), p; "
        "print(json.dumps({k:p[k] for k in ('last_activity_at','current_test_id','current_family')},sort_keys=True))"
    )
    subprocess.run([sys.executable, "-c", code, str(server_dir), intake_id], env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write, restart, and verify (default: dry-run)")
    parser.add_argument("--app", type=Path, default=Path("/opt/backbond/scan-intake/server/app.py"))
    parser.add_argument("--db", type=Path, default=Path("/var/lib/backbond-scan-intake/scan-intake.sqlite"))
    parser.add_argument("--intake-id", required=True, help="active intake used for the real progress verification")
    args = parser.parse_args()
    source = args.app.read_text()
    candidate, changed = patch_source(source)
    if not changed:
        print("already patched; no-op")
        return
    with tempfile.TemporaryDirectory(prefix="beaaa-39573-") as tmp:
        candidate_path = Path(tmp) / "app.py"
        candidate_path.write_text(candidate)
        py_compile.compile(str(candidate_path), doraise=True)
    total, unrelated = active_intakes(args.db, args.intake_id)
    print(f"active-intake count: {total}; unrelated: {len(unrelated)}")
    if unrelated:
        raise SystemExit("unrelated active intake(s); refusing restart: " + ",".join(unrelated))
    if not args.apply:
        print("dry-run: exact anchors and candidate py_compile passed; use --apply to install")
        return
    if os.geteuid() != 0:
        raise SystemExit("--apply must run as root")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = args.app.with_name(f"{args.app.name}.beaaa-39573.{stamp}.bak")
    shutil.copy2(args.app, backup)
    try:
        args.app.write_text(candidate)
        py_compile.compile(str(args.app), doraise=True)
        subprocess.run(["systemctl", "restart", "backbond-scan-intake.service"], check=True)
        subprocess.run(["systemctl", "is-active", "--quiet", "backbond-scan-intake.service"], check=True)
        verify_live(args.app.parent, args.intake_id)
    except Exception:
        shutil.copy2(backup, args.app)
        subprocess.run(["systemctl", "restart", "backbond-scan-intake.service"], check=False)
        raise
    print(f"installed; backup={backup}")


if __name__ == "__main__":
    main()
