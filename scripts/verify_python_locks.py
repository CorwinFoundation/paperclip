#!/usr/bin/env python3
"""Verify the BEAAA-17961 Python 3.11 hash locks.

Contract: are-scanner-api-v1@v1.0. This is dependency/build wiring only; it
does not alter Scanner scoring, rubric, judge, corpus, or response shape.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCKS = {
    "runtime": ROOT / "requirements-runtime.txt",
    "dev": ROOT / "requirements-dev.txt",
    "regression": ROOT / "requirements-scanner-regression.txt",
    "garak": ROOT / "requirements-eval-garak.txt",
    "pyrit": ROOT / "requirements-eval-pyrit.txt",
    "agentdojo": ROOT / "requirements-eval-agentdojo.txt",
    "inspect": ROOT / "requirements-eval-inspect.txt",
}
FRAMEWORK_DISTS = {
    "garak": "garak",
    "pyrit": "pyrit",
    "agentdojo": "agentdojo",
    "inspect": "inspect-ai",
}
REQ_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)")
HASH_RE = re.compile(r"--hash=sha256:[0-9a-f]{64}(?:\s|\\|$)")


def logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    current = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        current = f"{current} {stripped}".strip()
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        lines.append(current)
        current = ""
    if current:
        lines.append(current)
    return lines


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def main() -> int:
    errors: list[str] = []
    packages: dict[str, set[str]] = {}
    for env, path in LOCKS.items():
        if not path.is_file():
            errors.append(f"{env}: missing {path.name}")
            continue
        seen: set[str] = set()
        for line in logical_lines(path.read_text(encoding="utf-8")):
            match = REQ_RE.match(line)
            if not match:
                errors.append(f"{path.name}: unpinned/unsupported requirement: {line}")
                continue
            name = canonical(match.group(1))
            if name in seen:
                errors.append(f"{path.name}: duplicate requirement: {name}")
            seen.add(name)
            if not HASH_RE.search(line):
                errors.append(f"{path.name}: missing valid sha256 hash: {name}")
        packages[env] = seen

    for env, own_dist in FRAMEWORK_DISTS.items():
        env_packages = packages.get(env, set())
        if own_dist not in env_packages:
            errors.append(f"{env}: missing owning framework {own_dist}")
        leaked = sorted(
            dist
            for other, dist in FRAMEWORK_DISTS.items()
            if other != env and dist in env_packages
        )
        if leaked:
            errors.append(f"{env}: cross-framework leakage: {', '.join(leaked)}")

    forbidden = set(FRAMEWORK_DISTS.values())
    for env in ("runtime", "dev", "regression"):
        leaked = sorted(packages.get(env, set()) & forbidden)
        if leaked:
            errors.append(f"{env}: eval-framework leakage: {', '.join(leaked)}")

    if errors:
        print("Python lock verification FAILED:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    counts = ", ".join(f"{env}={len(packages[env])}" for env in LOCKS)
    print(f"Python lock verification OK ({counts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
