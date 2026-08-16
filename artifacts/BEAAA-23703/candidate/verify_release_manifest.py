#!/usr/bin/python3
"""Recompute the pinned d9 release tree digest two independent ways.

    verify_release_manifest.py git <repo> <commit>   # from tracked blobs
    verify_release_manifest.py tree <directory>      # from an extracted release

Both print "<files> <tree_sha256>". The algorithm is the one
backbond-real-agent-demo-ssh-gate.tree_digest() applies: one record per entry,
"<relative path>\\0<F|L>\\0<sha256 of content or link target>\\0", sorted, then
sha256 of the concatenation. The uploaded archive itself is excluded.

The two derivations agreeing is what proves the gate's pin describes exactly
the d9 tree and nothing else.
"""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ARCHIVE_NAME = "real-agent-demo-release.tgz"

# The exact path list the deploy job of .github/workflows/real-agent-demo.yml
# hands to tar at d9d680f53db7de263f026be5284c97229126872c.
RELEASE_PATHS = [
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


def finalise(records):
    digest = hashlib.sha256()
    for rel, kind, content_hash in sorted(records):
        digest.update(rel + b"\0" + kind + b"\0" + content_hash + b"\0")
    return len(records), digest.hexdigest()


def from_tree(root):
    root = Path(root)
    records = []
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(current)
        for name in list(dirs):
            path = base / name
            if path.is_symlink():
                dirs.remove(name)
                records.append(
                    (
                        path.relative_to(root).as_posix().encode(),
                        b"L",
                        hashlib.sha256(os.readlink(path).encode()).hexdigest().encode(),
                    )
                )
        for name in files:
            path = base / name
            rel = path.relative_to(root).as_posix()
            if rel == ARCHIVE_NAME:
                continue
            if path.is_symlink():
                kind, data = b"L", os.readlink(path).encode()
            elif path.is_file():
                kind, data = b"F", path.read_bytes()
            else:
                raise SystemExit("unsupported release entry type: %s" % rel)
            records.append((rel.encode(), kind, hashlib.sha256(data).hexdigest().encode()))
    return finalise(records)


def from_git(repo, commit):
    listing = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "-z", commit, "--"] + RELEASE_PATHS,
        capture_output=True,
        check=True,
    ).stdout
    records = []
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        meta, path = entry.split(b"\t", 1)
        mode, kind, oid = meta.split()
        if kind != b"blob":
            raise SystemExit("unexpected object kind in the release subset: %s" % kind.decode())
        blob = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "blob", oid.decode()],
            capture_output=True,
            check=True,
        ).stdout
        records.append(
            (path, b"L" if mode == b"120000" else b"F", hashlib.sha256(blob).hexdigest().encode())
        )
    return finalise(records)


def main(argv):
    if len(argv) == 4 and argv[1] == "git":
        count, digest = from_git(argv[2], argv[3])
    elif len(argv) == 3 and argv[1] == "tree":
        count, digest = from_tree(argv[2])
    else:
        raise SystemExit(__doc__)
    print("%d %s" % (count, digest))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
