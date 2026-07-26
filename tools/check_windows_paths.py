#!/usr/bin/env python3
"""Fail when the tracked Git tree contains Windows-invalid path components."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

INVALID_CHARACTERS = frozenset('<>:"\\|?*')
RESERVED_DEVICE_NAME = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$",
    re.IGNORECASE,
)


def invalid_reason(component: str) -> str | None:
    invalid = sorted(set(component) & INVALID_CHARACTERS)
    if invalid:
        return f"reserved character(s): {''.join(invalid)}"
    if component.endswith((" ", ".")):
        return "trailing space or dot"
    if RESERVED_DEVICE_NAME.fullmatch(component):
        return "reserved Windows device name"
    return None


def invalid_tracked_paths(paths: list[str]) -> list[tuple[str, str]]:
    failures: list[tuple[str, str]] = []
    for path in paths:
        for component in Path(path).parts:
            reason = invalid_reason(component)
            if reason:
                failures.append((path, reason))
                break
    return failures


def tracked_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return [
        path.decode("utf-8", errors="surrogateescape")
        for path in result.stdout.split(b"\0")
        if path
    ]


def main() -> int:
    failures = invalid_tracked_paths(tracked_paths())
    if not failures:
        print("PASS: tracked Git tree contains no Windows-invalid paths")
        return 0
    print("FAIL: Windows-invalid tracked paths:", file=sys.stderr)
    for path, reason in failures:
        print(f"- {path}: {reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
