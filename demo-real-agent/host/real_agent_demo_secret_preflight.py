#!/usr/bin/python3
"""Validate the fixed passive-demo secret file without disclosing values."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


ENV_PATH = Path("/etc/backbond/real-agent-demo.env")
EXPECTED_UID = 10001
EXPECTED_GID = 10001
EXPECTED_MODE = 0o600


class PreflightError(Exception):
    pass


def _fail(status: str) -> None:
    raise PreflightError(status)


def _read_fixed_file(path: Path = ENV_PATH) -> str:
    try:
        before = path.lstat()
    except FileNotFoundError:
        _fail("secret_file: missing")
    if not stat.S_ISREG(before.st_mode):
        _fail("secret_file: not_regular")
    if (before.st_uid, before.st_gid) != (EXPECTED_UID, EXPECTED_GID):
        _fail("secret_file: wrong_owner")
    if stat.S_IMODE(before.st_mode) != EXPECTED_MODE:
        _fail("secret_file: wrong_mode")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        _fail("secret_file: open_rejected")
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            _fail("secret_file: opened_not_regular")
        if (opened.st_uid, opened.st_gid) != (EXPECTED_UID, EXPECTED_GID):
            _fail("secret_file: opened_wrong_owner")
        if stat.S_IMODE(opened.st_mode) != EXPECTED_MODE:
            _fail("secret_file: opened_wrong_mode")
        if (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            _fail("secret_file: changed_during_check")
        with os.fdopen(fd, "r", encoding="utf-8", errors="strict") as handle:
            fd = -1
            return handle.read()
    finally:
        if fd >= 0:
            os.close(fd)


def _parse_names(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name:
            values[name] = value.strip()
    return values


def validate(path: Path = ENV_PATH) -> list[str]:
    values = _parse_names(_read_fixed_file(path))
    configured: list[str] = []
    for name in ("DEMO_ACCESS_TOKEN", "ARE_BUNDLE_SIGNING_KEY"):
        if not values.get(name):
            _fail(f"{name}: missing_or_empty")
        configured.append(name)

    if values.get("DEMO_JUDGE_BACKEND") == "claude-code-proxy":
        if not values.get("CLAUDE_JUDGE_PROXY_TOKEN"):
            _fail("CLAUDE_JUDGE_PROXY_TOKEN: missing_or_empty")
        configured.extend(("DEMO_JUDGE_BACKEND", "CLAUDE_JUDGE_PROXY_TOKEN"))
    else:
        api_names = [
            name
            for name in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY")
            if values.get(name)
        ]
        if not api_names:
            _fail("judge_configuration: missing_or_invalid")
        configured.extend(api_names)
    return configured


def main(argv: list[str]) -> int:
    if argv:
        print("arguments: rejected", file=sys.stderr)
        return 2
    try:
        configured = validate()
    except (PreflightError, UnicodeError) as exc:
        print(str(exc) if isinstance(exc, PreflightError) else "secret_file: invalid_encoding", file=sys.stderr)
        return 1
    print("secret_file: valid")
    for name in configured:
        print(f"{name}: configured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
