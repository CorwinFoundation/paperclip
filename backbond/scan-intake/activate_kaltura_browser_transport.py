#!/usr/bin/env python3
"""Activate the reviewed Kaltura browser transport in a customized intake install."""
from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
import py_compile
import shutil
import sqlite3
import subprocess


OLD_TRANSPORT = 'transport: str = Field(pattern="^(rest|openai_chat|json_map|vapi)$")'
NEW_TRANSPORT = 'transport: str = Field(pattern="^(rest|openai_chat|json_map|vapi|kaltura_genie|kaltura_genie_browser)$")'
OLD_TRANSPORT_AGUI = OLD_TRANSPORT.replace("json_map|vapi", "json_map|ag_ui|vapi")
NEW_TRANSPORT_AGUI = NEW_TRANSPORT.replace("json_map|vapi", "json_map|ag_ui|vapi")
ANCHOR = '''            if not self.response_path:
                raise ValueError("response_path is required for json_map")
        return self'''
VALIDATION = '''        if self.transport == "kaltura_genie":
            if not re.fullmatch(r"genie\\.[a-z0-9-]+\\.ovp\\.kaltura\\.com", endpoint_host):
                raise ValueError("kaltura_genie requires a regional Kaltura Genie host")
            if urlsplit(self.endpoint_url).path.rstrip('/') != '/assistant/converse':
                raise ValueError("kaltura_genie requires /assistant/converse")
            if self.auth.type != "bearer":
                raise ValueError("kaltura_genie requires a scoped KS in the secure auth field")
        if self.transport == "kaltura_genie_browser":
            if self.endpoint_url != "https://learning.kaltura.com/":
                raise ValueError("kaltura_genie_browser requires the approved public URL")
            if self.auth.type != "none":
                raise ValueError("kaltura_genie_browser requires auth type none")
'''
ACTIVE_STATUSES = ("reserved", "starting", "running", "dispatch_unknown")


def patch_app(text: str) -> str:
    declarations = {
        OLD_TRANSPORT: NEW_TRANSPORT,
        NEW_TRANSPORT: NEW_TRANSPORT,
        OLD_TRANSPORT_AGUI: NEW_TRANSPORT_AGUI,
        NEW_TRANSPORT_AGUI: NEW_TRANSPORT_AGUI,
    }
    matches = [value for value in declarations if value in text]
    if len(matches) != 1 or text.count(matches[0]) != 1:
        raise RuntimeError("expected exactly one recognized transport declaration")
    if text.count(VALIDATION) > 1:
        raise RuntimeError("duplicate Kaltura validation blocks")
    needs_validation = VALIDATION not in text
    if needs_validation and text.count(ANCHOR) != 1:
        raise RuntimeError("expected exactly one ConnectionFields validation anchor")
    text = text.replace(matches[0], declarations[matches[0]], 1)
    if not needs_validation:
        return text
    replacement = ANCHOR.removesuffix("        return self") + VALIDATION + "        return self"
    return text.replace(ANCHOR, replacement, 1)


def assert_idle(db_path: Path) -> None:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        count = db.execute(
            f"SELECT count(*) FROM scans WHERE status IN ({placeholders})", ACTIVE_STATUSES
        ).fetchone()[0]
    if count:
        raise RuntimeError(f"refusing intake reload: {count} active or dispatch-uncertain scan(s)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, default=Path("/opt/backbond/scan-intake/server/app.py"))
    parser.add_argument("--db", type=Path, default=Path("/var/lib/backbond-scan-intake/scan-intake.sqlite"))
    parser.add_argument("--service", default="backbond-scan-intake.service")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("run as root")

    current = args.app.read_text()
    patched = patch_app(current)
    if patched == current:
        print("already active; no reload required")
        return
    assert_idle(args.db)

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path("/var/backups/backbond-scan-intake") / f"beaaa-39354-{stamp}"
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(args.app, backup / "app.py")
    staged = args.app.with_name(".app.py.beaaa-39354")
    try:
        staged.write_text(patched)
        py_compile.compile(str(staged), doraise=True)
        os.chown(staged, args.app.stat().st_uid, args.app.stat().st_gid)
        os.chmod(staged, args.app.stat().st_mode & 0o7777)
        os.replace(staged, args.app)
        subprocess.run(["systemctl", "reload-or-restart", args.service], check=True)
        subprocess.run(["systemctl", "is-active", "--quiet", args.service], check=True)
    except Exception:
        staged.unlink(missing_ok=True)
        shutil.copy2(backup / "app.py", args.app)
        subprocess.run(["systemctl", "restart", args.service], check=False)
        raise
    print(f"activated kaltura_genie_browser; backup={backup}")


if __name__ == "__main__":
    main()
