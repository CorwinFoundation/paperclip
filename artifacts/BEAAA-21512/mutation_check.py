#!/usr/bin/env python3
"""BEAAA-21512 mutation check — reproduces the BEAAA-21513 D2/D4/D5 defect set.

Run from the repository root:

    python3 artifacts/BEAAA-21512/mutation_check.py

Each mutant deletes or widens exactly one load-bearing branch and re-runs
tests/test_scanner_image_readonly_verify_beaaa21512.py. Every mutant must be
KILLED; the script exits non-zero if any survives.
"""

import subprocess, sys
from pathlib import Path

SRC = Path("scripts/verify_scanner_image.py")
WF = Path(".github/workflows/scanner-image-readonly-verify.yml")

SCRIPT_MUTANTS = {
    "M1 drop resolved-digest check in main()": (
        '        if args.resolved_digest != args.expected_digest:\n'
        '            raise VerificationError("tag resolved to an unexpected digest")\n', ""),
    "M2 drop frozen-manifest SHA check in main()": (
        '        if manifest_sha != args.expected_manifest_sha256:\n'
        '            raise VerificationError("frozen source release-manifest SHA-256 mismatch")\n', ""),
    "M3 drop OCI label comparison": (
        '    for key, value in expected.items():\n'
        '        if labels.get(key) != value:\n'
        '            raise VerificationError(f"OCI label mismatch: {key}")\n', ""),
    "M4 drop artifact archive SHA check": (
        '    if archive_sha != expected_archive_sha256:\n'
        '        raise VerificationError("Actions artifact archive SHA-256 mismatch")\n', ""),
    "M5 drop inspect single-image shape guard": (
        '    if not isinstance(payload, list) or len(payload) != 1:\n'
        '        raise VerificationError("image inspect payload must contain exactly one image")\n', ""),
    "M6 drop bundled image-tar checksum comparison": (
        '        if tar_sha != match.group(1):\n'
        '            raise VerificationError("bundled image checksum mismatch")\n', ""),
}

WORKFLOW_MUTANTS = {
    "W1 widen a top-level scope to write": ("  contents: read\n", "  contents: write\n"),
    "W2 add a job-level scope override": (
        "    runs-on: ubuntu-latest\n",
        "    runs-on: ubuntu-latest\n    permissions:\n      contents: read\n"),
    "W3 revert an action to a mutable tag": (
        "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2",
        "actions/checkout@v6"),
    "W4 introduce a repository secret": (
        "password: ${{ github.token }}", "password: ${{ secrets.GHCR_PAT }}"),
    "W5 drop the CTO-ruled actions:read scope": ("  actions: read\n", ""),
}

SUITE = "tests/test_scanner_image_readonly_verify_beaaa21512.py"


def run(target: Path, mutants: dict) -> int:
    original = target.read_text()
    survivors = 0
    try:
        for name, (old, new) in mutants.items():
            if old not in original:
                raise SystemExit(f"mutation pattern not found for {name}")
            target.write_text(original.replace(old, new, 1))
            proc = subprocess.run([sys.executable, "-m", "pytest", "-q", SUITE],
                                  capture_output=True, text=True)
            tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
            verdict = "KILLED" if proc.returncode != 0 else "SURVIVED"
            survivors += verdict == "SURVIVED"
            print(f"{verdict:9} | {name} | {tail}")
    finally:
        target.write_text(original)
    return survivors


if __name__ == "__main__":
    total = run(SRC, SCRIPT_MUTANTS) + run(WF, WORKFLOW_MUTANTS)
    print(f"survivors: {total}")
    raise SystemExit(1 if total else 0)
