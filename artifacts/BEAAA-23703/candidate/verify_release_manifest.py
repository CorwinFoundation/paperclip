#!/usr/bin/python3
"""Recompute the pinned d9 release manifest that the host gate enforces.

Two independent routes to the same pair of numbers, so a reviewer never has to
take the pinned constants on trust:

    # from git, materialising a real checkout of the release roots
    python3 verify_release_manifest.py --from-git /path/to/are-scanner

    # from a release directory that has already been extracted on the host
    python3 verify_release_manifest.py --from-tree \\
        /opt/backbond-real-agent-demo/releases/d9d680f53db7de263f026be5284c97229126872c

Exit status is 0 only when the recomputed pair matches the pinned pair.
Reads only; creates nothing outside a temporary directory it removes.
"""

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SHA = "d9d680f53db7de263f026be5284c97229126872c"
ARCHIVE_NAME = "real-agent-demo-release.tgz"
EXPECTED_FILES = 906
EXPECTED_TREE_SHA256 = "5d6398a3cbf567778a8ca9ae7254b82a286a782dbd2874f0f711e9292bf16c17"

# The exact argument list of the workflow's `tar -czf real-agent-demo-release.tgz ...`
# step at commit d9, line 117. Read it from d9 and not from a checkout: the
# working tree packs a shorter list, which silently yields 902 files.
RELEASE_ROOTS = [
    "api",
    "harness",
    "rubric",
    "scanners",
    "release",
    "release_identity.py",
    "tools",
    "demo-real-agent",
    "tests/probe_sets",
    "artifacts/beaaa-152/datasets/halueval_dialogue_summ_general_n300.json",
    "artifacts/beaaa-18720/unified-probe-inventory.jsonl",
]


def tree_digest(root: Path) -> tuple[int, str]:
    """The digest the gate and the root helper both compute, byte for byte."""
    records: list[tuple[bytes, bytes, bytes]] = []
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(current)
        for name in list(dirs):
            path = base / name
            if path.is_symlink():
                dirs.remove(name)
                rel = path.relative_to(root).as_posix().encode()
                digest = hashlib.sha256(os.readlink(path).encode()).hexdigest().encode()
                records.append((rel, b"L", digest))
        for name in files:
            path = base / name
            rel = path.relative_to(root).as_posix()
            if rel == ARCHIVE_NAME:
                continue
            if path.is_symlink():
                kind, data = b"L", os.readlink(path).encode()
            else:
                kind, data = b"F", path.read_bytes()
            records.append((rel.encode(), kind, hashlib.sha256(data).hexdigest().encode()))
    digest = hashlib.sha256()
    for rel, kind, content_hash in sorted(records):
        digest.update(rel + b"\0" + kind + b"\0" + content_hash + b"\0")
    return len(records), digest.hexdigest()


def from_git(repo: Path) -> tuple[int, str]:
    """Materialise a real d9 checkout of the release roots, then hash it."""
    with tempfile.TemporaryDirectory(prefix="d9-release-") as scratch:
        work = Path(scratch)
        tree = work / "tree"
        tree.mkdir()
        env = {**os.environ, "GIT_INDEX_FILE": str(work / "index")}
        subprocess.run(["git", "read-tree", SHA], cwd=repo, env=env, check=True)
        names = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "-z", SHA, "--", *RELEASE_ROOTS],
            cwd=repo,
            capture_output=True,
            check=True,
        ).stdout
        subprocess.run(
            ["git", "checkout-index", "--stdin", "-f", "-z", f"--prefix={tree}/"],
            cwd=repo,
            env=env,
            input=names,
            check=True,
        )
        return tree_digest(tree)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--from-git", metavar="REPO", help="recompute from a git clone")
    group.add_argument("--from-tree", metavar="DIR", help="recompute from an extracted release")
    arguments = parser.parse_args()

    if arguments.from_git:
        count, digest = from_git(Path(arguments.from_git))
        source = f"git checkout of {SHA}"
    else:
        count, digest = tree_digest(Path(arguments.from_tree))
        source = arguments.from_tree

    print(f"source      = {source}")
    print(f"files       = {count}")
    print(f"tree_sha256 = {digest}")
    if count == EXPECTED_FILES and digest == EXPECTED_TREE_SHA256:
        print(f"manifest=PASS sha={SHA}")
        return 0
    print(
        f"manifest=FAIL expected files={EXPECTED_FILES} tree_sha256={EXPECTED_TREE_SHA256}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
