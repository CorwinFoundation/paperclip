#!/usr/bin/env python3
"""Validate canonical release identity and emit auditable evidence JSON."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from release_identity import ReleaseManifestError, release_identity_evidence


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
    ).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-sha", default=os.getenv("GITHUB_SHA"))
    parser.add_argument("--tag", default=os.getenv("BACKBOND_RELEASE_TAG"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    build_sha = args.build_sha or _git_sha()
    try:
        evidence = release_identity_evidence(REPO, build_sha=build_sha, tag=args.tag)
    except (OSError, ReleaseManifestError) as exc:
        print(f"release identity FAILED: {exc}", file=sys.stderr)
        return 1
    rendered = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
