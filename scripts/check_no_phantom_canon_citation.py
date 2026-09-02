#!/usr/bin/env python3
"""Reject new repository-relative citations to the seat-only metadata schema.

The nine historical evidence records named in ``ALLOWLIST`` are preserved as
filed. Live documents must use the ruled form ``artifact-metadata-schema.md
(seat instruction set)`` instead.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Keep the forbidden path out of this guard, or it would have to allowlist
# itself and the guard would conceal the thing it is meant to detect.
FORBIDDEN_SUBSTRING = "canon" + "/" + "artifact-metadata-schema"
CANONICAL_FORM = "artifact-metadata-schema.md (seat instruction set)"

ALLOWLIST: dict[str, str] = {
    "tools/are-scan-smoke-post-rename-bmi-to-are.md": (
        "BEAAA-510 reproducibility smoke; filed measurement record."
    ),
    "tools/calibration-deltas/2026-06-01-beaaa794-tool-misuse-activation.md": (
        "BEAAA-794 calibration delta; filed measurement record."
    ),
    "artifacts/beaaa-8127/BEAAA-8127_ops_live_fire_blocker_packet_v0.1.md": (
        "BEAAA-8127 closed-ticket evidence; frozen as filed."
    ),
    "artifacts/beaaa-12362/jadepuffer_loss_chain_coverage_inventory_v0.1.md": (
        "BEAAA-12362 closed-ticket evidence; frozen as filed."
    ),
    "artifacts/beaaa-12393/jadepuffer_chain_fixture_mapping_v0.1.md": (
        "BEAAA-12393 closed-ticket evidence; frozen as filed."
    ),
    "artifacts/BEAAA-23849/REPORT.md": (
        "BEAAA-23849 closed-ticket QA report; frozen as filed."
    ),
    "artifacts/BEAAA-23960/REPORT-R2.md": (
        "BEAAA-23960 closed-ticket evidence; frozen as filed."
    ),
    "artifacts/BEAAA-23964/QA-REPORT-R2.md": (
        "BEAAA-23964 closed-ticket QA report; frozen as filed."
    ),
    "artifacts/BEAAA-23964/REPORT.md": (
        "BEAAA-23964 closed-ticket evidence; frozen as filed."
    ),
}


def _tracked_files(repo_root: Path) -> list[Path] | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z"],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode:
        return None
    return [repo_root / name for name in result.stdout.decode().split("\0") if name]


def _walked_files(repo_root: Path) -> list[Path]:
    skipped = {".git", "__pycache__", ".venv", "venv", "node_modules", "dist"}
    return [
        path
        for path in sorted(repo_root.rglob("*"))
        if path.is_file() and not skipped.intersection(path.relative_to(repo_root).parts)
    ]


def scan(repo_root: Path) -> list[tuple[str, int, str]]:
    """Return non-allowlisted ``(path, line, text)`` hits."""
    paths = _tracked_files(repo_root) or _walked_files(repo_root)
    hits: list[tuple[str, int, str]] = []
    for path in paths:
        relative = path.relative_to(repo_root).as_posix()
        if relative in ALLOWLIST:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(content.splitlines(), 1):
            if FORBIDDEN_SUBSTRING in line:
                hits.append((relative, line_number, line.strip()))
    return hits


def stale_allowlist_entries(repo_root: Path) -> list[str]:
    stale: list[str] = []
    for relative in sorted(ALLOWLIST):
        try:
            content = (repo_root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            stale.append(relative)
            continue
        if FORBIDDEN_SUBSTRING not in content:
            stale.append(relative)
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    if not repo_root.is_dir():
        print(f"invalid repository root: {repo_root}", file=sys.stderr)
        return 2
    failed = False
    hits = scan(repo_root)
    if hits:
        print("phantom canon citation(s) found:", file=sys.stderr)
        for relative, line_number, line in hits:
            print(f"  {relative}:{line_number}: {line}", file=sys.stderr)
        print(f"Use the ruled form: {CANONICAL_FORM}", file=sys.stderr)
        failed = True
    stale = stale_allowlist_entries(repo_root)
    if stale:
        print(f"stale allowlist entries: {', '.join(stale)}", file=sys.stderr)
        failed = True
    if failed:
        return 1
    print(f"PASS — 0 phantom citations outside {len(ALLOWLIST)} frozen artifacts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
