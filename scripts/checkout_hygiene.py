#!/usr/bin/env python3
"""Inventory and safely retire merged Scanner worktrees (BEAAA-15613).

Dry-run is the default.  ``--apply`` archives every selected worktree, verifies
the archive checksum, and only then asks Git to remove it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

TICKET = re.compile(r"(?i)beaaa[-_/ ]?(\d+)")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


def ticket_for(path: str, branch: str) -> str | None:
    match = TICKET.search(f"{path} {branch}")
    return f"BEAAA-{match.group(1)}" if match else None


@dataclass(frozen=True)
class Checkout:
    path: str
    head: str
    branch: str
    ticket: str | None
    clean: bool
    merged: bool
    current: bool


def inventory(repo: Path, base: str) -> list[Checkout]:
    records: list[dict[str, str]] = []
    record: dict[str, str] = {}
    for line in git(repo, "worktree", "list", "--porcelain").splitlines() + [""]:
        if not line:
            if record:
                records.append(record)
                record = {}
            continue
        key, _, value = line.partition(" ")
        record[key] = value

    current = str(repo.resolve())
    result = []
    for row in records:
        path = Path(row["worktree"])
        branch = row.get("branch", "detached").removeprefix("refs/heads/")
        clean = not git(path, "status", "--porcelain=v1", "--untracked-files=all")
        merged = subprocess.run(
            ["git", "-C", str(path), "merge-base", "--is-ancestor", row["HEAD"], base],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
        result.append(Checkout(str(path), row["HEAD"], branch, ticket_for(str(path), branch), clean, merged, str(path.resolve()) == current))
    return result


def select_retirements(checkouts: list[Checkout], maximum: int) -> list[Checkout]:
    needed = max(0, len(checkouts) - maximum)
    eligible = [c for c in checkouts if not c.current and c.clean and c.merged]
    # Retire unowned leftovers first, then oldest stable path order. Any survivor
    # other than the canonical checkout must remain ticket-owned.
    eligible.sort(key=lambda c: (c.ticket is not None, c.path.casefold()))
    selected = eligible[:needed]
    survivors = [c for c in checkouts if c not in selected]
    if len(survivors) > maximum:
        raise RuntimeError(f"cannot reach maximum {maximum}: dirty or unmerged worktrees require review")
    unowned = [c.path for c in survivors if not c.current and not c.ticket]
    if unowned:
        raise RuntimeError("unowned survivors require a BEAAA ticket: " + ", ".join(unowned))
    return selected


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_then_remove(repo: Path, checkout: Checkout, archive_dir: Path) -> dict[str, str]:
    source = Path(checkout.path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = re.sub(r"[^a-zA-Z0-9_.-]+", "-", source.name)
    archive = archive_dir / f"scanner-{stem}-{checkout.head[:12]}-{stamp}.tar.gz"
    archive_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        bundle = Path(temp) / "repository.bundle"
        git(source, "bundle", "create", str(bundle), "--all")
        with tarfile.open(archive, "w:gz") as output:
            output.add(source, arcname="worktree")
            output.add(bundle, arcname="repository.bundle")
    expected = sha256(archive)
    if sha256(archive) != expected:
        raise RuntimeError(f"checksum verification failed: {archive}")
    git(repo, "worktree", "remove", checkout.path)
    return {"archive": archive.name, "sha256": expected, "checkout": checkout.path}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", default="origin/master")
    parser.add_argument("--max-survivors", type=int, default=5)
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    checkouts = inventory(args.repo.resolve(), args.base)
    selected = select_retirements(checkouts, args.max_survivors)
    receipts = [archive_then_remove(args.repo, row, args.archive_dir) for row in selected] if args.apply else []
    payload = {"mode": "apply" if args.apply else "dry-run", "maximum": args.max_survivors,
               "inventory": [asdict(row) for row in checkouts], "selected": [row.path for row in selected],
               "receipts": receipts}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
