#!/usr/bin/env python3
"""BEAAA-21513 QA: mutation-test the producer's own 6 acceptance tests.

A mutant that SURVIVES (their suite still reports 6 passed) is a real coverage gap:
the defect would ship undetected by the tests named in the acceptance scope.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WT = Path(os.environ["WT"])
REL_SCRIPT = "scripts/verify_scanner_image.py"
REL_TEST = "tests/test_scanner_image_readonly_verify_beaaa21512.py"

MUTANTS = [
    ("M1 drop resolved-digest check in main()",
     '        if args.resolved_digest != args.expected_digest:\n'
     '            raise VerificationError("tag resolved to an unexpected digest")\n',
     '        if False:\n'
     '            raise VerificationError("tag resolved to an unexpected digest")\n'),

    ("M2 drop frozen-manifest SHA check in main()",
     '        if manifest_sha != args.expected_manifest_sha256:\n'
     '            raise VerificationError("frozen source release-manifest SHA-256 mismatch")\n',
     '        if False:\n'
     '            raise VerificationError("frozen source release-manifest SHA-256 mismatch")\n'),

    ("M3 drop OCI label comparison",
     '        if labels.get(key) != value:\n'
     '            raise VerificationError(f"OCI label mismatch: {key}")\n',
     '        if False:\n'
     '            raise VerificationError(f"OCI label mismatch: {key}")\n'),

    ("M4 drop artifact archive SHA check",
     '    if archive_sha != expected_archive_sha256:\n'
     '        raise VerificationError("Actions artifact archive SHA-256 mismatch")\n',
     '    if False:\n'
     '        raise VerificationError("Actions artifact archive SHA-256 mismatch")\n'),

    ("M5 drop inspect single-image shape guard",
     '    if not isinstance(payload, list) or len(payload) != 1:\n',
     '    if False:\n'),

    ("M6 drop bundled image-tar checksum comparison",
     '        if tar_sha != match.group(1):\n'
     '            raise VerificationError("bundled image checksum mismatch")\n',
     '        if False:\n'
     '            raise VerificationError("bundled image checksum mismatch")\n'),
]


def main() -> int:
    src = (WT / REL_SCRIPT).read_text(encoding="utf-8")
    rows = []
    for name, old, new in MUTANTS:
        if old not in src:
            rows.append((name, "ANCHOR-MISS", "mutation anchor not found"))
            continue
        with tempfile.TemporaryDirectory() as td:
            sandbox = Path(td) / "repo"
            shutil.copytree(WT, sandbox, symlinks=True,
                            ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
            (sandbox / REL_SCRIPT).write_text(src.replace(old, new, 1), encoding="utf-8")
            p = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", REL_TEST],
                cwd=sandbox, capture_output=True, text=True)
            tail = (p.stdout.strip().splitlines() or [""])[-1]
            killed = p.returncode != 0
            rows.append((name, "KILLED" if killed else "SURVIVED", tail))

    print("=== Mutation test of the acceptance-scope suite ===")
    survivors = 0
    for name, verdict, detail in rows:
        print(f"[{verdict:9s}] {name}\n            {detail}")
        if verdict == "SURVIVED":
            survivors += 1
    print(f"\n{len(rows) - survivors}/{len(rows)} mutants killed; {survivors} SURVIVED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
