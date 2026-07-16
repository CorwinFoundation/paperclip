#!/usr/bin/env python3
"""Fail closed when a Scanner worktree is unsafe for isolated work.

The preflight is intentionally read-only. Run ``git fetch origin --prune``
before it so the local ``origin/master`` reference represents GitHub.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence


DEFAULT_BRANCHES = frozenset({"main", "master"})


@dataclass(frozen=True)
class RepoState:
    repository: str
    branch: str
    head: str
    canonical_ref: str
    canonical_sha: str
    clean: bool
    canonical_is_ancestor: bool
    upstream: str | None = None


def evaluate(state: RepoState) -> list[str]:
    """Return blocking reasons for a proposed isolated worktree."""
    reasons: list[str] = []
    if state.branch == "HEAD":
        reasons.append("detached_head")
    if state.branch in DEFAULT_BRANCHES:
        reasons.append("direct_default_branch_work")
    if not state.clean:
        reasons.append("dirty_worktree")
    if not state.canonical_is_ancestor:
        reasons.append("branch_not_based_on_current_canonical_ref")
    return reasons


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def inspect(repo: Path, canonical_ref: str) -> RepoState:
    root = Path(_git(repo, "rev-parse", "--show-toplevel").stdout.strip())
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    canonical_sha = _git(root, "rev-parse", canonical_ref).stdout.strip()
    clean = not _git(root, "status", "--porcelain=v1").stdout.strip()
    ancestor = _git(
        root,
        "merge-base",
        "--is-ancestor",
        canonical_ref,
        "HEAD",
        check=False,
    ).returncode == 0
    upstream_result = _git(
        root,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        check=False,
    )
    upstream = upstream_result.stdout.strip() if upstream_result.returncode == 0 else None
    return RepoState(
        repository=str(root),
        branch=branch,
        head=head,
        canonical_ref=canonical_ref,
        canonical_sha=canonical_sha,
        clean=clean,
        canonical_is_ancestor=ancestor,
        upstream=upstream,
    )


def _human_output(state: RepoState, reasons: Sequence[str]) -> str:
    verdict = "PASS" if not reasons else "BLOCKED"
    lines = [
        f"version-control preflight: {verdict}",
        f"repository: {state.repository}",
        f"branch: {state.branch}",
        f"head: {state.head}",
        f"canonical: {state.canonical_ref}@{state.canonical_sha}",
        f"upstream: {state.upstream or 'none'}",
        f"clean: {str(state.clean).lower()}",
        f"canonical_is_ancestor: {str(state.canonical_is_ancestor).lower()}",
    ]
    if reasons:
        lines.append(f"blockers: {', '.join(reasons)}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--canonical-ref", default="origin/master")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    try:
        state = inspect(args.repo, args.canonical_ref)
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"version-control preflight: ERROR: {exc}", file=sys.stderr)
        return 2

    reasons = evaluate(state)
    if args.as_json:
        payload = asdict(state)
        payload.update({"safe": not reasons, "blockers": reasons})
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_human_output(state, reasons))
    return 0 if not reasons else 1


if __name__ == "__main__":
    raise SystemExit(main())
