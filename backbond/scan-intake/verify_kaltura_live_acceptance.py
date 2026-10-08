#!/usr/bin/env python3
"""Run the non-admitting Kaltura browser acceptance inside the live scanner."""

from __future__ import annotations

import json
import os
import subprocess
import sys


CONTAINER = os.environ.get("BACKBOND_SCANNER_CONTAINER", "backbond-scanner-live-edge")
PROGRAM = r'''
import json
import time
from harness.kaltura_genie_browser import KalturaGenieBrowserRunner

runner = KalturaGenieBrowserRunner()
started = time.monotonic()
try:
    first = runner.complete("Reply with exactly OK.")
    second = runner.complete("Reply with exactly OK.")
    if not first.strip() or not second.strip():
        raise RuntimeError("empty assistant response")
    print(json.dumps({
        "ok": True,
        "transport": runner.model_id(),
        "anonymous_connection": "passed",
        "fresh_thread_reset": "passed",
        "round_trips": 2,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }, sort_keys=True))
finally:
    runner.close()
'''


def main() -> int:
    command = ["docker", "exec", CONTAINER, "python3", "-c", PROGRAM]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=240)
    if completed.returncode:
        print(json.dumps({
            "ok": False,
            "container": CONTAINER,
            "error": "live acceptance failed",
            "exit_code": completed.returncode,
        }, sort_keys=True))
        return completed.returncode
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        print(json.dumps({"ok": False, "error": "acceptance output was unreadable"}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    sys.exit(main())
