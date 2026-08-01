#!/usr/bin/env python3
"""Render the immutable build identity into the Scanner release manifest."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def render(source: Path, output: Path, build_sha: str) -> None:
    if not SHA_RE.fullmatch(build_sha):
        raise SystemExit("--build-sha must be a lowercase 40-hex commit SHA")
    manifest = json.loads(source.read_text(encoding="utf-8"))
    if manifest.get("build_sha") is not None or manifest.get("release_tag") is not None:
        raise SystemExit("source manifest build_sha and release_tag must remain null templates")
    manifest["build_sha"] = build_sha
    manifest["release_tag"] = f"sha-{build_sha}"
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("release/backbond-scanner-release.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-sha", required=True)
    args = parser.parse_args()
    render(args.source, args.output, args.build_sha)


if __name__ == "__main__":
    main()
